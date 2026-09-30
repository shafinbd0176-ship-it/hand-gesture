"""
config.py
=========
Central configuration for Hand Gesture Control.

All tunable constants live here so behaviour can be adjusted without
touching business logic elsewhere in the codebase.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
CAMERA_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_MAX_FPS = 20
CAMERA_MIN_FPS = 8            # Floor the adaptive throttler will not go below
CAMERA_FPS_STEP = 2           # How much to reduce FPS by when CPU is high
CAMERA_BUFFER_SIZE = 1        # Keep webcam buffer small -> low latency

# ---------------------------------------------------------------------------
# Adaptive performance / CPU throttling
# ---------------------------------------------------------------------------
CPU_CHECK_INTERVAL_SEC = 2.0
CPU_HIGH_THRESHOLD = 80.0     # percent -> start throttling
CPU_LOW_THRESHOLD = 50.0      # percent -> allow FPS to recover
PROCESS_EVERY_N_FRAMES_MIN = 1
PROCESS_EVERY_N_FRAMES_MAX = 3

# ---------------------------------------------------------------------------
# MediaPipe Hands
# ---------------------------------------------------------------------------
# Single-hand tracking: the final gesture list is entirely single-hand, so
# we keep MAX_NUM_HANDS at 1 to minimize CPU cost (a second hand would
# roughly double MediaPipe's per-frame inference cost for no benefit).
MAX_NUM_HANDS = 1

# Detection confidence is kept relatively high (fewer false-positive hand
# detections = less jitter from spurious re-detections), while tracking
# confidence is kept lower than detection confidence -- this is the
# pattern MediaPipe itself recommends: once a hand is locked on, keep
# tracking it through motion blur / partial occlusion rather than
# discarding and re-running the (more expensive, less stable) full
# detector every time confidence dips slightly.
MIN_DETECTION_CONFIDENCE = 0.75
MIN_TRACKING_CONFIDENCE = 0.55
MODEL_COMPLEXITY = 0           # 0 = fastest / lite model, best for low-end CPUs

# How long (seconds) to keep treating the hand as "present" after MediaPipe
# briefly fails to detect it for a frame or two (motion blur, fast move,
# temporary occlusion). During this grace window the last known pose is
# held rather than immediately reporting "hand lost", which is the single
# biggest fix for the "loses the hand constantly" complaint.
HAND_LOST_GRACE_SEC = 0.25

# ---------------------------------------------------------------------------
# Screen / cursor mapping (relative / mouse-like movement model)
# ---------------------------------------------------------------------------
# Cursor movement is RELATIVE (like a real mouse) rather than mapping an
# absolute hand position to an absolute screen position. Each frame we take
# the filtered hand-point delta and move the OS cursor by that delta times
# a speed-dependent gain, exactly like OS mouse pointer acceleration.
CURSOR_BASE_SENSITIVITY = 3800    # px moved per 1.0 normalized-unit of delta at gain=1.0
CURSOR_MIN_MOVE_PX = 1            # ignore jitter below this pixel delta

# Acceleration curve: gain scales from ACCEL_MIN_GAIN (slow/precise hand
# movement) up to ACCEL_MAX_GAIN (fast hand movement) as hand speed
# approaches ACCEL_VELOCITY_REF (normalized units/sec). This is what makes
# small precise movements stay precise while large fast movements cover
# the screen quickly -- "mouse-like" responsiveness.
ACCEL_MIN_GAIN = 0.55
ACCEL_MAX_GAIN = 3.0
ACCEL_VELOCITY_REF = 0.55

# Precision Cursor Mode (index-finger-only) dampens the overall gain for
# fine, deliberate control.
PRECISION_GAIN_MULTIPLIER = 0.35

# ---------------------------------------------------------------------------
# One-Euro filter parameters (used for BOTH landmark smoothing and final
# cursor-position smoothing). The One-Euro filter adapts its own smoothing
# strength to signal speed: slow movement -> heavier smoothing (kills
# jitter), fast movement -> lighter smoothing (kills lag). This directly
# resolves the "stable but responsive" tension between Priority 1 and
# Priority 2 far better than a fixed exponential-smoothing alpha.
# Reference: Casiez, Roussel, Vogel - "1 Euro Filter" (CHI 2012).
# ---------------------------------------------------------------------------
LANDMARK_ONEEURO_MIN_CUTOFF = 1.0
LANDMARK_ONEEURO_BETA = 0.7
LANDMARK_ONEEURO_DCUTOFF = 1.0

CURSOR_ONEEURO_MIN_CUTOFF = 0.9
CURSOR_ONEEURO_BETA = 1.0
CURSOR_ONEEURO_DCUTOFF = 1.0

# ---------------------------------------------------------------------------
# Gesture geometry thresholds (normalized 0-1 landmark space unless noted)
# ---------------------------------------------------------------------------
PINCH_TOUCH_DISTANCE = 0.045     # thumb tip <-> other fingertip distance to count as "touching"
PINCH_RELEASE_DISTANCE = 0.065   # must exceed this to count as "released" (hysteresis)

FINGER_TOGETHER_DISTANCE = 0.06  # index tip <-> middle tip distance for "together" pose
FINGER_APART_DISTANCE = 0.09     # distance to count as "apart", hysteresis

# ---------------------------------------------------------------------------
# Gesture timing / debounce
# ---------------------------------------------------------------------------
GESTURE_STABLE_TIME_SEC = 0.12      # pose must be held this long before it "fires"
CLICK_COOLDOWN_SEC = 0.5            # minimum time between repeat clicks of same type
ZOOM_REPEAT_INTERVAL_SEC = 0.35     # pulse interval while pinch(zoom-in)/fist(zoom-out) held
SCROLL_REPEAT_INTERVAL_SEC = 0.08   # minimum interval between scroll ticks

# Thumb+Index is overloaded (per spec: "Pinching" = Zoom In, "Thumb touching
# Index" = Left Click). Resolved via tap-vs-hold, the same disambiguation
# every touchscreen uses for tap vs. long-press:
#   - released before PINCH_HOLD_TO_ZOOM_SEC  -> Left Click (single)
#   - still held past PINCH_HOLD_TO_ZOOM_SEC  -> Zoom In (repeats while held)
# This is documented again in gesture_detector.py next to the logic itself.
PINCH_HOLD_TO_ZOOM_SEC = 0.30

# Landmark position smoothing (reduces per-frame jitter before gesture logic runs)
LANDMARK_HISTORY_LEN = 5

# Minimum hand-center velocity (normalized units/sec) to be considered "moving"
# vs "stationary" -- used to distinguish the static fist/zoom-out pose from
# ordinary cursor motion.
MOTION_VELOCITY_THRESHOLD = 0.30

# Scroll gesture vertical movement thresholds (normalized units)
SCROLL_TRIGGER_DELTA = 0.035
SCROLL_WHEEL_AMOUNT = 40

# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------
PYAUTOGUI_PAUSE = 0.0            # disable pyautogui's built-in per-call sleep
ZOOM_WHEEL_AMOUNT = 60           # wheel "clicks" magnitude sent for ctrl+scroll zoom

# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------
HOTKEY_EXIT = "esc"
HOTKEY_ENABLE = "f8"
HOTKEY_DISABLE = "f9"

# ---------------------------------------------------------------------------
# UI / App identity
# ---------------------------------------------------------------------------
APP_NAME = "ALPHA Air Mouse"
APP_SUBTITLE = "Professional AI Hand Gesture Mouse Controller"
APP_VERSION = "2.0.0"
APP_DEVELOPER = "Alpha AI"
APP_TAGLINE = "Developed with Python + MediaPipe + Modern Computer Vision"

UI_TITLE = APP_NAME
UI_MIN_WIDTH = 900
UI_MIN_HEIGHT = 620
UI_UPDATE_INTERVAL_MS = 50        # how often the UI polls the shared state
UI_PREVIEW_WIDTH = 480
UI_PREVIEW_HEIGHT = 360

# Application logo, shown on the home screen and top-left corner of the app.
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
LOGO_ICON_PATH = os.path.join(ASSETS_DIR, "logo_icon.png")
LOGO_HOME_SIZE = 96
LOGO_ICON_SIZE = 36

# Loading animation (shown briefly while the camera connects after Start)
LOADING_MIN_DISPLAY_SEC = 0.6
LOADING_TIMEOUT_SEC = 6.0

# Fade transition (whole-window alpha fade when switching screens)
FADE_STEPS = 8
FADE_STEP_DELAY_MS = 12

# Dark theme palette (modern card-based look)
COLOR_BG = "#12141a"
COLOR_BG_GRADIENT_TOP = "#161923"
COLOR_PANEL = "#1b1e29"
COLOR_CARD = "#1f2330"
COLOR_CARD_HOVER = "#272c3d"
COLOR_TEXT = "#eef0f6"
COLOR_TEXT_MUTED = "#9aa0b4"
COLOR_MUTED = "#9aa0b4"
COLOR_ACCENT = "#5b8cff"
COLOR_ACCENT_HOVER = "#4a78e6"
COLOR_ACCENT_GREEN = "#4caf50"
COLOR_ACCENT_OFF = "#e5484d"
COLOR_BORDER = "#2a2f40"
COLOR_BUTTON = "#232838"
COLOR_BUTTON_HOVER = "#2c3247"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(LOG_DIR, "app.log")
LOG_MAX_BYTES = 2 * 1024 * 1024   # 2 MB rotate
LOG_BACKUP_COUNT = 3
LOG_LEVEL = "INFO"

# ---------------------------------------------------------------------------
# Threading / queues
# ---------------------------------------------------------------------------
FRAME_QUEUE_MAXSIZE = 2           # small -> drop old frames instead of lagging
UI_STATE_QUEUE_MAXSIZE = 2
