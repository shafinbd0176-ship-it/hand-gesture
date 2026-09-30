"""
camera.py
=========
Owns the webcam. Runs entirely on its own thread so a slow camera or a
busy CPU never blocks gesture processing or the UI.

Responsibilities:
- Open / reconnect to the webcam.
- Capture frames at an adaptive frame rate (throttles down automatically
  when system CPU usage gets high, recovers when it drops).
- Publish the newest frame into a bounded queue (size 1-2) so downstream
  consumers always work on the freshest frame instead of a backlog.
"""

import queue
import threading
import time

import cv2

import config
from utils import setup_logger, WindowsCPUMonitor

logger = setup_logger(__name__)


class CameraThread(threading.Thread):
    """Background thread that continuously grabs frames from the webcam."""

    def __init__(self, frame_queue: "queue.Queue", camera_index: int = config.CAMERA_INDEX):
        super().__init__(daemon=True, name="CameraThread")
        self.frame_queue = frame_queue
        self.camera_index = camera_index

        self._stop_event = threading.Event()
        self._cap = None

        self._current_fps_limit = config.CAMERA_MAX_FPS
        self._cpu_monitor = WindowsCPUMonitor()
        self._last_cpu_check = 0.0

        self.connected = False
        self.actual_fps = 0.0
        self.cpu_usage = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _open_camera(self) -> bool:
        try:
            # CAP_DSHOW is the fast, low-latency backend on Windows.
            self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self._cap = cv2.VideoCapture(self.camera_index)

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, config.CAMERA_BUFFER_SIZE)
            self._cap.set(cv2.CAP_PROP_FPS, config.CAMERA_MAX_FPS)

            opened = self._cap.isOpened()
            self.connected = opened
            if opened:
                logger.info("Camera %s opened successfully.", self.camera_index)
            else:
                logger.error("Failed to open camera index %s.", self.camera_index)
            return opened
        except Exception:
            logger.exception("Exception while opening camera.")
            self.connected = False
            return False

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Adaptive FPS control
    # ------------------------------------------------------------------
    def _update_adaptive_fps(self):
        """Poll CPU usage periodically and adjust the target FPS accordingly.

        This keeps the app responsive on low-end hardware: if the CPU is
        struggling, we simply capture (and therefore process) fewer frames
        per second rather than freezing or crashing.
        """
        now = time.time()
        if now - self._last_cpu_check < config.CPU_CHECK_INTERVAL_SEC:
            return
        self._last_cpu_check = now

        self.cpu_usage = self._cpu_monitor.sample()

        if self.cpu_usage >= config.CPU_HIGH_THRESHOLD:
            new_limit = max(config.CAMERA_MIN_FPS, self._current_fps_limit - config.CAMERA_FPS_STEP)
            if new_limit != self._current_fps_limit:
                logger.info("High CPU usage (%.1f%%) - lowering FPS limit to %s.", self.cpu_usage, new_limit)
            self._current_fps_limit = new_limit
        elif self.cpu_usage <= config.CPU_LOW_THRESHOLD:
            new_limit = min(config.CAMERA_MAX_FPS, self._current_fps_limit + config.CAMERA_FPS_STEP)
            if new_limit != self._current_fps_limit:
                logger.info("CPU usage normal (%.1f%%) - raising FPS limit to %s.", self.cpu_usage, new_limit)
            self._current_fps_limit = new_limit

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        logger.info("Camera thread starting.")
        if not self._open_camera():
            # Retry loop -- keep trying in case the camera is plugged in later.
            while not self._stop_event.is_set() and not self.connected:
                time.sleep(1.0)
                self._open_camera()

        frame_count = 0
        window_start = time.time()

        while not self._stop_event.is_set():
            self._update_adaptive_fps()
            frame_interval = 1.0 / max(1, self._current_fps_limit)
            loop_start = time.time()

            if self._cap is None or not self._cap.isOpened():
                self.connected = False
                logger.warning("Camera disconnected, attempting reconnect...")
                time.sleep(1.0)
                self._open_camera()
                continue

            ok, frame = self._cap.read()
            if not ok or frame is None:
                self.connected = False
                logger.warning("Failed to read frame from camera.")
                time.sleep(0.2)
                continue

            self.connected = True
            frame = cv2.flip(frame, 1)  # mirror for natural interaction

            # Keep the queue shallow: drop the oldest frame if full so the
            # consumer always gets the most recent one (low latency > completeness).
            try:
                if self.frame_queue.full():
                    self.frame_queue.get_nowait()
                self.frame_queue.put_nowait(frame)
            except queue.Full:
                pass
            except queue.Empty:
                pass

            frame_count += 1
            elapsed = time.time() - window_start
            if elapsed >= 1.0:
                self.actual_fps = frame_count / elapsed
                frame_count = 0
                window_start = time.time()

            # Sleep just enough to respect the adaptive FPS cap.
            processing_time = time.time() - loop_start
            sleep_time = frame_interval - processing_time
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._release()
        logger.info("Camera thread stopped.")

    def _release(self):
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            logger.exception("Error releasing camera.")
