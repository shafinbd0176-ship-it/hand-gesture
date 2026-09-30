"""
main.py
=======
Application entry point for ALPHA Air Mouse.

Thread layout:
    - Camera thread   (camera.py::CameraThread)   -> captures webcam frames
    - Gesture thread  (this file::GestureThread)   -> tracking + classification + output
    - UI thread       (this file::main(), Tkinter mainloop)

The camera and gesture threads are created lazily: the app opens on the
Home screen with the camera untouched, and only starts capturing once the
user presses "Start Air Mouse" (see settings.py). Pressing Stop tears both
threads down cleanly; pressing Start again builds fresh ones -- this keeps
every run starting from a clean tracking/filter state.

A single thread-safe SharedState object is all that's shared between
threads.
"""

import queue
import sys
import threading
import time

from pynput import keyboard as pynput_keyboard

import config
from camera import CameraThread
from gesture_detector import GestureDetector
from hand_tracker import HandTracker
from mouse_controller import MouseController
from scroll_controller import ScrollController
from settings import AlphaAirMouseUI
from utils import setup_logger, FPSCounter

logger = setup_logger(__name__)


class SharedState:
    """Thread-safe container for values the UI thread reads and the
    gesture thread writes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._gesture_name = "None"
        self._fps = 0.0
        self._cpu_usage = 0.0
        self._camera_connected = False
        self._enabled = True
        self._tracking_confidence = 0.0

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, f"_{key}", value)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "frame": self._frame,
                "gesture_name": self._gesture_name,
                "fps": self._fps,
                "cpu_usage": self._cpu_usage,
                "camera_connected": self._camera_connected,
                "enabled": self._enabled,
                "tracking_confidence": self._tracking_confidence,
            }


class GestureThread(threading.Thread):
    """Consumes frames from the camera queue, runs hand tracking + gesture
    classification, and dispatches the resulting mouse/scroll/zoom/copy/
    paste actions."""

    def __init__(self, frame_queue: "queue.Queue", shared_state: SharedState,
                 camera_thread: CameraThread):
        super().__init__(daemon=True, name="GestureThread")
        self.frame_queue = frame_queue
        self.shared_state = shared_state
        self.camera_thread = camera_thread

        self._stop_event = threading.Event()

        self.hand_tracker = HandTracker()
        self.gesture_detector = GestureDetector()
        self.mouse = MouseController()
        self.scroll = ScrollController()

        self._fps_counter = FPSCounter()
        self._had_hand_last_frame = False

        self._process_every_n = config.PROCESS_EVERY_N_FRAMES_MIN
        self._frame_counter = 0

    def stop(self):
        self._stop_event.set()

    def set_enabled(self, value: bool):
        self.gesture_detector.enabled = value
        self.shared_state.update(enabled=value)

    def toggle_enabled(self):
        self.set_enabled(not self.gesture_detector.enabled)

    def run(self):
        logger.info("Gesture thread starting.")
        while not self._stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                self.shared_state.update(camera_connected=self.camera_thread.connected)
                continue

            self._frame_counter += 1
            self._process_every_n = self._compute_frame_skip()
            if self._frame_counter % self._process_every_n != 0:
                self.shared_state.update(
                    frame=frame,
                    camera_connected=self.camera_thread.connected,
                    fps=self.camera_thread.actual_fps,
                    cpu_usage=self.camera_thread.cpu_usage,
                )
                continue

            try:
                self._process_frame(frame)
            except Exception:
                logger.exception("Unhandled error while processing a frame.")

        self.hand_tracker.close()
        logger.info("Gesture thread stopped.")

    def _compute_frame_skip(self) -> int:
        cpu = self.camera_thread.cpu_usage
        if cpu >= config.CPU_HIGH_THRESHOLD:
            return config.PROCESS_EVERY_N_FRAMES_MAX
        return config.PROCESS_EVERY_N_FRAMES_MIN

    def _process_frame(self, frame):
        hand_result = self.hand_tracker.process(frame)

        if hand_result.detected != self._had_hand_last_frame:
            self.mouse.reset_smoothing()
            self._had_hand_last_frame = hand_result.detected

        gesture_state = self.gesture_detector.update(hand_result)
        self._dispatch_actions(gesture_state)

        if gesture_state.cursor_point is not None and gesture_state.enabled:
            self.mouse.move_relative(
                gesture_state.cursor_point, time.time(),
                gain_multiplier=gesture_state.cursor_gain_multiplier,
            )

        preview_frame = self.hand_tracker.draw_landmarks(frame, hand_result)
        fps = self._fps_counter.tick()

        self.shared_state.update(
            frame=preview_frame,
            gesture_name=gesture_state.gesture_name,
            fps=fps,
            cpu_usage=self.camera_thread.cpu_usage,
            camera_connected=self.camera_thread.connected,
            enabled=gesture_state.enabled,
            tracking_confidence=gesture_state.tracking_confidence,
        )

    def _dispatch_actions(self, gesture_state):
        for action in gesture_state.actions:
            if action == "click_left":
                self.mouse.click_left()
            elif action == "click_right":
                self.mouse.click_right()
            elif action == "copy":
                self.mouse.copy()
            elif action == "paste":
                self.mouse.paste()
            elif action == "zoom_in":
                self.mouse.zoom_in()
            elif action == "zoom_out":
                self.mouse.zoom_out()
            elif action == "scroll_up":
                self.scroll.scroll_up()
            elif action == "scroll_down":
                self.scroll.scroll_down()


class Application:
    """Top-level orchestrator: owns the UI and lazily-created camera/gesture
    threads, coordinating clean start/stop/shutdown from any trigger."""

    def __init__(self):
        self.shared_state = SharedState()
        self.frame_queue = None
        self.camera_thread = None
        self.gesture_thread = None
        self._pipeline_running = False

        self.ui = AlphaAirMouseUI(
            shared_state=self.shared_state,
            on_start=self.start_pipeline,
            on_stop=self.stop_pipeline,
            on_toggle_enabled=self._toggle_enabled,
            on_exit=self.shutdown,
        )

        self._hotkey_listener = pynput_keyboard.Listener(on_press=self._on_key_press)
        self._shutting_down = False

    # ------------------------------------------------------------------
    def _toggle_enabled(self):
        if self.gesture_thread is not None:
            self.gesture_thread.toggle_enabled()

    def _on_key_press(self, key):
        try:
            if key == pynput_keyboard.Key.esc:
                if self.ui.is_fullscreen():
                    logger.info("Hotkey ESC pressed -> exiting fullscreen.")
                    self.ui.set_fullscreen(False)
                elif self._pipeline_running:
                    logger.info("Hotkey ESC pressed -> stopping Air Mouse.")
                    self.stop_pipeline()
                else:
                    logger.info("Hotkey ESC pressed -> exiting.")
                    self.shutdown()
            elif key == pynput_keyboard.Key.f8:
                logger.info("Hotkey F8 pressed -> enabling gestures.")
                if self.gesture_thread is not None:
                    self.gesture_thread.set_enabled(True)
            elif key == pynput_keyboard.Key.f9:
                logger.info("Hotkey F9 pressed -> disabling gestures.")
                if self.gesture_thread is not None:
                    self.gesture_thread.set_enabled(False)
            elif key == pynput_keyboard.Key.f11:
                self.ui.toggle_fullscreen()
        except Exception:
            logger.exception("Error handling global hotkey.")

    # ------------------------------------------------------------------
    # Pipeline (camera + gesture threads) lifecycle
    # ------------------------------------------------------------------
    def start_pipeline(self):
        if self._pipeline_running:
            return
        logger.info("Starting Air Mouse pipeline.")

        self.frame_queue = queue.Queue(maxsize=config.FRAME_QUEUE_MAXSIZE)
        self.camera_thread = CameraThread(self.frame_queue)
        self.gesture_thread = GestureThread(self.frame_queue, self.shared_state, self.camera_thread)

        self.camera_thread.start()
        self.gesture_thread.start()
        self._pipeline_running = True

    def stop_pipeline(self):
        if not self._pipeline_running:
            return
        logger.info("Stopping Air Mouse pipeline.")

        if self.gesture_thread is not None:
            self.gesture_thread.stop()
        if self.camera_thread is not None:
            self.camera_thread.stop()

        if self.gesture_thread is not None:
            self.gesture_thread.join(timeout=2.0)
        if self.camera_thread is not None:
            self.camera_thread.join(timeout=2.0)

        self.gesture_thread = None
        self.camera_thread = None
        self.frame_queue = None
        self._pipeline_running = False

        self.shared_state.update(frame=None, gesture_name="None", camera_connected=False)
        self.ui.go_home()

    # ------------------------------------------------------------------
    def start(self):
        logger.info("Application starting.")
        self._hotkey_listener.start()
        self.ui.run()  # blocks on the Tk mainloop (UI thread)

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Application shutting down.")

        try:
            self._hotkey_listener.stop()
        except Exception:
            logger.exception("Error stopping hotkey listener.")

        if self._pipeline_running:
            if self.gesture_thread is not None:
                self.gesture_thread.stop()
            if self.camera_thread is not None:
                self.camera_thread.stop()
            if self.gesture_thread is not None:
                self.gesture_thread.join(timeout=2.0)
            if self.camera_thread is not None:
                self.camera_thread.join(timeout=2.0)

        self.ui.close()
        logger.info("Shutdown complete.")


def main():
    try:
        app = Application()
        app.start()
    except Exception:
        logger.exception("Fatal error during application startup.")
        sys.exit(1)


if __name__ == "__main__":
    main()
