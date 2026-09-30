"""
hand_tracker.py
================
Thin wrapper around MediaPipe Hands.

Isolating MediaPipe here means the rest of the app (gesture_detector.py,
mouse_controller.py, ui code) never touches the mediapipe API directly --
if the tracking backend ever needs to change, only this file is affected.

NOTE: MediaPipe's Python distribution currently lags behind the newest
CPython releases. See README.md for the recommended interpreter version.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import mediapipe as mp

import config
from utils import setup_logger

logger = setup_logger(__name__)

# Landmark index constants (MediaPipe Hands topology, 21 points)
WRIST = 0
THUMB_TIP = 4
THUMB_IP = 3
INDEX_TIP = 8
INDEX_PIP = 6
INDEX_MCP = 5
MIDDLE_TIP = 12
MIDDLE_PIP = 10
MIDDLE_MCP = 9
RING_TIP = 16
RING_PIP = 14
PINKY_TIP = 20
PINKY_PIP = 18


@dataclass
class HandResult:
    """Simplified, per-frame result of hand tracking.

    landmarks: list of 21 (x, y) tuples in normalized [0, 1] image space.
    handedness: "Left" or "Right" as reported by MediaPipe (mirrored frame
                means this corresponds to the user's perceived hand).
    confidence: MediaPipe's handedness classification score (0-1), used
                as a practical proxy for "how confident is the tracker
                right now" in the UI.
    stale: True when this result is being reused from a recent previous
           frame during the hand-lost grace period (see gesture_detector.py),
           rather than a fresh detection this frame.
    """
    detected: bool = False
    landmarks: List[Tuple[float, float]] = field(default_factory=list)
    handedness: Optional[str] = None
    confidence: float = 0.0
    stale: bool = False

    def get(self, index: int) -> Optional[Tuple[float, float]]:
        if not self.detected or index >= len(self.landmarks):
            return None
        return self.landmarks[index]


class HandTracker:
    """Wraps `mediapipe.solutions.hands.Hands` with a simple process() API."""

    def __init__(self):
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=config.MAX_NUM_HANDS,
            model_complexity=config.MODEL_COMPLEXITY,
            min_detection_confidence=config.MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=config.MIN_TRACKING_CONFIDENCE,
        )
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_styles = mp.solutions.drawing_styles
        logger.info("MediaPipe Hands initialized (model_complexity=%s).", config.MODEL_COMPLEXITY)

    def process(self, frame_bgr) -> HandResult:
        """Run hand detection on a single BGR frame (as returned by OpenCV).

        Returns a HandResult; on any internal failure, returns a "not
        detected" result rather than raising, so the caller's loop never
        crashes because of a single bad frame.
        """
        try:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            results = self._hands.process(frame_rgb)

            if not results.multi_hand_landmarks:
                return HandResult(detected=False)

            hand_landmarks = results.multi_hand_landmarks[0]
            points = [(lm.x, lm.y) for lm in hand_landmarks.landmark]

            handedness_label = None
            confidence = 0.0
            if results.multi_handedness:
                classification = results.multi_handedness[0].classification[0]
                handedness_label = classification.label
                confidence = float(classification.score)

            return HandResult(
                detected=True, landmarks=points,
                handedness=handedness_label, confidence=confidence,
            )
        except Exception:
            logger.exception("Error during hand landmark processing.")
            return HandResult(detected=False)

    def draw_landmarks(self, frame_bgr, hand_result: HandResult):
        """Draw landmark skeleton onto the frame in-place (for the preview UI only).

        Kept optional / cheap: only invoked when the settings window preview
        is actually visible.
        """
        if not hand_result.detected:
            return frame_bgr

        h, w = frame_bgr.shape[:2]
        for (x, y) in hand_result.landmarks:
            cx, cy = int(x * w), int(y * h)
            cv2.circle(frame_bgr, (cx, cy), 3, (76, 175, 80), -1)

        connections = self._mp_hands.HAND_CONNECTIONS
        for (start_idx, end_idx) in connections:
            if start_idx < len(hand_result.landmarks) and end_idx < len(hand_result.landmarks):
                x1, y1 = hand_result.landmarks[start_idx]
                x2, y2 = hand_result.landmarks[end_idx]
                p1 = (int(x1 * w), int(y1 * h))
                p2 = (int(x2 * w), int(y2 * h))
                cv2.line(frame_bgr, p1, p2, (200, 200, 200), 1)

        return frame_bgr

    def close(self):
        try:
            self._hands.close()
        except Exception:
            logger.exception("Error closing MediaPipe Hands.")
