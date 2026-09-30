"""
mouse_controller.py
====================
Translates hand landmark positions into actual OS mouse movement and
click/keyboard events.

Cursor movement model (Priority 2 - sensitivity/responsiveness):
    Movement is RELATIVE, not absolute -- exactly like a real mouse. Each
    frame we compute how far the (filtered) hand point moved since last
    frame, then move the OS cursor by that delta multiplied by a
    speed-dependent gain (see config.ACCEL_*). This is a standard mouse
    pointer acceleration curve: slow/deliberate hand movement stays
    precise, fast hand movement covers the screen quickly -- "feels like
    a real mouse" rather than a slow 1:1 webcam-frame mapping.

    A One-Euro filter (utils.OneEuroFilter) smooths the final on-screen
    position so the accel curve doesn't amplify jitter along with speed.

Uses `pyautogui` for clicks and `pynput` for wheel/keyboard events (Ctrl
held during scroll for zoom, Ctrl+C / Ctrl+V for copy/paste).
"""

import ctypes

import pyautogui
from pynput.mouse import Controller as PynputMouse
from pynput.mouse import Button
from pynput.keyboard import Controller as PynputKeyboard
from pynput.keyboard import Key

import config
from utils import setup_logger, clamp, OneEuroFilter

logger = setup_logger(__name__)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = config.PYAUTOGUI_PAUSE


def _get_screen_size():
    """True Windows screen resolution via ctypes; falls back to pyautogui
    if unavailable (e.g. non-Windows dev/test environment)."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return pyautogui.size()


class MouseController:
    """Owns all cursor movement, click, zoom, copy, and paste output actions."""

    def __init__(self):
        self.screen_w, self.screen_h = _get_screen_size()
        logger.info("Detected screen resolution: %sx%s", self.screen_w, self.screen_h)

        self._mouse = PynputMouse()
        self._keyboard = PynputKeyboard()

        self._filter_x = OneEuroFilter(
            freq=30.0, min_cutoff=config.CURSOR_ONEEURO_MIN_CUTOFF,
            beta=config.CURSOR_ONEEURO_BETA, d_cutoff=config.CURSOR_ONEEURO_DCUTOFF,
        )
        self._filter_y = OneEuroFilter(
            freq=30.0, min_cutoff=config.CURSOR_ONEEURO_MIN_CUTOFF,
            beta=config.CURSOR_ONEEURO_BETA, d_cutoff=config.CURSOR_ONEEURO_DCUTOFF,
        )

        self._prev_point = None      # last filtered (x, y) normalized hand point
        self._prev_time = None
        self._accum_x = 0.0          # sub-pixel remainder accumulator
        self._accum_y = 0.0

    # ------------------------------------------------------------------
    # Cursor movement
    # ------------------------------------------------------------------
    def move_relative(self, point, timestamp, gain_multiplier: float = 1.0):
        """Move the OS cursor based on a normalized (0-1, 0-1) hand point,
        using relative/delta movement with speed-adaptive acceleration.
        """
        if point is None:
            return

        fx = self._filter_x(point[0], timestamp)
        fy = self._filter_y(point[1], timestamp)
        filtered = (fx, fy)

        if self._prev_point is None or self._prev_time is None:
            self._prev_point = filtered
            self._prev_time = timestamp
            return

        dt = max(timestamp - self._prev_time, 1e-3)
        dx = filtered[0] - self._prev_point[0]
        dy = filtered[1] - self._prev_point[1]
        speed = ((dx * dx + dy * dy) ** 0.5) / dt

        self._prev_point = filtered
        self._prev_time = timestamp

        # Speed-adaptive acceleration gain (like OS mouse pointer acceleration).
        speed_ratio = clamp(speed / config.ACCEL_VELOCITY_REF, 0.0, 1.0)
        gain = config.ACCEL_MIN_GAIN + speed_ratio * (config.ACCEL_MAX_GAIN - config.ACCEL_MIN_GAIN)
        gain *= gain_multiplier

        move_x = dx * config.CURSOR_BASE_SENSITIVITY * gain + self._accum_x
        move_y = dy * config.CURSOR_BASE_SENSITIVITY * gain + self._accum_y

        int_x, int_y = int(move_x), int(move_y)
        self._accum_x = move_x - int_x  # keep sub-pixel remainder for next frame
        self._accum_y = move_y - int_y

        if abs(int_x) < config.CURSOR_MIN_MOVE_PX and abs(int_y) < config.CURSOR_MIN_MOVE_PX:
            return

        try:
            cur_x, cur_y = self._mouse.position
            new_x = int(clamp(cur_x + int_x, 0, self.screen_w - 1))
            new_y = int(clamp(cur_y + int_y, 0, self.screen_h - 1))
            self._mouse.position = (new_x, new_y)
        except Exception:
            logger.exception("Failed to move cursor.")

    def reset_smoothing(self):
        """Call when the hand is lost/re-acquired to avoid a big cursor jump."""
        self._filter_x.reset()
        self._filter_y.reset()
        self._prev_point = None
        self._prev_time = None
        self._accum_x = 0.0
        self._accum_y = 0.0

    # ------------------------------------------------------------------
    # Clicks
    # ------------------------------------------------------------------
    def click_left(self):
        try:
            self._mouse.click(Button.left, 1)
            logger.info("Action: Left click")
        except Exception:
            logger.exception("Left click failed.")

    def click_right(self):
        try:
            self._mouse.click(Button.right, 1)
            logger.info("Action: Right click")
        except Exception:
            logger.exception("Right click failed.")

    # ------------------------------------------------------------------
    # Copy / Paste
    # ------------------------------------------------------------------
    def copy(self):
        self._key_combo(Key.ctrl, "c")
        logger.info("Action: Copy (Ctrl+C)")

    def paste(self):
        self._key_combo(Key.ctrl, "v")
        logger.info("Action: Paste (Ctrl+V)")

    def _key_combo(self, modifier, key_char):
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(key_char)
            self._keyboard.release(key_char)
            self._keyboard.release(modifier)
        except Exception:
            logger.exception("Key combo action failed.")

    # ------------------------------------------------------------------
    # Zoom (Ctrl + Mouse Wheel)
    # ------------------------------------------------------------------
    def zoom_in(self):
        self._ctrl_scroll(config.ZOOM_WHEEL_AMOUNT)
        logger.info("Action: Zoom in (Ctrl+WheelUp)")

    def zoom_out(self):
        self._ctrl_scroll(-config.ZOOM_WHEEL_AMOUNT)
        logger.info("Action: Zoom out (Ctrl+WheelDown)")

    def _ctrl_scroll(self, amount: int):
        try:
            self._keyboard.press(Key.ctrl)
            self._mouse.scroll(0, 1 if amount > 0 else -1)
            self._keyboard.release(Key.ctrl)
        except Exception:
            logger.exception("Ctrl+Scroll (zoom) action failed.")
