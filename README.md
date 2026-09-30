# ALPHA Air Mouse

A lightweight, CPU-only webcam gesture-controlled mouse for Windows 10/11,
optimized for low-end hardware (Intel Core 2 Duo, 4GB RAM, integrated
graphics — no dedicated GPU required). Turns your webcam into a full mouse
replacement using AI-powered hand tracking.

---

## Important: Python Version

MediaPipe does not officially publish wheels for Python 3.14 (it trails
1-2 releases behind). **Use Python 3.10, 3.11, or 3.12 (64-bit Windows).**
Nothing else in this codebase needs to change — only the interpreter.

---

## What's New in This Version

- **Hand-lost grace period** — a momentary MediaPipe miss (fast move,
  motion blur, brief occlusion) no longer instantly reports "hand lost."
  The last known pose is held for `HAND_LOST_GRACE_SEC` (default 0.25s)
  before truly declaring the hand gone — this is the main fix for
  tracking that used to "flicker."
- **One-Euro adaptive filtering** replaces the old fixed-alpha smoothing
  for both landmarks and cursor position. It automatically applies heavy
  smoothing when the hand is nearly still (kills jitter) and light
  smoothing when it's moving fast (kills lag) — instead of picking one
  fixed tradeoff.
- **Relative, mouse-like cursor movement** with speed-adaptive
  acceleration (`config.ACCEL_*`) — small slow movements stay precise,
  fast movements cover the screen quickly, just like real mouse pointer
  acceleration. Replaced the old absolute hand-position-to-screen-position
  mapping.
- **Detection/tracking confidence tuned** — higher detection confidence
  (fewer false hand detections) paired with lower tracking confidence
  (keeps tracking locked-on through motion blur instead of re-running
  the more expensive full detector).
- **New gesture set** (see table below) — Pinch now zooms in, Fist zooms
  out, and Thumb+Ring / Thumb+Pinky now trigger Copy / Paste.
- **Brand new Home screen** — the app opens on a welcome/info screen with
  the ALPHA Air Mouse logo, gesture reference cards, and a **Start Air
  Mouse** button. The camera is never touched until you press Start.
- **Fullscreen support** (`F11`, or the toolbar button), responsive to any
  screen resolution from 1366×768 up to 4K.
- **Live status panel** on the Air Mouse screen: gesture, FPS, tracking
  confidence, mouse status, camera connection, CPU load.

---

## Gesture Reference

| Gesture | Action |
|---|---|
| Open hand, moving | Move mouse cursor |
| Index finger only extended | Precision Cursor Mode |
| Pinch (thumb+index), held | Zoom In (`Ctrl` + Wheel Up), repeats while held |
| Closed fist, held still | Zoom Out (`Ctrl` + Wheel Down), repeats while held |
| Thumb touching Index (quick tap) | Left Click (single) |
| Thumb touching Middle | Right Click (single) |
| Thumb touching Ring | Copy (`Ctrl+C`, single) |
| Thumb touching Pinky | Paste (`Ctrl+V`, single) |
| Index + Middle together, moving up | Scroll Up |
| Index + Middle together, moving down | Scroll Down |

### Resolving the Pinch / Left-Click overlap

"Pinch (hold) → Zoom In" and "Thumb touching Index → Left Click" are the
same physical pose (thumb tip touching index tip). This is resolved with
a **tap vs. hold** split — the same disambiguation every touchscreen uses
for tap vs. long-press:

- Released before `PINCH_HOLD_TO_ZOOM_SEC` (default 0.30s) → **Left Click**
- Still held past that → **Zoom In**, repeating every `ZOOM_REPEAT_INTERVAL_SEC`

Tune both in `config.py` if you want a snappier/slower split.

---

## Using the App

1. Launch with `python main.py`. You land on the **Home screen** — no
   camera access yet.
2. Press **▶ Start Air Mouse**. A brief "Initializing camera..." state
   shows while the webcam connects, then the app fades into the **Air
   Mouse screen**.
3. Use the gestures above. The live status panel shows your current
   gesture, FPS, tracking confidence, and more.
4. Press **Stop** (or `ESC`) to return to the Home screen and release the
   camera. Press **Start** again anytime.
5. `F8` / `F9` enable/disable gesture recognition globally (even when the
   window isn't focused). `F11` toggles fullscreen.

---

## Project Structure

```
hand_gesture_control/
├── main.py               # Entry point; app lifecycle, lazy pipeline start/stop
├── camera.py              # Camera capture thread, adaptive FPS
├── hand_tracker.py        # MediaPipe Hands wrapper (+ confidence score)
├── gesture_detector.py    # Gesture state machine / classification
├── mouse_controller.py    # Relative cursor movement, clicks, zoom, copy/paste
├── scroll_controller.py   # Scroll wheel actions
├── config.py               # All tunable constants
├── settings.py             # Tkinter UI: Home screen + Air Mouse screen
├── utils.py                 # Logging, One-Euro filter, CPU monitor, FPS counter
├── assets/                  # logo.png, logo_icon.png
├── logs/                    # Rotating log files (created automatically)
├── requirements.txt
└── README.md
```

---

## Installation

```
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Allow the camera permission / firewall prompt on first run.

---

## Building a Standalone .exe

```
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "AlphaAirMouse" ^
    --collect-data mediapipe --add-data "assets;assets" main.py
```

The executable will be at `dist/AlphaAirMouse.exe`.

---

## Tuning

All knobs live in **`config.py`**:

- `MIN_DETECTION_CONFIDENCE` / `MIN_TRACKING_CONFIDENCE` — detection vs.
  tracking confidence (see "What's New" above for why they differ).
- `HAND_LOST_GRACE_SEC` — how long to tolerate a momentary tracking miss.
- `CURSOR_BASE_SENSITIVITY`, `ACCEL_MIN_GAIN`, `ACCEL_MAX_GAIN`,
  `ACCEL_VELOCITY_REF` — cursor speed and acceleration curve.
- `LANDMARK_ONEEURO_*`, `CURSOR_ONEEURO_*` — One-Euro filter tuning
  (`beta` controls how aggressively speed reduces smoothing).
- `PINCH_HOLD_TO_ZOOM_SEC` — tap-vs-hold threshold for the pinch gesture.
- `GESTURE_STABLE_TIME_SEC`, `PINCH_TOUCH_DISTANCE`,
  `PINCH_RELEASE_DISTANCE` — general gesture debounce/hysteresis.

---

## Logs

`logs/app.log` (rotated at 2MB, 3 backups) captures camera failures,
MediaPipe errors, and unhandled per-frame exceptions without crashing the
main loop.

---

## Hotkeys

| Key | Action |
|---|---|
| `ESC` | Exit fullscreen, then Stop Air Mouse, then exit app (in that priority order) |
| `F8` | Enable gesture recognition |
| `F9` | Disable gesture recognition |
| `F11` | Toggle fullscreen |

---

## Known Limitations

- Single-hand tracking only, by design, for CPU efficiency.
- Windows-only: uses `GetSystemTimes` and `SetProcessDPIAware` via ctypes.
- Low ambient light reduces MediaPipe detection confidence; keep the hand
  reasonably well lit.
