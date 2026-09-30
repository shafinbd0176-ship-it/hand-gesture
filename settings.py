"""
settings.py
============
The complete Tkinter UI for ALPHA Air Mouse: a Home/welcome screen shown
on launch (camera untouched), and the live Air Mouse screen shown after
the user presses "Start Air Mouse".

Screens are implemented as sibling Frames stacked with `grid` + `tkraise`
inside one root window, so switching screens never destroys/rebuilds
widgets. A short whole-window alpha fade is used as a lightweight
transition between them.

Camera frames are converted to Tk-displayable images without Pillow:
OpenCV encodes the frame as PNG bytes and Tkinter's built-in `PhotoImage`
decodes PNG natively (Tcl/Tk 8.6+).
"""

import tkinter as tk

import cv2

import config
from utils import setup_logger

logger = setup_logger(__name__)


def _load_png_photoimage(path, size=None):
    """Load an image file as a Tk PhotoImage, optionally resized, using
    OpenCV for decoding/resizing (keeps Pillow out of the dependency list)."""
    try:
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if size is not None:
            img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".png", img)
        if not ok:
            return None
        return tk.PhotoImage(data=buffer.tobytes())
    except Exception:
        logger.exception("Failed to load logo image at %s", path)
        return None


def _add_hover(widget, normal_bg, hover_bg):
    widget.bind("<Enter>", lambda e: widget.configure(bg=hover_bg))
    widget.bind("<Leave>", lambda e: widget.configure(bg=normal_bg))


def _card(parent, **kwargs):
    frame = tk.Frame(parent, bg=config.COLOR_CARD, highlightthickness=1,
                      highlightbackground=config.COLOR_BORDER, **kwargs)
    return frame


