"""
scroll_controller.py
=====================
Handles plain (non-Ctrl) vertical scroll wheel events, triggered by the
"Index + Middle finger together, move up/down" gesture.

Kept separate from mouse_controller.py so scroll behaviour (e.g. speed,
inertia) can be tuned independently from cursor movement and zoom.
"""

from pynput.mouse import Controller as PynputMouse

from utils import setup_logger

logger = setup_logger(__name__)


class ScrollController:
    """Owns scroll-wheel output actions."""

    def __init__(self):
        self._mouse = PynputMouse()

    def scroll_up(self):
        try:
            self._mouse.scroll(0, 1)
            logger.debug("Action: Scroll up")
        except Exception:
            logger.exception("Scroll up failed.")

    def scroll_down(self):
        try:
            self._mouse.scroll(0, -1)
            logger.debug("Action: Scroll down")
        except Exception:
            logger.exception("Scroll down failed.")
