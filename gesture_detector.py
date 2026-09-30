"""
gesture_detector.py
====================
Converts raw hand landmarks (from hand_tracker.py) into high-level,
debounced gesture events for ALPHA Air Mouse.

Stability measures (Priority 1):
  1. Landmark smoothing via a One-Euro filter (utils.OneEuroFilter) --
     adapts smoothing strength to hand speed instead of a fixed alpha.
  2. Hand-lost grace period -- a momentary MediaPipe miss (motion blur,
     fast move, brief occlusion) does NOT immediately report "no hand";
     the last known pose is held for HAND_LOST_GRACE_SEC before the hand
     is truly considered lost. This is the main fix for "loses the hand
     constantly."
  3. Pose stability time-gate -- a pose must be held briefly before any
     discrete action (click/zoom/copy/paste) is allowed to fire.
  4. Distance hysteresis -- separate touch/release thresholds so a pinch
     sitting near the boundary doesn't flicker.
  5. Cooldown / debounce + single-shot "arming" latches -- one gesture
     produces exactly one action for single-fire gestures.

Gesture-table ambiguity, resolved and documented:
  "Pinching" (Zoom In) and "Thumb touching Index" (Left Click) are the
  literal same physical pose (thumb tip <-> index tip touching). This is
  resolved with a tap-vs-hold split, identical to how every touchscreen
  disambiguates tap vs. long-press:
      - released before PINCH_HOLD_TO_ZOOM_SEC  -> Left Click (once)
      - still held past PINCH_HOLD_TO_ZOOM_SEC   -> Zoom In (repeats)
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import config
from hand_tracker import (
    HandResult, WRIST, THUMB_TIP, INDEX_TIP, INDEX_MCP, INDEX_PIP,
    MIDDLE_TIP, MIDDLE_MCP, MIDDLE_PIP, RING_TIP, RING_PIP,
    PINKY_TIP, PINKY_PIP,
)
from utils import setup_logger, distance, Debouncer, PointOneEuroFilter

logger = setup_logger(__name__)


@dataclass
class GestureState:
    """Everything downstream consumers (main.py, UI, controllers) need for one frame."""
    hand_detected: bool = False
    enabled: bool = True
    gesture_name: str = "None"
    tracking_confidence: float = 0.0
    cursor_point: Optional[Tuple[float, float]] = None
    cursor_gain_multiplier: float = 1.0
    actions: List[str] = field(default_factory=list)


class _SingleShotLatch:
    """Fires exactly once when a condition becomes true; re-arms only once
    the condition has gone false again."""

    def __init__(self):
        self.armed = True

    def try_fire(self, condition: bool) -> bool:
        if condition and self.armed:
            self.armed = False
            return True
        if not condition:
            self.armed = True
        return False


class GestureDetector:
    """Stateful gesture classifier. Call `update(hand_result)` once per frame."""

    def __init__(self):
        self.enabled = True

        # One-Euro landmark filters (lazy dict keyed by landmark index)
        self._filters = {}

        # Debouncers / cooldowns
        self._zoom_in_gate = Debouncer(config.ZOOM_REPEAT_INTERVAL_SEC)
        self._zoom_out_gate = Debouncer(config.ZOOM_REPEAT_INTERVAL_SEC)
        self._scroll_gate = Debouncer(config.SCROLL_REPEAT_INTERVAL_SEC)

        # Single-shot latches for single-fire gestures
        self._left_click_latch = _SingleShotLatch()
        self._right_click_latch = _SingleShotLatch()
        self._copy_latch = _SingleShotLatch()
        self._paste_latch = _SingleShotLatch()

        # Pose stability tracking
        self._current_pose = None
        self._pose_start_time = 0.0

        # Thumb-Index tap-vs-hold tracking
        self._pinch_start_time = None

        # Motion tracking (static-vs-moving disambiguation for the fist pose)
        self._prev_center = None
        self._prev_center_time = 0.0
        self._last_velocity = 0.0

        # Scroll gesture vertical origin
        self._scroll_origin_y = None

        # Hand-lost grace period bookkeeping
        self._last_seen_landmarks = None
        self._last_seen_time = 0.0

    # ------------------------------------------------------------------
    # Landmark smoothing
    # ------------------------------------------------------------------
    def _smooth(self, idx: int, point: Tuple[float, float], t: float) -> Tuple[float, float]:
        f = self._filters.get(idx)
        if f is None:
            f = PointOneEuroFilter(
                freq=30.0,
                min_cutoff=config.LANDMARK_ONEEURO_MIN_CUTOFF,
                beta=config.LANDMARK_ONEEURO_BETA,
                d_cutoff=config.LANDMARK_ONEEURO_DCUTOFF,
            )
            self._filters[idx] = f
        return f.filter(point, t)

    def _reset_filters(self):
        self._filters.clear()

    # ------------------------------------------------------------------
    # Finger state helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _finger_up(landmarks, tip_idx, pip_idx, margin=0.02) -> bool:
        return (landmarks[pip_idx][1] - landmarks[tip_idx][1]) > margin

    @staticmethod
    def _thumb_extended(landmarks) -> bool:
        hand_size = max(distance(landmarks[WRIST], landmarks[MIDDLE_MCP]), 1e-6)
        thumb_reach = distance(landmarks[THUMB_TIP], landmarks[INDEX_MCP])
        return thumb_reach > 0.55 * hand_size

    def _classify_fingers(self, landmarks):
        return {
            "thumb": self._thumb_extended(landmarks),
            "index": self._finger_up(landmarks, INDEX_TIP, INDEX_PIP),
            "middle": self._finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP),
            "ring": self._finger_up(landmarks, RING_TIP, RING_PIP),
            "pinky": self._finger_up(landmarks, PINKY_TIP, PINKY_PIP),
        }

    # ------------------------------------------------------------------
    # Pose stability gate
    # ------------------------------------------------------------------
    def _pose_stable_for(self, pose_name: str) -> float:
        now = time.time()
        if pose_name != self._current_pose:
            self._current_pose = pose_name
            self._pose_start_time = now
            return 0.0
        return now - self._pose_start_time

    # ------------------------------------------------------------------
    # Hand-lost grace period
    # ------------------------------------------------------------------
    def _resolve_hand_result(self, hand_result: HandResult) -> HandResult:
        """Applies the hand-lost grace period: a real detection updates
        the "last seen" cache; a miss reuses that cache (marked stale)
        until HAND_LOST_GRACE_SEC elapses, after which it's truly lost."""
        now = time.time()

        if hand_result.detected:
            self._last_seen_landmarks = hand_result.landmarks
            self._last_seen_time = now
            return hand_result

        if self._last_seen_landmarks is not None and (now - self._last_seen_time) < config.HAND_LOST_GRACE_SEC:
            return HandResult(
                detected=True, landmarks=self._last_seen_landmarks,
                handedness=None, confidence=0.0, stale=True,
            )

        # Truly lost.
        self._last_seen_landmarks = None
        return hand_result

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def update(self, hand_result: HandResult) -> GestureState:
        state = GestureState(enabled=self.enabled)

        hand_result = self._resolve_hand_result(hand_result)

        if not hand_result.detected:
            self._current_pose = None
            self._prev_center = None
            self._scroll_origin_y = None
            self._pinch_start_time = None
            self._reset_filters()
            state.hand_detected = False
            state.gesture_name = "No Hand"
            return state

        state.hand_detected = True
        state.tracking_confidence = hand_result.confidence

        now = time.time()
        raw = hand_result.landmarks
        landmarks = [self._smooth(i, p, now) for i, p in enumerate(raw)]

        if not self.enabled:
            state.gesture_name = "Disabled"
            return state

        fingers = self._classify_fingers(landmarks)

        # Hand center + velocity (disambiguates the static fist/zoom-out pose)
        center = (
            (landmarks[WRIST][0] + landmarks[INDEX_MCP][0] + landmarks[MIDDLE_MCP][0]) / 3.0,
            (landmarks[WRIST][1] + landmarks[INDEX_MCP][1] + landmarks[MIDDLE_MCP][1]) / 3.0,
        )
        velocity = 0.0
        if self._prev_center is not None:
            dt = max(now - self._prev_center_time, 1e-3)
            velocity = distance(center, self._prev_center) / dt
        self._prev_center = center
        self._prev_center_time = now
        self._last_velocity = velocity

        pose_name = self._resolve_pose(fingers, landmarks)
        stable_time = self._pose_stable_for(pose_name)
        is_stable = stable_time >= config.GESTURE_STABLE_TIME_SEC

        # --- Thumb-Index: tap (Left Click) vs hold (Zoom In) ----------------
        if pose_name == "pinch_index":
            if self._pinch_start_time is None:
                self._pinch_start_time = now
            held_for = now - self._pinch_start_time

            if held_for >= config.PINCH_HOLD_TO_ZOOM_SEC:
                if self._zoom_in_gate.ready():
                    self._zoom_in_gate.fire()
                    state.actions.append("zoom_in")
                state.gesture_name = "Pinch - Zoom In"
            else:
                state.gesture_name = "Pinch"
            self._left_click_latch.armed = True  # suppress click until release
            return state
        else:
            if self._pinch_start_time is not None:
                held_for = now - self._pinch_start_time
                if held_for < config.PINCH_HOLD_TO_ZOOM_SEC and self._left_click_latch.try_fire(True):
                    state.actions.append("click_left")
                    state.gesture_name = "Left Click"
                    self._pinch_start_time = None
                    return state
            self._pinch_start_time = None
            self._left_click_latch.armed = True

        # --- Closed fist, stationary -> Zoom Out (repeats while held) ------
        if pose_name == "fist" and is_stable:
            if self._zoom_out_gate.ready():
                self._zoom_out_gate.fire()
                state.actions.append("zoom_out")
            state.gesture_name = "Fist - Zoom Out"
            return state

        # --- Thumb-Middle -> Right Click (single shot) ----------------------
        if pose_name == "pinch_middle":
            if is_stable and self._right_click_latch.try_fire(True):
                state.actions.append("click_right")
            state.gesture_name = "Right Click"
            return state
        else:
            self._right_click_latch.armed = True

        # --- Thumb-Ring -> Copy (Ctrl+C, single shot) ------------------------
        if pose_name == "pinch_ring":
            if is_stable and self._copy_latch.try_fire(True):
                state.actions.append("copy")
            state.gesture_name = "Copy"
            return state
        else:
            self._copy_latch.armed = True

        # --- Thumb-Pinky -> Paste (Ctrl+V, single shot) ----------------------
        if pose_name == "pinch_pinky":
            if is_stable and self._paste_latch.try_fire(True):
                state.actions.append("paste")
            state.gesture_name = "Paste"
            return state
        else:
            self._paste_latch.armed = True

        # --- Index + Middle together, moving vertically -> Scroll -----------
        if pose_name == "scroll":
            self._handle_scroll(landmarks, state)
            state.gesture_name = "Scroll"
            return state
        else:
            self._scroll_origin_y = None

        # --- Index only -> Precision Cursor Mode -----------------------------
        if pose_name == "index_only":
            state.cursor_point = landmarks[INDEX_TIP]
            state.cursor_gain_multiplier = config.PRECISION_GAIN_MULTIPLIER
            state.gesture_name = "Precision Cursor"
            return state

        # --- Default: open hand moving -> normal cursor movement ------------
        state.cursor_point = center
        state.cursor_gain_multiplier = 1.0
        state.gesture_name = "Cursor" if pose_name != "fist_moving" else "Cursor"
        return state

    # ------------------------------------------------------------------
    # Pose resolution (pure classification, no side effects)
    # ------------------------------------------------------------------
    def _resolve_pose(self, fingers, landmarks) -> str:
        thumb_index_d = distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
        thumb_middle_d = distance(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP])
        thumb_ring_d = distance(landmarks[THUMB_TIP], landmarks[RING_TIP])
        thumb_pinky_d = distance(landmarks[THUMB_TIP], landmarks[PINKY_TIP])
        index_middle_d = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP])

        all_closed = not any(fingers.values())
        is_moving = self._last_velocity > config.MOTION_VELOCITY_THRESHOLD

        if thumb_index_d < config.PINCH_TOUCH_DISTANCE:
            return "pinch_index"
        if thumb_middle_d < config.PINCH_TOUCH_DISTANCE:
            return "pinch_middle"
        if thumb_ring_d < config.PINCH_TOUCH_DISTANCE:
            return "pinch_ring"
        if thumb_pinky_d < config.PINCH_TOUCH_DISTANCE:
            return "pinch_pinky"
        if all_closed:
            return "fist" if not is_moving else "fist_moving"
        if fingers["index"] and fingers["middle"] and not fingers["ring"] and not fingers["pinky"]:
            if index_middle_d < config.FINGER_TOGETHER_DISTANCE:
                return "scroll"
        if fingers["index"] and not fingers["middle"] and not fingers["ring"] and not fingers["pinky"]:
            return "index_only"
        return "open_hand"

    # ------------------------------------------------------------------
    # Scroll handling
    # ------------------------------------------------------------------
    def _handle_scroll(self, landmarks, state: GestureState):
        current_y = (landmarks[INDEX_TIP][1] + landmarks[MIDDLE_TIP][1]) / 2.0

        if self._scroll_origin_y is None:
            self._scroll_origin_y = current_y
            return

        delta = current_y - self._scroll_origin_y

        if abs(delta) >= config.SCROLL_TRIGGER_DELTA and self._scroll_gate.ready():
            self._scroll_gate.fire()
            if delta > 0:
                state.actions.append("scroll_down")
            else:
                state.actions.append("scroll_up")
            self._scroll_origin_y = current_y  # ratchet: reset origin after each tick

    def reset(self):
        """Full state reset (e.g. after camera reconnect / Start pressed again)."""
        self._reset_filters()
        self._current_pose = None
        self._prev_center = None
        self._scroll_origin_y = None
        self._pinch_start_time = None
        self._last_seen_landmarks = None