class AlphaAirMouseUI:
    """Owns the Tk root window and both application screens."""

    def __init__(self, shared_state, on_start, on_stop, on_toggle_enabled, on_exit):
        self.shared_state = shared_state
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_toggle_enabled = on_toggle_enabled
        self.on_exit = on_exit

        self._fullscreen = False
        self._loading = False
        self._loading_dots = 0
        self._loading_started_at = 0.0

        self.root = tk.Tk()
        self.root.title(config.APP_NAME)
        self.root.configure(bg=config.COLOR_BG)
        self.root.minsize(config.UI_MIN_WIDTH, config.UI_MIN_HEIGHT)
        self.root.geometry(f"{config.UI_MIN_WIDTH}x{config.UI_MIN_HEIGHT}")
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._logo_home_img = _load_png_photoimage(config.LOGO_PATH, config.LOGO_HOME_SIZE)
        self._logo_icon_img = _load_png_photoimage(config.LOGO_ICON_PATH, config.LOGO_ICON_SIZE)
        self._preview_image = None  # keep a reference to avoid GC

        self._container = tk.Frame(self.root, bg=config.COLOR_BG)
        self._container.pack(fill="both", expand=True)
        self._container.grid_rowconfigure(0, weight=1)
        self._container.grid_columnconfigure(0, weight=1)

        self.home_frame = tk.Frame(self._container, bg=config.COLOR_BG)
        self.air_mouse_frame = tk.Frame(self._container, bg=config.COLOR_BG)
        for frame in (self.home_frame, self.air_mouse_frame):
            frame.grid(row=0, column=0, sticky="nsew")

        self._build_home_screen()
        self._build_air_mouse_screen()

        self.home_frame.tkraise()

    # ==================================================================
    # HOME SCREEN
    # ==================================================================
    def _build_home_screen(self):
        f = self.home_frame
        f.grid_columnconfigure(0, weight=1)

        content = tk.Frame(f, bg=config.COLOR_BG)
        content.grid(row=0, column=0, pady=(36, 16))

        if self._logo_home_img is not None:
            tk.Label(content, image=self._logo_home_img, bg=config.COLOR_BG).pack()

        tk.Label(
            content, text=config.APP_NAME, bg=config.COLOR_BG, fg=config.COLOR_TEXT,
            font=("Segoe UI", 30, "bold"),
        ).pack(pady=(10, 2))

        tk.Label(
            content, text=config.APP_SUBTITLE, bg=config.COLOR_BG, fg=config.COLOR_ACCENT,
            font=("Segoe UI", 13, "bold"),
        ).pack()

        desc = (
            "ALPHA Air Mouse turns your webcam into a full mouse replacement using "
            "AI-powered hand tracking. Move the cursor, click, scroll, zoom, copy and "
            "paste -- all with natural hand gestures, no touching your desk required."
        )
        tk.Label(
            content, text=desc, bg=config.COLOR_BG, fg=config.COLOR_TEXT_MUTED,
            font=("Segoe UI", 10), wraplength=620, justify="center",
        ).pack(pady=(14, 22))

        # Feature / gesture cards
        cards_frame = tk.Frame(f, bg=config.COLOR_BG)
        cards_frame.grid(row=1, column=0)
        gestures = [
            ("Open Hand", "Move Cursor"),
            ("Index Only", "Precision Mode"),
            ("Pinch (hold)", "Zoom In"),
            ("Fist (hold)", "Zoom Out"),
            ("Thumb + Index", "Left Click"),
            ("Thumb + Middle", "Right Click"),
            ("Thumb + Ring", "Copy"),
            ("Thumb + Pinky", "Paste"),
        ]
        for i, (gesture, action) in enumerate(gestures):
            row, col = divmod(i, 4)
            card = _card(cards_frame, width=150, height=64)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            card.grid_propagate(False)
            tk.Label(card, text=action, bg=config.COLOR_CARD, fg=config.COLOR_TEXT,
                      font=("Segoe UI", 10, "bold")).pack(pady=(12, 0))
            tk.Label(card, text=gesture, bg=config.COLOR_CARD, fg=config.COLOR_TEXT_MUTED,
                      font=("Segoe UI", 8)).pack()
            _add_hover(card, config.COLOR_CARD, config.COLOR_CARD_HOVER)

        tk.Label(
            f, text=config.APP_TAGLINE, bg=config.COLOR_BG, fg=config.COLOR_TEXT_MUTED,
            font=("Segoe UI", 9, "italic"),
        ).grid(row=2, column=0, pady=(20, 4))

        # Start button
        self.start_btn = tk.Button(
            f, text="\u25B6  Start Air Mouse", command=self._handle_start_click,
            bg=config.COLOR_ACCENT, fg="white", activebackground=config.COLOR_ACCENT_HOVER,
            activeforeground="white", relief="flat", font=("Segoe UI", 13, "bold"),
            padx=28, pady=12, cursor="hand2",
        )
        self.start_btn.grid(row=3, column=0, pady=(4, 8))
        _add_hover(self.start_btn, config.COLOR_ACCENT, config.COLOR_ACCENT_HOVER)

        # Footer
        footer = tk.Frame(f, bg=config.COLOR_BG)
        footer.grid(row=4, column=0, pady=(6, 20))
        tk.Label(
            footer,
            text=f"Version {config.APP_VERSION}   |   Developed by {config.APP_DEVELOPER}   |   F11 Fullscreen",
            bg=config.COLOR_BG, fg=config.COLOR_TEXT_MUTED, font=("Segoe UI", 8),
        ).pack()

    def _handle_start_click(self):
        self._show_loading()
        self.on_start()
        self._loading_started_at = _now()
        self.root.after(100, self._poll_loading)

    def _show_loading(self):
        self._loading = True
        self.start_btn.configure(state="disabled", text="Initializing camera...")

    def _poll_loading(self):
        if not self._loading:
            return
        snapshot = self.shared_state.snapshot()
        elapsed = _now() - self._loading_started_at

        dots = "." * ((self._loading_dots % 3) + 1)
        self._loading_dots += 1
        self.start_btn.configure(text=f"Initializing camera{dots}")

        if snapshot.get("camera_connected") or elapsed >= config.LOADING_TIMEOUT_SEC:
            self._loading = False
            self.start_btn.configure(state="normal", text="\u25B6  Start Air Mouse")
            self._go_air_mouse()
            return

        self.root.after(180, self._poll_loading)

    # ==================================================================
    # AIR MOUSE (CAMERA) SCREEN
    # ==================================================================
    def _build_air_mouse_screen(self):
        f = self.air_mouse_frame
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        # Top bar
        top_bar = tk.Frame(f, bg=config.COLOR_PANEL)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_columnconfigure(1, weight=1)

        left = tk.Frame(top_bar, bg=config.COLOR_PANEL)
        left.grid(row=0, column=0, sticky="w", padx=14, pady=10)
        if self._logo_icon_img is not None:
            tk.Label(left, image=self._logo_icon_img, bg=config.COLOR_PANEL).pack(side="left", padx=(0, 8))
        tk.Label(left, text=config.APP_NAME, bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
                  font=("Segoe UI", 12, "bold")).pack(side="left")

        right = tk.Frame(top_bar, bg=config.COLOR_PANEL)
        right.grid(row=0, column=2, sticky="e", padx=14, pady=8)

        self.toggle_btn = self._toolbar_button(right, "Disable (F9)", self._on_toggle_click)
        self.toggle_btn.pack(side="left", padx=4)

        fullscreen_btn = self._toolbar_button(right, "Fullscreen (F11)", self.toggle_fullscreen)
        fullscreen_btn.pack(side="left", padx=4)

        stop_btn = self._toolbar_button(right, "Stop", self._on_stop_click, accent=config.COLOR_ACCENT_OFF)
        stop_btn.pack(side="left", padx=4)

        # Body: preview + status panel side by side
        body = tk.Frame(f, bg=config.COLOR_BG)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        preview_card = _card(body)
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.preview_label = tk.Label(preview_card, bg="black")
        self.preview_label.pack(padx=10, pady=10, fill="both", expand=True)

        status_card = _card(body, width=260)
        status_card.grid(row=0, column=1, sticky="nsew")
        status_card.grid_propagate(False)

        tk.Label(status_card, text="Live Status", bg=config.COLOR_CARD, fg=config.COLOR_TEXT,
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(14, 8))

        self.lbl_gesture = self._status_row(status_card, "Gesture")
        self.lbl_mode = self._status_row(status_card, "Mode")
        self.lbl_fps = self._status_row(status_card, "FPS")
        self.lbl_confidence = self._status_row(status_card, "Tracking Conf.")
        self.lbl_mouse_status = self._status_row(status_card, "Mouse")
        self.lbl_camera = self._status_row(status_card, "Camera")
        self.lbl_cpu = self._status_row(status_card, "CPU")
        self.lbl_state = self._status_row(status_card, "State")

        self._poll()

    def _toolbar_button(self, parent, text, command, accent=None):
        bg = accent if accent else config.COLOR_BUTTON
        hover = config.COLOR_ACCENT_OFF if accent else config.COLOR_BUTTON_HOVER
        btn = tk.Button(
            parent, text=text, command=command, bg=bg, fg="white" if accent else config.COLOR_TEXT,
            activebackground=hover, activeforeground="white", relief="flat",
            padx=10, pady=6, font=("Segoe UI", 9), cursor="hand2",
        )
        _add_hover(btn, bg, hover)
        return btn

    def _status_row(self, parent, label_text):
        row = tk.Frame(parent, bg=config.COLOR_CARD)
        row.pack(fill="x", padx=14, pady=4)
        tk.Label(row, text=label_text, bg=config.COLOR_CARD, fg=config.COLOR_TEXT_MUTED,
                  font=("Segoe UI", 9), width=13, anchor="w").pack(side="left")
        value = tk.Label(row, text="--", bg=config.COLOR_CARD, fg=config.COLOR_TEXT,
                           font=("Segoe UI", 9, "bold"), anchor="w")
        value.pack(side="left", fill="x", expand=True)
        return value

    # ------------------------------------------------------------------
    def _on_toggle_click(self):
        self.on_toggle_enabled()

    def _on_stop_click(self):
        self.on_stop()

    def _handle_close(self):
        logger.info("UI close requested by user.")
        self.on_exit()

    def go_home(self):
        self._fade_switch(self.home_frame)

    def _go_air_mouse(self):
        self._fade_switch(self.air_mouse_frame)

    # ------------------------------------------------------------------
    # Fade transition
    # ------------------------------------------------------------------
    def _fade_switch(self, target_frame):
        steps = config.FADE_STEPS

        def fade_out(i=0):
            alpha = max(1.0 - i / steps, 0.4)
            try:
                self.root.attributes("-alpha", alpha)
            except Exception:
                pass
            if i < steps:
                self.root.after(config.FADE_STEP_DELAY_MS, fade_out, i + 1)
            else:
                target_frame.tkraise()
                fade_in(0)

        def fade_in(i=0):
            alpha = min(0.4 + i / steps * 0.6, 1.0)
            try:
                self.root.attributes("-alpha", alpha)
            except Exception:
                pass
            if i < steps:
                self.root.after(config.FADE_STEP_DELAY_MS, fade_in, i + 1)

        fade_out()

    # ------------------------------------------------------------------
    # Fullscreen
    # ------------------------------------------------------------------
    def is_fullscreen(self) -> bool:
        return self._fullscreen

    def set_fullscreen(self, value: bool):
        self._fullscreen = value
        try:
            self.root.attributes("-fullscreen", value)
        except Exception:
            logger.exception("Fullscreen toggle failed.")

    def toggle_fullscreen(self):
        self.set_fullscreen(not self._fullscreen)

    # ------------------------------------------------------------------
    # Polling loop (Tk main thread only)
    # ------------------------------------------------------------------
    def _poll(self):
        try:
            snapshot = self.shared_state.snapshot()
            self._update_preview(snapshot.get("frame"))
            self._update_labels(snapshot)
        except Exception:
            logger.exception("Error while updating UI.")
        finally:
            self.root.after(config.UI_UPDATE_INTERVAL_MS, self._poll)

    def _update_preview(self, frame):
        if frame is None:
            return
        try:
            h, w = frame.shape[:2]
            scale = min(config.UI_PREVIEW_WIDTH / w, config.UI_PREVIEW_HEIGHT / h)
            resized = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buffer = cv2.imencode(".png", resized)
            if not ok:
                return
            self._preview_image = tk.PhotoImage(data=buffer.tobytes())
            self.preview_label.configure(image=self._preview_image)
        except Exception:
            logger.exception("Failed to update camera preview.")

    def _update_labels(self, snapshot):
        gesture_name = snapshot.get("gesture_name", "--")
        self.lbl_gesture.configure(text=gesture_name)
        self.lbl_mode.configure(text=gesture_name)
        self.lbl_fps.configure(text=f"{snapshot.get('fps', 0.0):.1f}")
        self.lbl_confidence.configure(text=f"{snapshot.get('tracking_confidence', 0.0) * 100:.0f}%")

        connected = snapshot.get("camera_connected", False)
        self.lbl_camera.configure(
            text="Connected" if connected else "Disconnected",
            fg=config.COLOR_ACCENT_GREEN if connected else config.COLOR_ACCENT_OFF,
        )
        self.lbl_mouse_status.configure(
            text="Active" if gesture_name not in ("No Hand", "None") else "Idle",
        )
        self.lbl_cpu.configure(text=f"{snapshot.get('cpu_usage', 0.0):.0f}%")

        enabled = snapshot.get("enabled", True)
        state_text = "Active" if enabled else "Disabled"
        state_color = config.COLOR_ACCENT_GREEN if enabled else config.COLOR_ACCENT_OFF
        self.lbl_state.configure(text=state_text, fg=state_color)
        self.toggle_btn.configure(text="Enable (F8)" if not enabled else "Disable (F9)")

    # ------------------------------------------------------------------
    def run(self):
        self.root.mainloop()

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def _now():
    import time
    return time.time()
