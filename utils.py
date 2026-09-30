"""
utils.py
========
Small, dependency-light helper utilities shared across the application:

- Logger setup (rotating file handler + console)
- Geometry helpers (distance, clamp, map_range)
- Exponential smoothing filter for noisy signals (landmarks / cursor)
- A lightweight rolling FPS counter

Keeping these here avoids duplicating logic in camera.py, hand_tracker.py,
gesture_detector.py and mouse_controller.py.
"""

import ctypes
import logging
import math
import time
from collections import deque
from logging.handlers import RotatingFileHandler

import config


def setup_logger(name: str = "hand_gesture_control") -> logging.Logger:
    """Create (or fetch) a configured logger with rotating file + console output.

    Safe to call multiple times from different modules; handlers are only
    attached once thanks to the `logger.handlers` guard.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def distance(p1, p2) -> float:
    """Euclidean distance between two (x, y) points."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def clamp(value, low, high):
    """Clamp `value` into the inclusive range [low, high]."""
    return max(low, min(high, value))


def map_range(value, in_min, in_max, out_min, out_max):
    """Linearly map `value` from [in_min, in_max] to [out_min, out_max], clamped."""
    if in_max - in_min == 0:
        return out_min
    value = clamp(value, in_min, in_max)
    ratio = (value - in_min) / (in_max - in_min)
    return out_min + ratio * (out_max - out_min)


# ---------------------------------------------------------------------------
# Exponential smoothing filter
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# One-Euro filter
# ---------------------------------------------------------------------------
class _LowPassFilter:
    """Internal exponential low-pass filter building block for OneEuroFilter."""

    def __init__(self):
        self._initialized = False
        self._value = 0.0
        self._raw_value = 0.0

    def filter(self, value, alpha):
        if not self._initialized:
            self._value = value
            self._initialized = True
        else:
            self._value = alpha * value + (1.0 - alpha) * self._value
        self._raw_value = value
        return self._value

    @property
    def last_raw(self):
        return self._raw_value

    def reset(self):
        self._initialized = False


class OneEuroFilter:
    """1-Euro adaptive low-pass filter (Casiez, Roussel, Vogel - CHI 2012).

    Unlike a fixed-alpha exponential smoother, this filter adapts its own
    cutoff frequency to the *speed* of the incoming signal:

    - When the signal is moving slowly (e.g. a hand resting, held still)
      the filter applies heavy smoothing -> kills jitter.
    - When the signal is moving quickly (e.g. a fast hand swipe) the
      filter applies light smoothing -> kills lag, stays responsive.

    This single filter is what lets the app be simultaneously stable
    (Priority 1) and responsive (Priority 2) without the two fighting
    each other, which a single fixed alpha cannot do well.

    Operates on scalars; wrap with a small tuple-adapter for 2D points
    (see PointOneEuroFilter below).
    """

    def __init__(self, freq=30.0, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff

        self._x_filter = _LowPassFilter()
        self._dx_filter = _LowPassFilter()
        self._last_time = None

    @staticmethod
    def _alpha(cutoff, freq):
        te = 1.0 / freq
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def __call__(self, value, timestamp=None):
        t = timestamp if timestamp is not None else time.time()

        if self._last_time is not None:
            dt = t - self._last_time
            if dt > 0:
                self.freq = 1.0 / dt
        self._last_time = t

        # Estimate the derivative (speed) of the signal.
        prev_x = self._x_filter.last_raw if self._x_filter._initialized else value
        dx = (value - prev_x) * self.freq
        edx = self._dx_filter.filter(dx, self._alpha(self.d_cutoff, self.freq))

        # Adaptive cutoff: higher speed -> higher cutoff -> less smoothing.
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x_filter.filter(value, self._alpha(cutoff, self.freq))

    def reset(self):
        self._x_filter.reset()
        self._dx_filter.reset()
        self._last_time = None


class PointOneEuroFilter:
    """Convenience wrapper applying an independent OneEuroFilter to each
    axis of a 2D (x, y) point."""

    def __init__(self, freq=30.0, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self._fx = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)
        self._fy = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)

    def filter(self, point, timestamp=None):
        x, y = point
        return (self._fx(x, timestamp), self._fy(y, timestamp))

    def reset(self):
        self._fx.reset()
        self._fy.reset()


class ExponentialSmoother:
    """1D or 2D exponential moving average filter.

    new_value = alpha * raw + (1 - alpha) * previous_smoothed

    Lower alpha = smoother but more lag. Higher alpha = more responsive but
    more jitter. Used for both landmark stabilization and cursor motion.
    """

    def __init__(self, alpha: float = 0.4):
        self.alpha = alpha
        self._value = None

    def reset(self):
        self._value = None

    def update(self, raw_value):
        if self._value is None:
            self._value = raw_value
            return self._value

        if isinstance(raw_value, (tuple, list)):
            self._value = tuple(
                self.alpha * r + (1 - self.alpha) * v
                for r, v in zip(raw_value, self._value)
            )
        else:
            self._value = self.alpha * raw_value + (1 - self.alpha) * self._value

        return self._value

    @property
    def value(self):
        return self._value


class LandmarkHistory:
    """Rolling window average for a single (x, y) landmark point.

    Provides extra noise rejection on top of exponential smoothing by
    averaging the last N raw samples. Cheap: O(1) amortized with deque.
    """

    def __init__(self, maxlen: int = 5):
        self._points = deque(maxlen=maxlen)

    def push(self, point):
        self._points.append(point)
        return self.average()

    def average(self):
        if not self._points:
            return None
        xs = sum(p[0] for p in self._points) / len(self._points)
        ys = sum(p[1] for p in self._points) / len(self._points)
        return (xs, ys)

    def clear(self):
        self._points.clear()


# ---------------------------------------------------------------------------
# FPS counter
# ---------------------------------------------------------------------------
class FPSCounter:
    """Rolling FPS counter based on a short time window of frame timestamps."""

    def __init__(self, window_size: int = 30):
        self._timestamps = deque(maxlen=window_size)

    def tick(self) -> float:
        """Call once per processed frame. Returns the current smoothed FPS."""
        now = time.time()
        self._timestamps.append(now)
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / elapsed


class Debouncer:
    """Generic time-based debouncer / cooldown gate.

    Usage:
        gate = Debouncer(cooldown=0.5)
        if gate.ready():
            gate.fire()
            do_action()
    """

    def __init__(self, cooldown: float):
        self.cooldown = cooldown
        self._last_fire = 0.0

    def ready(self) -> bool:
        return (time.time() - self._last_fire) >= self.cooldown

    def fire(self):
        self._last_fire = time.time()

    def reset(self):
        self._last_fire = 0.0


# ---------------------------------------------------------------------------
# Lightweight system-wide CPU usage monitor (Windows only, no psutil needed)
# ---------------------------------------------------------------------------
class WindowsCPUMonitor:
    """Estimates system-wide CPU usage percentage using the Win32 API
    `GetSystemTimes`, avoiding a dependency on psutil.

    Two consecutive samples are required to compute a delta-based
    percentage, so the first call to `sample()` after construction (or
    after a long gap) returns 0.0.

    Falls back gracefully (returns 0.0) on any non-Windows platform or if
    the API call fails for any reason, so the rest of the app never
    crashes because of this optional feature.
    """

    def __init__(self):
        self._prev_idle = None
        self._prev_kernel = None
        self._prev_user = None
        self._available = hasattr(ctypes, "windll")

    @staticmethod
    def _filetime_to_int(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    def sample(self) -> float:
        if not self._available:
            return 0.0

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                        ("dwHighDateTime", ctypes.c_uint32)]

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        try:
            success = ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
        except Exception:
            self._available = False
            return 0.0

        if not success:
            return 0.0

        idle_val = self._filetime_to_int(idle)
        kernel_val = self._filetime_to_int(kernel)
        user_val = self._filetime_to_int(user)

        if self._prev_idle is None:
            self._prev_idle, self._prev_kernel, self._prev_user = (
                idle_val, kernel_val, user_val,
            )
            return 0.0

        idle_delta = idle_val - self._prev_idle
        kernel_delta = kernel_val - self._prev_kernel
        user_delta = user_val - self._prev_user

        self._prev_idle, self._prev_kernel, self._prev_user = (
            idle_val, kernel_val, user_val,
        )

        total_delta = kernel_delta + user_delta
        if total_delta <= 0:
            return 0.0

        busy_delta = total_delta - idle_delta
        usage = (busy_delta / total_delta) * 100.0
        return clamp(usage, 0.0, 100.0)
