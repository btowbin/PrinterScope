from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from dataclasses import asdict, fields, replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

from .acquisition import AcquisitionRunner, AcquisitionSettings
from .autofocus import STANDARD_FOCUS_MEASURES
from .camera import DahengCamera, SimulatedCamera
from .display import auto_range, histogram_counts, reduce_for_display, to_uint8
from .image_io import (DEFAULT_JPEG2000_PSNR_DB, JPEG2000, JPEG2000_PSNR_RANGE_DB, TIFF_FORMATS,
                       UNCOMPRESSED, max_value, validate_psnr, write_tiff)
from .gamepad import (ButtonTracker, GamepadConfig, XInputBackend, actions_for,
                      next_speed_level, velocity_from_state)
from .jog import JogRunner
from . import session
from .models import (ImageROI, Position, PointsHistory, StagePoint, load_points, save_points,
                     update_points)
from .presets import PRESET_FIELDS, PRESET_NAMES, StepPreset, default_presets, with_field
from .motion import (MarlinMotionController, SimulatedMotionController, detect_printer,
                     printer_port_score, rank_printer_ports, serial_port_infos)
from .well_detection import WELL_DETECTORS


IMX178_MAX_WIDTH = 3088
IMX178_MAX_HEIGHT = 2064


class MicroscopeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CNC Microscope")
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"1260x{min(960, max(700, screen_height - 90))}")
        self.root.minsize(1100, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.motion = None
        self.camera = None
        self.runner: AcquisitionRunner | None = None
        self.points: list[StagePoint] = []
        self.points_history = PointsHistory()
        self.port_descriptions: dict[str, str] = {}
        self.detecting_port = False
        self.ui_queue: queue.Queue = queue.Queue()
        # Live acquisition may produce ~13 MB IMX178 frames faster than Tk can
        # draw them. Keep only the newest frame instead of allowing an
        # unbounded queue to consume memory and freeze/crash the application.
        self.live_frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self.preview_photo = None
        self.image_stats = tk.StringVar(value="Raw image: no frame received")
        self.last_image: np.ndarray | None = None
        self.camera_lock = threading.Lock()
        self.live_stop = threading.Event()
        self.live_thread: threading.Thread | None = None

        self.pad_backend = XInputBackend()
        self.pad_config = GamepadConfig()
        self.pad_tracker = ButtonTracker(self.pad_config)
        self.jog: JogRunner | None = None
        self.ui_motion_active = False   # a button-driven move is running on a worker thread
        self.focus_busy = False         # a gamepad autofocus scan is running

        self._make_variables()
        self._build_ui()
        self._apply_step_mode()
        self._focus_measure_changed()
        self._refresh_ports()
        self._restore_autosave()
        self._set_connected_state(False)
        for key, delta in (("<Up>", -1), ("<Left>", -1), ("<Down>", 1), ("<Right>", 1)):
            self.root.bind(key, lambda e, d=delta, k=key: self._points_key(e, d, k))
        self.root.after(50, self._drain_ui_queue)
        self.root.after(33, self._gamepad_poll)
        self.root.after(30000, self._autosave_timer)

    def _make_variables(self) -> None:
        self.simulation = tk.BooleanVar(value=True)
        self.gamepad_enabled = tk.BooleanVar(value=False)
        self.pad_status = tk.StringVar(value="Gamepad: off")
        self.port = tk.StringVar()
        self.baud = tk.StringVar(value="115200")
        self.status = tk.StringVar(value="Ready — simulation mode selected")
        self.position_text = tk.StringVar(value="X —    Y —    Z —")
        self.step_mode = tk.StringVar(value="Fine")
        self.presets = default_presets()
        self.rapid_xy_speed = tk.DoubleVar(value=100.0)
        self.rapid_z_speed = tk.DoubleVar(value=20.0)
        self.bit_depth = tk.IntVar(value=10)  # the Daheng IMX178 maximum
        self.auto_scale = tk.BooleanVar(value=True)
        self.display_range_text = tk.StringVar(value="Display range: —")
        self.display_min = 0.0
        self.display_max = 1023.0
        self._reduced = None
        self._hist_counts = None
        self._hist_drag_target = None
        self.tiff_format = tk.StringVar(value=UNCOMPRESSED)
        self.jpeg2000_psnr_db = tk.DoubleVar(value=DEFAULT_JPEG2000_PSNR_DB)
        self.step_summary = tk.StringVar(value="Active steps: X 0.1 mm, Y 0.1 mm, Z 0.01 mm")
        self.backlash_correction = tk.BooleanVar(value=True)
        self.xy_preload_mm = tk.DoubleVar(value=0.25)
        self.z_preload_mm = tk.DoubleVar(value=0.05)
        self.goto_x = tk.DoubleVar(value=0.0)
        self.goto_y = tk.DoubleVar(value=0.0)
        self.goto_z = tk.DoubleVar(value=0.0)
        self.exposure_ms = tk.DoubleVar(value=20.0)
        self.repeats = tk.IntVar(value=1)
        self.interval_s = tk.DoubleVar(value=60.0)
        self.autofocus = tk.BooleanVar(value=False)
        self.save_stack = tk.BooleanVar(value=False)
        self.z_range_um = tk.DoubleVar(value=100.0)
        self.z_spacing_um = tk.DoubleVar(value=10.0)
        self.z_range_label = tk.StringVar(value="Z range (µm)")
        self.z_spacing_label = tk.StringVar(value="Spacing (µm)")
        self.stagger_autofocus = tk.BooleanVar(value=False)
        self.stagger_n = tk.IntVar(value=3)
        self.two_step_autofocus = tk.BooleanVar(value=False)
        self.fine_range_um = tk.DoubleVar(value=20.0)
        self.fine_spacing_um = tk.DoubleVar(value=2.0)
        self.focus_measure_name = tk.StringVar(value="Tenengrad")
        self.custom_focus_path = tk.StringVar()
        self.center_well = tk.BooleanVar(value=False)
        self.well_detector_name = tk.StringVar(value="Gradient centroid")
        self.custom_well_path = tk.StringVar()
        self.well_x_um_per_pixel = tk.DoubleVar(value=1.0)
        self.well_y_um_per_pixel = tk.DoubleVar(value=1.0)
        self.invert_well_x = tk.BooleanVar(value=False)
        self.invert_well_y = tk.BooleanVar(value=False)
        self.max_center_correction_mm = tk.DoubleVar(value=2.0)
        self.roi_enabled = tk.BooleanVar(value=False)
        self.roi_width = tk.IntVar(value=0)
        self.roi_height = tk.IntVar(value=0)
        self.roi_summary = tk.StringVar(value="ROI: full 3088 × 2064 camera image")
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "images"))
        self.progress_text = tk.StringVar(value="0 / 0")
        # Variables remembered between sessions and stored in session files.
        names = ("simulation", "port", "baud", "gamepad_enabled", "step_mode",
                 "backlash_correction", "xy_preload_mm", "z_preload_mm", "rapid_xy_speed",
                 "rapid_z_speed", "exposure_ms", "repeats", "interval_s", "autofocus",
                 "save_stack", "z_range_um", "z_spacing_um", "two_step_autofocus",
                 "fine_range_um", "fine_spacing_um", "stagger_autofocus", "stagger_n",
                 "focus_measure_name",
                 "custom_focus_path", "center_well", "well_detector_name", "custom_well_path",
                 "well_x_um_per_pixel", "well_y_um_per_pixel", "invert_well_x",
                 "invert_well_y", "max_center_correction_mm", "roi_enabled", "roi_width",
                 "roi_height", "output_dir", "tiff_format", "jpeg2000_psnr_db", "auto_scale")
        self._persisted = {name: getattr(self, name) for name in names}
        self._last_autosave = None

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Position.TLabel", font=("Consolas", 13, "bold"))

        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Save session…", accelerator="Ctrl+S",
                              command=self._save_session)
        file_menu.add_command(label="Load session…", accelerator="Ctrl+O",
                              command=self._load_session)
        file_menu.add_separator()
        file_menu.add_command(label="Forget remembered settings",
                              command=self._forget_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)
        self.root.bind("<Control-s>", lambda _e: self._save_session())
        self.root.bind("<Control-o>", lambda _e: self._load_session())
        self.root.bind("<Control-z>", self._undo_key)

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CNC Microscope Control", style="Title.TLabel").pack(anchor="w")

        connection = ttk.LabelFrame(outer, text="Hardware", padding=8)
        connection.pack(fill="x", pady=(8, 8))
        ttk.Checkbutton(connection, text="Simulation (no hardware)", variable=self.simulation,
                        command=self._simulation_changed).grid(row=0, column=0, padx=(0, 18))
        ttk.Label(connection, text="Ender 3 port").grid(row=0, column=1)
        self.port_combo = ttk.Combobox(connection, textvariable=self.port, width=12, state="readonly")
        self.port_combo.grid(row=0, column=2, padx=5)
        port_buttons = ttk.Frame(connection)
        port_buttons.grid(row=0, column=3, padx=(0, 14))
        self.refresh_button = ttk.Button(port_buttons, text="Refresh", command=self._refresh_ports)
        self.refresh_button.pack(side="left")
        self.detect_button = ttk.Button(port_buttons, text="Auto-detect", command=self._autodetect_port)
        self.detect_button.pack(side="left", padx=(4, 0))
        ttk.Label(connection, text="Baud").grid(row=0, column=4)
        self.baud_combo = ttk.Combobox(connection, textvariable=self.baud, width=9,
                                       values=("115200", "250000"), state="readonly")
        self.baud_combo.grid(row=0, column=5, padx=5)
        self.connect_button = ttk.Button(connection, text="Connect", command=self._toggle_connection)
        self.connect_button.grid(row=0, column=6, padx=(12, 5))
        self.home_button = ttk.Button(connection, text="Home XYZ…", command=self._home)
        self.home_button.grid(row=0, column=7, padx=5)
        self.estop_button = tk.Button(connection, text="EMERGENCY STOP", bg="#b42318", fg="white",
                                      activebackground="#7a271a", activeforeground="white",
                                      font=("Segoe UI", 9, "bold"), command=self._emergency_stop)
        self.estop_button.grid(row=0, column=8, padx=(18, 0))

        ttk.Checkbutton(connection, text="Gamepad control", variable=self.gamepad_enabled).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(connection, textvariable=self.pad_status, foreground="#555555").grid(
            row=1, column=2, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Button(connection, text="Gamepad settings…", command=self._open_gamepad_settings).grid(
            row=1, column=7, columnspan=2, sticky="e", pady=(6, 0))

        main = ttk.Panedwindow(outer, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main, padding=(0, 0, 8, 0))
        right = ttk.Frame(main, padding=(8, 0, 0, 0))
        main.add(left, weight=3)
        main.add(right, weight=2)
        self._main_pane = main

        controls = self._make_scrollable(left)
        self._controls_inner = controls
        self._build_motion(controls)
        self._build_points(controls)
        self._build_experiment(controls)
        self._build_preview(right)

        self.root.after(150, self._fit_sash)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bottom, length=230, mode="determinate")
        self.progress.pack(side="left", padx=8)
        ttk.Label(bottom, textvariable=self.progress_text, width=10).pack(side="left")

    PREVIEW_MIN_WIDTH = 330  # at Tk scaling 1.0; multiplied by the display scaling

    def _fit_sash(self) -> None:
        """Give the controls column the width its widgets need (so nothing is cut off at any
        display scaling) and leave the rest to the preview; keep the preview usable."""
        try:
            self.root.update_idletasks()
            preview_min = int(self.PREVIEW_MIN_WIDTH * float(self.root.tk.call("tk", "scaling")))
            needed = self._controls_inner.winfo_reqwidth() + 16 + 24  # padding + scrollbar
            screen = self.root.winfo_screenwidth()
            want_total = needed + preview_min + 30
            width = self.root.winfo_width()
            if want_total > width and want_total <= screen:
                self.root.geometry(f"{want_total}x{self.root.winfo_height()}")
                self.root.update_idletasks()
            total = self._main_pane.winfo_width()
            self._main_pane.sashpos(0, max(300, min(needed, total - preview_min)))
        except tk.TclError:
            pass

    def _make_scrollable(self, parent) -> ttk.Frame:
        """A scrollable column, so the controls stay reachable on small screens and at high
        display scaling. It scrolls vertically, and horizontally when it is too narrow."""
        canvas = tk.Canvas(parent, highlightthickness=0)
        vbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(parent, orient="horizontal", command=canvas.xview)
        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_inner_resize(_event):
            canvas.configure(scrollregion=canvas.bbox("all"), width=inner.winfo_reqwidth())

        def on_canvas_resize(event):
            # Never squeeze the content below what its widgets need; scroll instead.
            canvas.itemconfigure(window, width=max(event.width, inner.winfo_reqwidth()))

        def on_xscroll(low, high):
            hbar.set(low, high)
            if float(low) <= 0.0 and float(high) >= 1.0:
                hbar.pack_forget()
            elif not hbar.winfo_ismapped():
                hbar.pack(side="bottom", fill="x", before=canvas)

        inner.bind("<Configure>", on_inner_resize)
        canvas.bind("<Configure>", on_canvas_resize)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=on_xscroll)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def on_wheel(event):
            if inner.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _build_motion(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Objective position", padding=10)
        box.pack(fill="x")
        ttk.Label(box, textvariable=self.position_text, style="Position.TLabel").grid(
            row=0, column=0, columnspan=5, pady=(0, 8), sticky="w")
        ttk.Button(box, text="Read position from printer", command=self._sync_position).grid(
            row=0, column=5, columnspan=2, pady=(0, 8), sticky="e")
        profiles = ttk.LabelFrame(
            box, text="Movement step preset (double-click a value to edit it)", padding=6)
        profiles.grid(row=1, column=0, columnspan=7, sticky="ew")
        radios = ttk.Frame(profiles)
        radios.pack(fill="x")
        for name in PRESET_NAMES:
            ttk.Radiobutton(radios, text=name, value=name, variable=self.step_mode,
                            command=self._apply_step_mode).pack(side="left", padx=(0, 16))
        columns = ("name",) + PRESET_FIELDS
        self.preset_tree = ttk.Treeview(profiles, columns=columns, show="headings",
                                        height=4, selectmode="browse")
        titles = {"name": "Preset", "x_step": "X step (mm)", "y_step": "Y step (mm)",
                  "z_step": "Z step (mm)", "xy_speed": "XY (mm/s)",
                  "z_speed": "Z (mm/s)"}
        for key in columns:
            self.preset_tree.heading(key, text=titles[key])
            self.preset_tree.column(key, width=95 if key == "name" else 105, anchor="center")
        self.preset_tree.pack(fill="x", pady=(4, 0))
        self.preset_tree.bind("<Double-1>", self._edit_preset_cell)
        self.preset_tree.bind("<<TreeviewSelect>>", self._preset_row_selected)
        self._preset_editor = None
        self._redraw_presets()
        ttk.Label(box, textvariable=self.step_summary, foreground="#555555").grid(
            row=2, column=0, columnspan=7, sticky="w", pady=(4, 0))

        pad = ttk.Frame(box)
        pad.grid(row=3, column=0, columnspan=7, pady=10, sticky="w")
        ttk.Button(pad, text="Y +", width=8, command=lambda: self._jog(dy=1)).grid(row=0, column=1)
        ttk.Button(pad, text="X −", width=8, command=lambda: self._jog(dx=-1)).grid(row=1, column=0)
        ttk.Button(pad, text="X +", width=8, command=lambda: self._jog(dx=1)).grid(row=1, column=2)
        ttk.Button(pad, text="Y −", width=8, command=lambda: self._jog(dy=-1)).grid(row=2, column=1)
        ttk.Button(pad, text="Z +  retract", width=12, command=lambda: self._jog(dz=1)).grid(
            row=0, column=4, padx=(22, 2))
        ttk.Button(pad, text="Z −  approach", width=12, command=lambda: self._jog(dz=-1)).grid(
            row=1, column=4, padx=(22, 2))

        go = ttk.Frame(box)
        go.grid(row=4, column=0, columnspan=7, sticky="w")
        for index, (label, variable) in enumerate(
                (("X", self.goto_x), ("Y", self.goto_y), ("Z", self.goto_z))):
            ttk.Label(go, text=label).grid(row=0, column=index * 2)
            ttk.Entry(go, textvariable=variable, width=9).grid(row=0, column=index * 2 + 1,
                                                               padx=(2, 10))
        ttk.Button(go, text="Move to position (fast)", command=self._go_to).grid(
            row=0, column=6, padx=4)
        for entry in go.winfo_children():
            if isinstance(entry, ttk.Entry):
                entry.bind("<Return>", lambda _e: self._go_to())
        ttk.Label(go, text="Rapid speed (mm/s):  XY").grid(
            row=1, column=0, columnspan=3, sticky="e", pady=(6, 0))
        ttk.Entry(go, textvariable=self.rapid_xy_speed, width=9).grid(
            row=1, column=3, padx=(2, 10), pady=(6, 0))
        ttk.Label(go, text="Z").grid(row=1, column=4, sticky="e", pady=(6, 0))
        ttk.Entry(go, textvariable=self.rapid_z_speed, width=9).grid(
            row=1, column=5, padx=(2, 10), pady=(6, 0))
        ttk.Label(go, text="Z first when moving up, XY first when moving down; "
                           "Marlin limits each axis to its configured maximum speed.",
                  foreground="#555555").grid(row=2, column=0, columnspan=7, sticky="w")

        backlash = ttk.Frame(box)
        backlash.grid(row=5, column=0, columnspan=7, sticky="w", pady=(9, 0))
        ttk.Checkbutton(backlash, text="Backlash correction", variable=self.backlash_correction).grid(
            row=0, column=0, padx=(0, 10))
        ttk.Label(backlash, text="XY preload (mm)").grid(row=0, column=1)
        ttk.Entry(backlash, textvariable=self.xy_preload_mm, width=7).grid(row=0, column=2, padx=(4, 12))
        ttk.Label(backlash, text="Z preload (mm)").grid(row=0, column=3)
        ttk.Entry(backlash, textvariable=self.z_preload_mm, width=7).grid(row=0, column=4, padx=(4, 0))

    def _build_points(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Positions of interest", padding=8)
        box.pack(fill="both", expand=True, pady=8)
        self.points_tree = ttk.Treeview(box, columns=("name", "x", "y", "z"), show="headings",
                                        height=5, selectmode="browse")
        widths = {"name": 145, "x": 85, "y": 85, "z": 85}
        for key, title in (("name", "Name"), ("x", "X (mm)"), ("y", "Y (mm)"), ("z", "Z (mm)")):
            self.points_tree.heading(key, text=title)
            self.points_tree.column(key, width=widths[key], anchor="center")
        self.points_tree.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(box)
        controls.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(controls, text="Add current", command=self._add_point).pack(fill="x", pady=2)
        navigate = ttk.Frame(controls)
        navigate.pack(fill="x", pady=2)
        ttk.Button(navigate, text="◀ Previous", width=10,
                   command=lambda: self._select_relative(-1)).pack(side="left", expand=True, fill="x")
        ttk.Button(navigate, text="Next ▶", width=10,
                   command=lambda: self._select_relative(1)).pack(side="left", expand=True,
                                                                  fill="x", padx=(4, 0))
        ttk.Button(controls, text="Move to", command=self._move_to_selected).pack(fill="x", pady=2)
        ttk.Button(controls, text="Update XYZ",
                   command=lambda: self._update_points("point_xyz")).pack(fill="x", pady=2)
        update = ttk.Menubutton(controls, text="Update from current ▾")
        menu = tk.Menu(update, tearoff=False)
        for item in (
                ("Selected position: set X, Y and Z", "point_xyz"),
                ("Selected position: set X and Y only", "point_xy"),
                ("Selected position: set Z only", "point_z"),
                None,
                ("All positions: shift X, Y and Z", "all_xyz"),
                ("All positions: shift X and Y only", "all_xy"),
                ("All positions: shift Z only", "all_z")):
            if item is None:
                menu.add_separator()
            else:
                menu.add_command(label=item[0], command=lambda m=item[1]: self._update_points(m))
        update["menu"] = menu
        update.pack(fill="x", pady=2)
        ttk.Button(controls, text="Remove", command=self._remove_point).pack(fill="x", pady=2)
        ttk.Button(controls, text="Clear all", command=self._clear_points).pack(fill="x", pady=2)
        self.undo_button = ttk.Button(controls, text="Undo", command=self._undo_points,
                                      state="disabled")
        self.undo_button.pack(fill="x", pady=2)
        ttk.Separator(controls).pack(fill="x", pady=7)
        ttk.Button(controls, text="Save list…", command=self._save_point_list).pack(fill="x", pady=2)
        ttk.Button(controls, text="Load list…", command=self._load_point_list).pack(fill="x", pady=2)

    def _build_experiment(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Time-lapse acquisition", padding=10)
        box.pack(fill="x")
        fields = (("Repeats", self.repeats), ("Interval (s)", self.interval_s),
                  ("Exposure (ms)", self.exposure_ms))
        for col, (label, variable) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=0, column=col * 2, sticky="e", padx=(0, 4))
            ttk.Entry(box, textvariable=variable, width=9).grid(row=0, column=col * 2 + 1,
                                                                padx=(0, 14))
        ttk.Checkbutton(box, text="Autofocus", variable=self.autofocus).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Checkbutton(box, text="Save Z-stack", variable=self.save_stack).grid(
            row=1, column=3, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Label(box, textvariable=self.z_range_label).grid(row=2, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.z_range_um, width=9).grid(row=2, column=1, sticky="w")
        ttk.Label(box, textvariable=self.z_spacing_label).grid(row=2, column=2, sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.z_spacing_um, width=9).grid(row=2, column=3, sticky="w")
        ttk.Checkbutton(box, text="Center well in XY after Z AF", variable=self.center_well,
                        command=self._center_well_changed).grid(
            row=2, column=4, columnspan=2, sticky="w", padx=(12, 0))
        ttk.Checkbutton(box, text="Two-step autofocus", variable=self.two_step_autofocus,
                        command=self._two_step_changed).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        # The fine fields mirror the coarse "range / spacing" row, in the same narrow columns.
        ttk.Label(box, text="Fine range (µm)").grid(row=4, column=0, sticky="e", padx=(0, 4))
        fine_range = ttk.Entry(box, textvariable=self.fine_range_um, width=9)
        fine_range.grid(row=4, column=1, sticky="w")
        ttk.Label(box, text="Fine spacing (µm)").grid(row=4, column=2, sticky="e", padx=(0, 4))
        fine_spacing = ttk.Entry(box, textvariable=self.fine_spacing_um, width=9)
        fine_spacing.grid(row=4, column=3, sticky="w")
        self.fine_entries = (fine_range, fine_spacing)
        self._two_step_changed()
        ttk.Checkbutton(box, text="Staggered autofocus", variable=self.stagger_autofocus,
                        command=self._stagger_changed).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(box, text="every n-th position, n").grid(
            row=5, column=2, sticky="e", padx=(0, 4), pady=(6, 0))
        self.stagger_entry = ttk.Entry(box, textvariable=self.stagger_n, width=9)
        self.stagger_entry.grid(row=5, column=3, sticky="w", pady=(6, 0))
        self._stagger_changed()
        ttk.Label(box, text="Focus measure").grid(row=6, column=0, pady=(8, 0), sticky="e", padx=(0, 4))
        self.focus_measure_combo = ttk.Combobox(
            box, textvariable=self.focus_measure_name, values=STANDARD_FOCUS_MEASURES,
            state="readonly", width=25)
        self.focus_measure_combo.grid(row=6, column=1, columnspan=2, pady=(8, 0), sticky="w")
        self.focus_measure_combo.bind("<<ComboboxSelected>>", self._focus_measure_changed)
        ttk.Button(box, text="Well centering settings…", command=self._open_well_settings).grid(
            row=6, column=4, columnspan=2, pady=(8, 0), sticky="e")

        ttk.Label(box, text="Custom file").grid(row=7, column=0, pady=(5, 0), sticky="e", padx=(0, 4))
        self.custom_focus_entry = ttk.Entry(box, textvariable=self.custom_focus_path)
        self.custom_focus_entry.grid(row=7, column=1, columnspan=4, pady=(5, 0), sticky="ew")
        self.custom_focus_button = ttk.Button(box, text="Choose Python file…",
                                              command=self._browse_custom_focus)
        self.custom_focus_button.grid(row=7, column=5, pady=(5, 0))

        ttk.Label(box, text="Output folder").grid(row=8, column=0, pady=(8, 0), sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.output_dir).grid(row=8, column=1, columnspan=4,
                                                          pady=(8, 0), sticky="ew")
        ttk.Button(box, text="Browse…", command=self._browse_output).grid(row=8, column=5, pady=(8, 0))
        box.columnconfigure(4, weight=1)
        ttk.Label(box, text="Image format").grid(row=9, column=0, pady=(8, 0), sticky="e", padx=(0, 4))
        format_combo = ttk.Combobox(box, textvariable=self.tiff_format, values=TIFF_FORMATS,
                                    state="readonly", width=32)
        format_combo.grid(row=9, column=1, columnspan=4, pady=(8, 0), sticky="w")
        format_combo.bind("<<ComboboxSelected>>", lambda _e: self._format_changed())
        ttk.Label(box, text="JPEG 2000 quality (dB)").grid(
            row=10, column=0, pady=(5, 0), sticky="e", padx=(0, 4))
        self.jpeg2000_entry = ttk.Entry(box, textvariable=self.jpeg2000_psnr_db, width=8)
        self.jpeg2000_entry.grid(row=10, column=1, pady=(5, 0), sticky="w")
        ttk.Label(box, foreground="#555555",
                  text="Lossy: higher = better, larger files. 50 dB ≈ 3 counts RMS error at 10 bit."
                  ).grid(row=10, column=2, columnspan=4, pady=(5, 0), sticky="w", padx=(8, 0))
        self._format_changed()
        buttons = ttk.Frame(box)
        buttons.grid(row=11, column=0, columnspan=6, sticky="e", pady=(10, 0))
        self.stop_button = ttk.Button(buttons, text="Stop after current action", command=self._stop,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.start_button = ttk.Button(buttons, text="Start acquisition", command=self._start)
        self.start_button.pack(side="left")

    def _build_preview(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Latest image", padding=8)
        box.pack(fill="both", expand=True)
        self.preview = ttk.Label(box, text="No image captured", anchor="center")
        self.preview.pack(fill="both", expand=True)
        self.hist_canvas = tk.Canvas(box, height=90, bg="#1e1e1e", highlightthickness=0,
                                     cursor="sb_h_double_arrow")
        self.hist_canvas.pack(fill="x", pady=(6, 0))
        self.hist_canvas.bind("<Configure>", lambda _e: self._redraw_histogram())
        self.hist_canvas.bind("<ButtonPress-1>", self._hist_press)
        self.hist_canvas.bind("<B1-Motion>", self._hist_drag)
        display_row = ttk.Frame(box)
        display_row.pack(fill="x")
        ttk.Checkbutton(display_row, text="Autoscale", variable=self.auto_scale,
                        command=self._render_preview).pack(side="left")
        ttk.Button(display_row, text="Full range", command=self._full_display_range).pack(
            side="left", padx=8)
        ttk.Label(display_row, textvariable=self.display_range_text,
                  foreground="#555555").pack(side="left")
        ttk.Label(box, textvariable=self.image_stats, wraplength=400, justify="left").pack(
            fill="x", pady=(6, 0))
        camera_controls = ttk.Frame(box)
        camera_controls.pack(fill="x", pady=(8, 0))
        self.live_button = ttk.Button(camera_controls, text="Start live view",
                                      command=self._toggle_live)
        self.live_button.grid(row=0, column=0, padx=(0, 5), pady=2, sticky="ew")
        self.capture_button = ttk.Button(camera_controls, text="Capture & save snapshot",
                                         command=self._capture_snapshot)
        self.capture_button.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        self.save_view_button = ttk.Button(camera_controls, text="Save current view",
                                           command=self._save_current_view)
        self.save_view_button.grid(row=1, column=0, padx=(0, 5), pady=2, sticky="ew")
        self.roi_button = ttk.Button(camera_controls, text="Camera ROI settings…",
                                     command=self._open_roi_settings)
        self.roi_button.grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        depth = ttk.Frame(camera_controls)
        depth.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(depth, text="Camera bit depth: 10-bit (sensor maximum)",
                  foreground="#555555").pack(side="left")
        ttk.Label(camera_controls, textvariable=self.roi_summary, foreground="#555555").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))
        camera_controls.columnconfigure(0, weight=1)
        camera_controls.columnconfigure(1, weight=1)
        ttk.Label(parent, text=("Images contain 10-bit data stored in a 16-bit TIFF container.\n"
                                "Z+ retracts the objective; Z− approaches the sample."),
                  foreground="#555555", justify="left").pack(fill="x", pady=(8, 0))

    def _simulation_changed(self) -> None:
        if self.motion and self.motion.is_connected:
            self.simulation.set(isinstance(self.motion, SimulatedMotionController))
            messagebox.showinfo("Already connected", "Disconnect before changing hardware mode.")
            return
        self._update_hardware_fields()
        self.status.set("Hardware mode selected" if not self.simulation.get() else "Simulation mode selected")

    def _update_hardware_fields(self) -> None:
        enabled = not self.simulation.get()
        self.port_combo.configure(state="readonly" if enabled else "disabled")
        self.refresh_button.configure(state="normal" if enabled else "disabled")
        self.detect_button.configure(
            state="normal" if enabled and not self.detecting_port else "disabled")
        self.baud_combo.configure(state="readonly" if enabled else "disabled")

    def _refresh_ports(self) -> None:
        infos = serial_port_infos()
        ports = [info.device for info in infos]
        self.port_descriptions = {info.device: info.description for info in infos}
        self.port_combo["values"] = ports
        if ports and self.port.get() not in ports:
            ranked = [info for info in rank_printer_ports(infos) if printer_port_score(info) > 0]
            self.port.set(ranked[0].device if ranked else ports[0])
        self._update_hardware_fields()

    def _autodetect_port(self) -> None:
        """Find the Ender 3: ask each likely USB serial port for Marlin's firmware reply."""
        if self.detecting_port:
            return
        if self.motion and self.motion.is_connected:
            messagebox.showinfo("Already connected", "Disconnect before searching for the printer.")
            return
        self._refresh_ports()
        infos = serial_port_infos()
        if not infos:
            self.status.set("No serial ports found — is the printer plugged in and switched on?")
            messagebox.showinfo("No serial ports",
                                "No serial ports were found.\n\nCheck the USB cable and that the "
                                "printer is switched on, then try again.")
            return
        self.detecting_port = True
        self.detect_button.configure(state="disabled")
        self.status.set("Searching for the printer…")

        def work():
            try:
                found = detect_printer(
                    infos, progress=lambda text: self.ui_queue.put(("status", text)))
            except Exception as exc:
                self.ui_queue.put(("port_detected", None, f"{type(exc).__name__}: {exc}"))
                return
            self.ui_queue.put(("port_detected", found, ""))

        threading.Thread(target=work, daemon=True).start()

    def _port_detected(self, found, error: str) -> None:
        self.detecting_port = False
        self._update_hardware_fields()
        if found:
            port, baud = found
            self.port.set(port)
            self.baud.set(str(baud))
            description = self.port_descriptions.get(port, "")
            self.status.set(f"Printer found on {port} at {baud} baud"
                            + (f" ({description})" if description else ""))
        else:
            self.status.set("No Marlin printer answered" + (f": {error}" if error else ""))
            messagebox.showwarning(
                "Printer not found",
                "No port answered like a Marlin printer.\n\nCheck that the printer is switched on, "
                "the USB cable is a data cable, no other program (Cura, Pronterface…) is using the "
                "port, and the CH340 driver is installed. You can also pick the port by hand.")

    def _toggle_connection(self) -> None:
        if self.motion and self.motion.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        if not self.simulation.get() and not self.port.get():
            messagebox.showerror("No serial port", "Select the Ender 3 serial port first.")
            return
        use_simulation = bool(self.simulation.get())
        selected_port = self.port.get()
        selected_baud = int(self.baud.get())
        selected_bits = int(self.bit_depth.get())

        def work():
            motion = (SimulatedMotionController() if use_simulation else
                      MarlinMotionController(selected_port, selected_baud))
            camera = (SimulatedCamera(lambda: motion.position.z, bit_depth=selected_bits)
                      if use_simulation else DahengCamera(bit_depth=selected_bits))
            try:
                motion.connect()
                camera.connect()
            except Exception:
                try:
                    motion.disconnect()
                except Exception:
                    pass
                raise
            self.motion, self.camera = motion, camera
            self.ui_queue.put(("connected",))

        self.status.set("Connecting…")
        self._thread(work)

    def _disconnect(self) -> None:
        if self.runner:
            messagebox.showwarning("Acquisition running", "Stop the acquisition before disconnecting.")
            return
        if not self._stop_live(wait=True):
            return
        if self.jog:
            self.jog.stop(wait=True)
            self.jog = None
        try:
            if self.camera:
                self.camera.disconnect()
            if self.motion:
                self.motion.disconnect()
        except Exception as exc:
            messagebox.showerror("Disconnect error", str(exc))
        self.motion = self.camera = None
        self._set_connected_state(False)
        self.status.set("Disconnected")

    def _home(self) -> None:
        if not self._require_connection():
            return
        warning = ("Is the objective fully retracted and is the complete XYZ homing path clear?\n\n"
                   "Ender 3 homing moves Z toward its end-stop. Keep a hand near the power switch "
                   "during first setup.")
        if not messagebox.askyesno("Confirm safe homing", warning, icon="warning"):
            return

        def work():
            self.motion.home()
            self.ui_queue.put(("position", self.motion.position))
            self.ui_queue.put(("status", "Homing complete"))

        if self._gamepad_motion_busy():
            return
        self.status.set("Homing XYZ…")
        self._thread_motion(work)

    # ---- remembered settings and sessions -----------------------------------
    HARDWARE_KEYS = ("simulation", "port", "baud")

    def _allowed_values(self) -> dict:
        return {"step_mode": PRESET_NAMES, "tiff_format": TIFF_FORMATS,
                "focus_measure_name": STANDARD_FOCUS_MEASURES,
                "well_detector_name": WELL_DETECTORS, "baud": ("115200", "250000"),
                "port": tuple(self.port_combo["values"])}

    def _session_data(self) -> dict:
        settings = {}
        for name, variable in self._persisted.items():
            try:
                settings[name] = variable.get()
            except (tk.TclError, ValueError):
                pass  # half-typed number: keep the previous saved value out of the file
        return {
            "saved": datetime.now().isoformat(timespec="seconds"),
            "settings": settings,
            "presets": {name: asdict(preset) for name, preset in self.presets.items()},
            "gamepad": asdict(self.pad_config),
            "display": {"min": self.display_min, "max": self.display_max},
            "points": [asdict(point) for point in self.points],
        }

    @staticmethod
    def _set_variable(variable, value) -> bool:
        try:
            previous = variable.get()
        except (tk.TclError, ValueError):
            previous = None
        try:
            variable.set(value)
            variable.get()  # a value of the wrong type only fails when read back
            return True
        except (tk.TclError, ValueError, TypeError):
            if previous is not None:
                variable.set(previous)
            return False

    def _apply_session(self, data: dict, include_hardware: bool) -> list[str]:
        """Apply a session dict; returns the names of entries that were ignored."""
        skipped: list[str] = []
        allowed = self._allowed_values()
        for name, value in (data.get("settings") or {}).items():
            variable = self._persisted.get(name)
            if variable is None or (name in self.HARDWARE_KEYS and not include_hardware):
                continue
            if name in allowed and str(value) not in {str(a) for a in allowed[name]}:
                if name != "port":
                    skipped.append(name)
                continue  # e.g. a serial port that is not present any more
            if not self._set_variable(variable, value):
                skipped.append(name)
        for name, values in (data.get("presets") or {}).items():
            try:
                preset = StepPreset(**{key: float(values[key]) for key in PRESET_FIELDS})
                preset.validate()
                if name in PRESET_NAMES:
                    self.presets[name] = preset
            except (KeyError, TypeError, ValueError):
                skipped.append(f"preset {name}")
        if isinstance(data.get("gamepad"), dict):
            try:
                known = {f.name for f in fields(GamepadConfig)}
                config = GamepadConfig(**{k: v for k, v in data["gamepad"].items() if k in known})
                config.validate()
                self.pad_config = config
                self.pad_tracker.config = config
            except (TypeError, ValueError):
                skipped.append("gamepad settings")
        if "points" in data:
            try:
                loaded = [StagePoint(str(p["name"]), float(p["x"]), float(p["y"]), float(p["z"]))
                          for p in data["points"]]
                if self.points != loaded:
                    self._record_points("load session")
                self.points = loaded
            except (KeyError, TypeError, ValueError):
                skipped.append("positions")
        display = data.get("display") or {}
        try:
            low, high = float(display["min"]), float(display["max"])
            if 0 <= low < high:
                self.display_min, self.display_max = low, high
        except (KeyError, TypeError, ValueError):
            pass
        # Refresh everything that is derived from the restored values.
        self._redraw_presets()
        self._apply_step_mode()
        self._redraw_points()
        self._focus_measure_changed()
        self._two_step_changed()
        self._stagger_changed()
        self._format_changed()
        self._well_detector_changed()
        self._update_hardware_fields()
        try:
            self._refresh_roi_summary(self._current_roi())
        except (ValueError, tk.TclError):
            pass
        self._render_preview()
        return skipped

    def _restore_autosave(self) -> None:
        path = session.autosave_path()
        if not path.exists():
            return
        try:
            skipped = self._apply_session(session.read_session(path), include_hardware=True)
        except (OSError, ValueError) as exc:
            self.status.set(f"Could not restore the previous settings: {exc}")
            return
        self.points_history.clear()  # restoring at start-up is not an undoable edit
        self._update_undo_button()
        note = f" ({len(skipped)} entries ignored)" if skipped else ""
        self.status.set(f"Restored settings from the previous session{note}")

    def _autosave(self) -> None:
        try:
            data = self._session_data()
            fingerprint = (data["settings"], data["presets"], data["gamepad"], data["points"],
                           data["display"])
            if fingerprint == self._last_autosave:
                return
            session.write_session(session.autosave_path(), data)
            self._last_autosave = fingerprint
        except Exception:
            pass  # remembering settings must never get in the way of the experiment

    def _autosave_timer(self) -> None:
        self._autosave()
        if self.root.winfo_exists():
            self.root.after(30000, self._autosave_timer)

    def _save_session(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save session", defaultextension=".json", initialfile="microscope_session.json",
            filetypes=(("Microscope session", "*.json"),))
        if not path:
            return
        try:
            session.write_session(Path(path), self._session_data())
        except OSError as exc:
            messagebox.showerror("Could not save the session", str(exc))
            return
        self.status.set(f"Session saved: {Path(path).name}")

    def _load_session(self) -> None:
        if self.runner:
            messagebox.showinfo("Acquisition running", "Load a session after the acquisition ends.")
            return
        path = filedialog.askopenfilename(title="Load session",
                                          filetypes=(("Microscope session", "*.json"),))
        if not path:
            return
        try:
            data = session.read_session(Path(path))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not load the session", str(exc))
            return
        if not messagebox.askyesno(
                "Load session",
                "Replace the current settings and positions with this session?"):
            return
        connected = bool(self.motion and self.motion.is_connected)
        skipped = self._apply_session(data, include_hardware=not connected)
        note = " Hardware mode and port were kept because the stage is connected." if connected else ""
        extra = f" Ignored: {', '.join(skipped)}." if skipped else ""
        self.status.set(f"Session loaded: {Path(path).name}.{note}{extra}")

    def _forget_settings(self) -> None:
        if not messagebox.askyesno(
                "Forget remembered settings",
                "Delete the remembered settings? The next start will use the defaults."):
            return
        try:
            session.autosave_path().unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror("Could not delete", str(exc))
            return
        self._last_autosave = None
        self.status.set("Remembered settings deleted; defaults apply from the next start")

    # ---- gamepad ---------------------------------------------------------------
    STEP_ACTIONS = {"xy_left": (-1, 0, 0), "xy_right": (1, 0, 0), "xy_up": (0, 1, 0),
                    "xy_down": (0, -1, 0), "z_down": (0, 0, -1), "z_up": (0, 0, 1)}

    def _start_jog_runner(self) -> None:
        if self.jog:
            self.jog.stop(wait=True)
        self.jog = JogRunner(
            lambda: self.motion,
            z_floor=lambda: self.pad_config.z_floor_mm,
            blocked=lambda: self.ui_motion_active or self.runner is not None,
            on_position=lambda p: self.ui_queue.put(("position", p)),
            on_error=self._gamepad_error,
        )
        self.jog.start()

    def _gamepad_error(self, exc: Exception) -> None:
        if isinstance(exc, ValueError):  # e.g. a travel limit: not worth a modal dialog
            self.ui_queue.put(("status", f"Gamepad: {exc}"))
        else:
            self.ui_queue.put(("error", type(exc).__name__, str(exc)))

    def _gamepad_ready(self) -> bool:
        return bool(self.jog and self.motion and self.motion.is_connected and self.motion.is_homed
                    and self.runner is None and not self.focus_busy
                    and not self.ui_motion_active)

    def _gamepad_poll(self) -> None:
        try:
            self._gamepad_tick()
        except Exception as exc:  # never let the poll loop die
            self.pad_status.set(f"Gamepad error: {type(exc).__name__}: {exc}")
        finally:
            if self.root.winfo_exists():
                self.root.after(33, self._gamepad_poll)

    def _gamepad_tick(self) -> None:
        if not self.gamepad_enabled.get():
            if self.jog:
                self.jog.set_velocity(0.0, 0.0, 0.0)
            self.pad_tracker.reset()
            self.pad_status.set("Gamepad: off")
            return
        if not self.pad_backend.available:
            self.pad_status.set("Gamepad: needs Windows (XInput)")
            return
        state = self.pad_backend.read()
        if state is None:
            if self.jog:
                self.jog.set_velocity(0.0, 0.0, 0.0)
            self.pad_tracker.reset()
            self.pad_status.set("Gamepad: not connected — plug in the controller")
            return
        events = self.pad_tracker.update(state.buttons, time.monotonic())
        ready = self._gamepad_ready()
        for action in actions_for(events, self.pad_config.mapping):
            self._gamepad_action(action, ready)
        velocity = (0.0, 0.0, 0.0)
        if ready:
            preset = self._active_preset()
            velocity = velocity_from_state(state, self.pad_config, preset.xy_speed, preset.z_speed)
        if self.jog:
            self.jog.set_velocity(*velocity)
        suffix = "" if ready else " (idle: connect and home first)"
        self.pad_status.set(f"Gamepad: connected — speed level {self.step_mode.get()}{suffix}")

    def _gamepad_action(self, action: str, ready: bool) -> None:
        if action == "speed":
            level = next_speed_level(self.step_mode.get())
            self.step_mode.set(level)
            self._apply_step_mode()
            self.status.set(f"Speed level: {level}")
            return
        if not ready:
            return
        if action in self.STEP_ACTIONS:
            self._gamepad_step(*self.STEP_ACTIONS[action])
        elif action == "add_point":
            self._add_point()
        elif action == "capture":
            if self.camera and self.camera.is_connected:
                self._capture_snapshot()
        elif action == "autofocus":
            self._gamepad_autofocus()

    def _gamepad_step(self, sx: int, sy: int, sz: int) -> None:
        preset = self._active_preset()
        dx, dy, dz = sx * preset.x_step, sy * preset.y_step, sz * preset.z_step
        feed = self._movement_feed(z_axis=bool(sz))
        floor = self.pad_config.z_floor_mm

        def task():
            p = self.motion.position
            if dz < 0 and p.z + dz < floor - 1e-9:
                raise ValueError(f"Z floor of {floor:g} mm reached")
            self.motion.move_by(dx, dy, dz, feed_mm_min=feed)

        self.jog.submit(task)

    def _gamepad_autofocus(self) -> None:
        if not self.camera or not self.camera.is_connected:
            self.status.set("Gamepad: connect the camera for autofocus")
            return
        try:
            settings = replace(self._build_settings(), autofocus=True, center_well=False)
        except (ValueError, tk.TclError) as exc:
            self.status.set(f"Gamepad autofocus: {exc}")
            return
        was_live = bool(self.live_thread and self.live_thread.is_alive())
        if not self._stop_live(wait=True):
            return
        self.focus_busy = True
        runner = AcquisitionRunner(self.motion, self.camera)

        def callback(message, image, _completed, _total):
            self.ui_queue.put(("acquisition_progress", message, image, 0, 0))

        def task():
            try:
                best = runner.autofocus_here(settings, callback)
                self.ui_queue.put(("status", f"Autofocus complete: Z {best.z:.4f} mm"))
            finally:
                self.ui_queue.put(("autofocus_done", was_live))

        self.status.set("Autofocus…")
        self.jog.submit(task)

    def _open_gamepad_settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Gamepad settings")
        window.transient(self.root)
        window.resizable(False, False)
        cfg = self.pad_config
        fields = (("Stick deadzone (0–0.9)", tk.DoubleVar(value=cfg.deadzone), "deadzone"),
                  ("Response curve exponent (1 = linear, 2 = gentle)", tk.DoubleVar(value=cfg.exponent), "exponent"),
                  ("Z stick speed (fraction of preset Z speed)", tk.DoubleVar(value=cfg.z_speed_scale), "z_speed_scale"),
                  ("Lowest Z the gamepad may reach (mm)", tk.DoubleVar(value=cfg.z_floor_mm), "z_floor_mm"))
        flags = (("Invert X", tk.BooleanVar(value=cfg.invert_x), "invert_x"),
                 ("Invert Y", tk.BooleanVar(value=cfg.invert_y), "invert_y"),
                 ("Invert Z", tk.BooleanVar(value=cfg.invert_z), "invert_z"))
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        for row, (label, variable, _name) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 10))
            ttk.Entry(frame, textvariable=variable, width=8).grid(row=row, column=1, pady=2)
        flag_row = ttk.Frame(frame)
        flag_row.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(6, 0))
        for label, variable, _name in flags:
            ttk.Checkbutton(flag_row, text=label, variable=variable).pack(side="left", padx=(0, 12))
        legend = ("Left stick: XY (push harder = faster)    Right stick up/down: Z (slower)\n"
                  "D-pad: one XY step    LB / RB: one Z step down / up\n"
                  "A: add position    B: autofocus    X: capture    Y: cycle speed level\n"
                  "Speed level = Coarse / Fine / Super fine preset (speeds and steps).")
        ttk.Label(frame, text=legend, foreground="#555555", justify="left").grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def apply() -> None:
            try:
                updated = replace(cfg, **{name: float(var.get()) for _l, var, name in fields},
                                  **{name: bool(var.get()) for _l, var, name in flags})
                updated.validate()
            except (ValueError, tk.TclError) as exc:
                messagebox.showerror("Invalid gamepad setting", str(exc), parent=window)
                return
            self.pad_config = updated
            self.pad_tracker.config = updated
            window.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Apply", command=apply).pack(side="left")

    def _sync_position(self) -> None:
        if not self._require_connection():
            return

        def work():
            position = self.motion.sync_position()
            self.ui_queue.put(("position", position))
            self.ui_queue.put(("status", "Position read from the printer"))

        self.status.set("Reading position from the printer…")
        self._thread(work)

    def _jog(self, dx=0, dy=0, dz=0) -> None:
        preset = self._active_preset()
        try:
            preset.validate()
        except ValueError as exc:
            messagebox.showerror("Invalid movement preset", str(exc))
            return
        feed = self._movement_feed(z_axis=bool(dz))
        self._motion_action(lambda: self.motion.move_by(
            dx * preset.x_step, dy * preset.y_step, dz * preset.z_step, feed_mm_min=feed),
                            "Moving objective…")

    def _go_to(self) -> None:
        try:
            target = Position(float(self.goto_x.get()), float(self.goto_y.get()), float(self.goto_z.get()))
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid position", "Enter numeric X, Y and Z coordinates.")
            return
        try:
            xy_speed, z_speed = float(self.rapid_xy_speed.get()), float(self.rapid_z_speed.get())
            if xy_speed <= 0 or z_speed <= 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid speed", "Rapid XY and Z speeds must be greater than zero.")
            return
        self._motion_action(lambda: self.motion.reposition(target, 60.0 * xy_speed, 60.0 * z_speed),
                            "Moving to absolute position…")

    def _thread_motion(self, function) -> None:
        """Run a button-driven move on a worker thread, flagged so the gamepad stands aside."""
        def guarded():
            self.ui_motion_active = True
            try:
                function()
            finally:
                self.ui_motion_active = False
        self._thread(guarded)

    def _gamepad_motion_busy(self) -> bool:
        if self.jog and self.jog.is_busy():
            self.status.set("Gamepad motion in progress — release the stick and try again")
            return True
        return False

    def _motion_action(self, action, description: str) -> None:
        if not self._require_homed() or self._gamepad_motion_busy():
            return

        def work():
            action()
            self.ui_queue.put(("position", self.motion.position))
            self.ui_queue.put(("status", "Move complete"))

        self.status.set(description)
        self._thread_motion(work)

    def _active_preset(self):
        return self.presets[self.step_mode.get()]

    def _redraw_presets(self) -> None:
        self.preset_tree.delete(*self.preset_tree.get_children())
        for name in PRESET_NAMES:
            preset = self.presets[name]
            self.preset_tree.insert("", "end", iid=name, values=(
                name, f"{preset.x_step:g}", f"{preset.y_step:g}", f"{preset.z_step:g}",
                f"{preset.xy_speed:g}", f"{preset.z_speed:g}"))
        if self.step_mode.get() in PRESET_NAMES:
            self.preset_tree.selection_set(self.step_mode.get())

    def _apply_step_mode(self) -> None:
        mode = self.step_mode.get()
        if self.preset_tree.selection() != (mode,):
            self.preset_tree.selection_set(mode)
        self._update_step_summary()

    def _preset_row_selected(self, _event=None) -> None:
        selected = self.preset_tree.selection()
        if selected and selected[0] != self.step_mode.get():
            self.step_mode.set(selected[0])
            self._update_step_summary()

    def _edit_preset_cell(self, event) -> None:
        tree = self.preset_tree
        if tree.identify_region(event.x, event.y) != "cell":
            return
        row = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        column_index = int(column_id[1:]) - 1
        if not row or column_index == 0:
            return  # the preset name is not editable
        field = PRESET_FIELDS[column_index - 1]
        self._close_preset_editor(commit=False)
        x, y, width, height = tree.bbox(row, column_id)
        editor = ttk.Entry(tree, justify="center")
        editor.insert(0, f"{getattr(self.presets[row], field):g}")
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.bind("<Return>", lambda _e: self._close_preset_editor(commit=True))
        editor.bind("<Escape>", lambda _e: self._close_preset_editor(commit=False))
        editor.bind("<FocusOut>", lambda _e: self._close_preset_editor(commit=True))
        self._preset_editor = (editor, row, field)

    def _close_preset_editor(self, commit: bool) -> None:
        if not self._preset_editor:
            return
        editor, row, field = self._preset_editor
        self._preset_editor = None
        text = editor.get()
        editor.destroy()
        if not commit:
            return
        try:
            self.presets[row] = with_field(self.presets[row], field, float(text))
        except ValueError as exc:
            messagebox.showerror("Invalid preset value",
                                 f"{exc}\n\nEnter a number greater than zero.")
            return
        self._redraw_presets()
        self._update_step_summary()

    def _update_step_summary(self) -> None:
        preset = self._active_preset()
        self.step_summary.set(
            f"Active ({self.step_mode.get()}): X {preset.x_step:g} mm, Y {preset.y_step:g} mm, "
            f"Z {preset.z_step:g} mm; speeds XY {preset.xy_speed:g}, Z {preset.z_speed:g} mm/s"
        )

    def _movement_feed(self, z_axis: bool) -> float:
        preset = self._active_preset()
        # Presets are in mm/s; the motion layer (Marlin F word) uses mm/min.
        return 60.0 * (preset.z_speed if z_axis else preset.xy_speed)

    def _backlash_values(self) -> tuple[bool, float, float]:
        enabled = bool(self.backlash_correction.get())
        xy = float(self.xy_preload_mm.get())
        z = float(self.z_preload_mm.get())
        if xy < 0 or z < 0:
            raise ValueError("Backlash preload distances cannot be negative")
        return enabled, xy, z

    def _approach_target(self, target: Position, backlash: tuple[bool, float, float]) -> None:
        # Backlash settings come from Tk variables and must be read on the UI
        # thread by the caller; this runs on a worker thread.
        enabled, xy, z = backlash
        if enabled:
            self.motion.approach_to(target, xy, z)
        else:
            self.motion.move_to(target)

    # ---- undo for the positions list ------------------------------------------
    def _record_points(self, label: str) -> None:
        """Call before every change to the list, so the change can be undone."""
        self.points_history.record(label, self.points)
        self._update_undo_button()

    def _update_undo_button(self) -> None:
        self.undo_button.configure(
            state="normal" if self.points_history.can_undo() else "disabled")

    def _undo_points(self) -> None:
        if not self.points_history.can_undo():
            return
        label, previous = self.points_history.undo()
        selected = self.points_tree.selection()
        self.points = previous
        index = int(selected[0]) if selected else None
        self._redraw_points(select=index)
        self._update_undo_button()
        remaining = self.points_history.next_label()
        self.status.set(f"Undid: {label}" + (f" (next undo: {remaining})" if remaining else ""))

    def _undo_key(self, _event):
        widget = self.root.focus_get()
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox, ttk.Spinbox, tk.Text)):
            return None  # Ctrl+Z belongs to the text field
        self._undo_points()
        return "break"

    def _add_point(self) -> None:
        if not self._require_homed():
            return
        p = self.motion.position
        self._record_points("add position")
        point = StagePoint(f"Point {len(self.points) + 1}", p.x, p.y, p.z)
        self.points.append(point)
        self._redraw_points()

    def _selected_point(self) -> StagePoint | None:
        selected = self.points_tree.selection()
        if not selected:
            messagebox.showinfo("Select a position", "Select a position of interest first.")
            return None
        return self.points[int(selected[0])]

    def _move_to_selected(self) -> None:
        point = self._selected_point()
        if point:
            try:
                backlash = self._backlash_values()
            except (ValueError, tk.TclError) as exc:
                messagebox.showerror("Invalid backlash settings", str(exc))
                return
            self._motion_action(lambda: self._approach_target(point.position, backlash),
                                f"Moving to {point.name}…")

    def _remove_point(self) -> None:
        selected = self.points_tree.selection()
        if selected:
            self._record_points("remove position")
            del self.points[int(selected[0])]
            for index, point in enumerate(self.points, 1):
                point.name = f"Point {index}"
            self._redraw_points()

    def _clear_points(self) -> None:
        """Remove every position (after confirmation; this cannot be undone)."""
        if not self.points:
            return
        if self.runner:
            messagebox.showinfo("Acquisition running", "Clear the positions after the acquisition ends.")
            return
        count = len(self.points)
        if not messagebox.askyesno(
                "Clear all positions",
                f"Remove all {count} position{'s' if count != 1 else ''} from the list?\n\n"
                "You can bring them back with Undo.",
                icon="warning", default="no"):
            return
        self._record_points("clear all positions")
        self.points.clear()
        self._redraw_points()
        self.status.set(f"Cleared {count} position{'s' if count != 1 else ''} (Undo restores them)")

    def _redraw_points(self, select: int | None = None) -> None:
        self.points_tree.delete(*self.points_tree.get_children())
        for index, point in enumerate(self.points):
            self.points_tree.insert("", "end", iid=str(index),
                                    values=(point.name, f"{point.x:.4f}", f"{point.y:.4f}", f"{point.z:.4f}"))
        if select is not None and 0 <= select < len(self.points):
            self.points_tree.selection_set(str(select))

    def _select_relative(self, delta: int) -> None:
        """Select the previous/next position; with no selection, start from the first/last."""
        count = len(self.points)
        if not count:
            return
        selected = self.points_tree.selection()
        if selected:
            index = min(max(int(selected[0]) + delta, 0), count - 1)
        else:
            index = 0 if delta > 0 else count - 1
        self.points_tree.selection_set(str(index))
        self.points_tree.focus(str(index))
        self.points_tree.see(str(index))

    def _points_key(self, event, delta: int, key: str):
        """Arrow-key shortcuts for Previous/Next while a position is selected."""
        if not self.points_tree.selection():
            return None
        widget = self.root.focus_get()
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox, ttk.Spinbox,
                               tk.Text)) or widget is self.preset_tree:
            return None  # the arrows belong to the text field / preset table
        if widget is self.points_tree and key in ("<Up>", "<Down>"):
            return None  # the list already moves its own selection
        self._select_relative(delta)
        return "break"

    def _update_points(self, mode: str) -> None:
        """Re-teach the selected position, or shift all positions by the same offset."""
        if not self._require_homed():
            return
        selected = self.points_tree.selection()
        if not selected:
            messagebox.showinfo("Select a position", "Select a position of interest first.")
            return
        index = int(selected[0])
        reference = self.points[index]
        current = self.motion.position
        try:
            new_points = update_points(self.points, index, current, mode)
            for point in new_points:
                self.motion.limits.validate(point.position)
        except (ValueError, IndexError) as exc:
            messagebox.showerror("Cannot update positions", str(exc))
            return
        axes = mode.split("_", 1)[1]
        if mode.startswith("all_"):
            parts = []
            if "xy" in axes:
                parts += [f"X {current.x - reference.x:+.4f} mm", f"Y {current.y - reference.y:+.4f} mm"]
            if axes.endswith("z"):
                parts.append(f"Z {current.z - reference.z:+.4f} mm")
            if not messagebox.askyesno(
                    "Shift all positions",
                    f"Shift all {len(self.points)} positions by {', '.join(parts)}?\n\n"
                    f"(the offset between the current position and {reference.name})"):
                return
        self._record_points("shift all positions" if mode.startswith("all_")
                            else "update position")
        self.points = new_points
        self._redraw_points(select=index)
        self.status.set(f"Updated positions ({mode.replace('_', ' ')})")

    def _save_point_list(self) -> None:
        path = filedialog.asksaveasfilename(title="Save positions", defaultextension=".json",
                                            filetypes=(("Position list", "*.json"),))
        if path:
            try:
                save_points(Path(path), self.points)
            except Exception as exc:
                messagebox.showerror("Could not save positions", str(exc))

    def _load_point_list(self) -> None:
        path = filedialog.askopenfilename(title="Load positions", filetypes=(("Position list", "*.json"),))
        if path:
            try:
                loaded = load_points(Path(path))
                self._record_points("load position list")
                self.points = loaded
                self._redraw_points()
            except Exception as exc:
                messagebox.showerror("Could not load positions", str(exc))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose image output folder", initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)

    def _format_changed(self) -> None:
        lossy = self.tiff_format.get() == JPEG2000
        self.jpeg2000_entry.configure(state="normal" if lossy else "disabled")

    def _jpeg2000_quality(self) -> float:
        """Read (UI thread only) and validate the JPEG 2000 quality; raises ValueError."""
        try:
            value = float(self.jpeg2000_psnr_db.get())
        except (ValueError, tk.TclError):
            low, high = JPEG2000_PSNR_RANGE_DB
            raise ValueError(f"JPEG 2000 quality must be a number between {low:g} and {high:g} dB.")
        validate_psnr(value)
        return value

    def _stagger_changed(self) -> None:
        self.stagger_entry.configure(state="normal" if self.stagger_autofocus.get() else "disabled")

    def _two_step_changed(self) -> None:
        two_step = bool(self.two_step_autofocus.get())
        self.z_range_label.set("Coarse range (µm)" if two_step else "Z range (µm)")
        self.z_spacing_label.set("Coarse spacing (µm)" if two_step else "Spacing (µm)")
        state = "normal" if two_step else "disabled"
        for entry in self.fine_entries:
            entry.configure(state=state)

    def _focus_measure_changed(self, _event=None) -> None:
        enabled = self.focus_measure_name.get() == "Custom Python function"
        state = "normal" if enabled else "disabled"
        self.custom_focus_entry.configure(state=state)
        self.custom_focus_button.configure(state=state)

    def _browse_custom_focus(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose custom autofocus function",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
        if path:
            self.custom_focus_path.set(path)

    def _center_well_changed(self) -> None:
        if self.center_well.get():
            self.autofocus.set(True)

    def _open_well_settings(self) -> None:
        existing = getattr(self, "well_settings_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = tk.Toplevel(self.root)
        self.well_settings_window = window
        window.title("Well centering settings")
        window.resizable(False, False)
        window.transient(self.root)
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Well detector").grid(row=0, column=0, sticky="e", padx=(0, 6))
        detector = ttk.Combobox(frame, textvariable=self.well_detector_name,
                                values=WELL_DETECTORS, state="readonly", width=27)
        detector.grid(row=0, column=1, columnspan=2, sticky="w")
        detector.bind("<<ComboboxSelected>>", self._well_detector_changed)

        ttk.Label(frame, text="Custom file").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=(7, 0))
        self.custom_well_entry = ttk.Entry(frame, textvariable=self.custom_well_path, width=45)
        self.custom_well_entry.grid(row=1, column=1, pady=(7, 0), sticky="ew")
        self.custom_well_button = ttk.Button(frame, text="Choose Python file…",
                                             command=self._browse_custom_well)
        self.custom_well_button.grid(row=1, column=2, pady=(7, 0), padx=(6, 0))

        calibration = ttk.LabelFrame(frame, text="Image-to-stage calibration", padding=8)
        calibration.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Label(calibration, text="X µm per pixel").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(calibration, textvariable=self.well_x_um_per_pixel, width=9).grid(row=0, column=1)
        ttk.Checkbutton(calibration, text="Invert X correction",
                        variable=self.invert_well_x).grid(row=0, column=2, padx=(12, 0))
        ttk.Label(calibration, text="Y µm per pixel").grid(row=1, column=0, padx=(0, 4), pady=(7, 0))
        ttk.Entry(calibration, textvariable=self.well_y_um_per_pixel, width=9).grid(
            row=1, column=1, pady=(7, 0))
        ttk.Checkbutton(calibration, text="Invert Y correction",
                        variable=self.invert_well_y).grid(row=1, column=2, padx=(12, 0), pady=(7, 0))

        ttk.Label(frame, text="Maximum XY correction (mm)").grid(
            row=3, column=0, sticky="e", padx=(0, 6), pady=(10, 0))
        ttk.Entry(frame, textvariable=self.max_center_correction_mm, width=9).grid(
            row=3, column=1, sticky="w", pady=(10, 0))
        ttk.Label(
            frame,
            text=("The detector runs on the image captured at the autofocus Z.\n"
                  "A fresh image is saved after the XY correction. Test direction and scale\n"
                  "in simulation or with generous objective clearance before unattended use."),
            foreground="#555555", justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(frame, text="Close", command=window.destroy).grid(
            row=5, column=2, sticky="e", pady=(12, 0))
        self._well_detector_changed()

    def _well_detector_changed(self, _event=None) -> None:
        enabled = self.well_detector_name.get() == "Custom Python function"
        state = "normal" if enabled else "disabled"
        if hasattr(self, "custom_well_entry") and self.custom_well_entry.winfo_exists():
            self.custom_well_entry.configure(state=state)
            self.custom_well_button.configure(state=state)

    def _browse_custom_well(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose custom well detector",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
        )
        if path:
            self.custom_well_path.set(path)

    def _open_roi_settings(self) -> None:
        existing = getattr(self, "roi_settings_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = tk.Toplevel(self.root)
        self.roi_settings_window = window
        window.title("Camera ROI settings")
        window.resizable(False, False)
        window.transient(self.root)
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Checkbutton(frame, text="Use a cropped camera region", variable=self.roi_enabled,
                        command=self._roi_enabled_changed).grid(
            row=0, column=0, columnspan=4, sticky="w")

        ttk.Label(frame, text="IMX178 Daheng maximum: 3088 × 2064 pixels",
                  font=("Segoe UI", 9, "bold")).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 2))

        self.roi_entries = []
        fields = (
            ("ROI width", self.roi_width), ("ROI height", self.roi_height),
        )
        for index, (label, variable) in enumerate(fields):
            row, column = 2, index * 2
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="e",
                                               padx=(0 if column == 0 else 14, 4), pady=(8, 0))
            entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row, column=column + 1, sticky="w", pady=(8, 0))
            self.roi_entries.append(entry)

        ttk.Label(
            frame,
            text=("The ROI is automatically centered horizontally and vertically.\n"
                  "Width or height 0 means use the full dimension.\n"
                  "This ROI is used for live view, autofocus, well detection and saved TIFFs."),
            foreground="#555555", justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Use full image", command=self._use_full_roi).pack(
            side="left", padx=(0, 6))
        ttk.Button(buttons, text="Apply and close",
                   command=lambda: self._apply_roi_settings(window)).pack(side="left")
        self._roi_enabled_changed()

    def _roi_enabled_changed(self) -> None:
        state = "normal" if self.roi_enabled.get() else "disabled"
        for entry in getattr(self, "roi_entries", []):
            if entry.winfo_exists():
                entry.configure(state=state)

    def _use_full_roi(self) -> None:
        self.roi_enabled.set(False)
        self.roi_width.set(0)
        self.roi_height.set(0)
        self._roi_enabled_changed()

    def _apply_roi_settings(self, window: tk.Toplevel) -> None:
        try:
            roi = self._current_roi()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid ROI", str(exc), parent=window)
            return
        was_live = bool(self.live_thread and self.live_thread.is_alive())
        if was_live and not self._stop_live(wait=True):
            return
        self._refresh_roi_summary(roi)
        window.destroy()
        if was_live:
            self._start_live()

    def _refresh_roi_summary(self, roi: ImageROI | None) -> None:
        if roi is None:
            self.roi_summary.set("ROI: full 3088 × 2064 camera image")
        else:
            width = roi.width or IMX178_MAX_WIDTH
            height = roi.height or IMX178_MAX_HEIGHT
            self.roi_summary.set(f"ROI: centered {width} × {height} px")

    def _current_roi(self) -> ImageROI | None:
        if not self.roi_enabled.get():
            return None
        width, height = int(self.roi_width.get()), int(self.roi_height.get())
        if width < 0 or height < 0:
            raise ValueError("ROI width and height cannot be negative")
        if width > IMX178_MAX_WIDTH or height > IMX178_MAX_HEIGHT:
            raise ValueError(
                f"ROI cannot exceed the Daheng IMX178 maximum of "
                f"{IMX178_MAX_WIDTH} × {IMX178_MAX_HEIGHT} pixels"
            )
        if width == 0 and height == 0:
            return None
        return ImageROI(width=width, height=height, centered=True)

    @staticmethod
    def _crop_to_roi(image: np.ndarray, roi: ImageROI | None) -> np.ndarray:
        if roi is not None:
            image = roi.crop(image)
        return np.ascontiguousarray(image)

    def _start(self) -> None:
        if not self._require_homed() or not self.camera or not self.camera.is_connected:
            return
        if self._gamepad_motion_busy():
            return
        if not self.points:
            messagebox.showerror("No positions", "Add at least one position of interest.")
            return
        try:
            settings = self._build_settings()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid acquisition settings", str(exc))
            return
        if not self._stop_live(wait=True):
            return
        self.runner = AcquisitionRunner(self.motion, self.camera)
        runner = self.runner
        points = [StagePoint(p.name, p.x, p.y, p.z) for p in self.points]
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(maximum=len(points) * settings.repeats, value=0)
        self.progress_text.set(f"0 / {len(points) * settings.repeats}")

        def callback(message, image, completed, total):
            self.ui_queue.put(("acquisition_progress", message, image, completed, total))

        def work():
            try:
                runner.run(points, settings, callback)
            except InterruptedError:
                pass
            finally:
                self.ui_queue.put(("acquisition_done", runner))

        self.status.set("Starting acquisition…")
        self._thread(work)

    def _build_settings(self) -> AcquisitionSettings:
        """Collect the acquisition panel into settings; raises ValueError/TclError if invalid."""
        backlash_enabled, xy_preload, z_preload = self._backlash_values()
        image_roi = self._current_roi()
        custom_focus = (Path(self.custom_focus_path.get())
                        if self.focus_measure_name.get() == "Custom Python function"
                        and self.custom_focus_path.get().strip() else None)
        custom_well = (Path(self.custom_well_path.get())
                       if self.well_detector_name.get() == "Custom Python function"
                       and self.custom_well_path.get().strip() else None)
        settings = AcquisitionSettings(
            repeats=int(self.repeats.get()), interval_s=float(self.interval_s.get()),
            exposure_ms=float(self.exposure_ms.get()), output_dir=Path(self.output_dir.get()),
            autofocus=bool(self.autofocus.get()), save_stack=bool(self.save_stack.get()),
            z_range_um=float(self.z_range_um.get()), z_spacing_um=float(self.z_spacing_um.get()),
            backlash_correction=backlash_enabled, xy_preload_mm=xy_preload,
            z_preload_mm=z_preload,
            focus_measure_name=self.focus_measure_name.get(),
            custom_focus_path=custom_focus,
            center_well=bool(self.center_well.get()),
            well_detector_name=self.well_detector_name.get(),
            custom_well_path=custom_well,
            well_x_um_per_pixel=float(self.well_x_um_per_pixel.get()),
            well_y_um_per_pixel=float(self.well_y_um_per_pixel.get()),
            invert_well_x=bool(self.invert_well_x.get()),
            invert_well_y=bool(self.invert_well_y.get()),
            max_center_correction_mm=float(self.max_center_correction_mm.get()),
            roi=image_roi,
            tiff_format=self.tiff_format.get(),
            jpeg2000_psnr_db=self._jpeg2000_quality() if self.tiff_format.get() == JPEG2000
            else DEFAULT_JPEG2000_PSNR_DB,
            bit_depth=self.camera.bit_depth,
            two_step_autofocus=bool(self.two_step_autofocus.get()),
            fine_range_um=float(self.fine_range_um.get()),
            fine_spacing_um=float(self.fine_spacing_um.get()),
            stagger_autofocus=bool(self.stagger_autofocus.get()),
            stagger_n=int(self.stagger_n.get()),
        )
        if settings.exposure_ms <= 0:
            raise ValueError("Exposure must be greater than zero")
        if settings.autofocus:
            settings.validate_autofocus()
        return settings

    def _stop(self) -> None:
        if self.runner:
            self.runner.stop()
            self.status.set("Stopping after the current camera or motion action…")
            self.stop_button.configure(state="disabled")

    def _emergency_stop(self) -> None:
        if not messagebox.askyesno("Emergency stop", "Immediately stop the printer? It may need to be power-cycled.",
                                   icon="warning"):
            return
        if self.runner:
            self.runner.stop()
        if self.jog:
            self.jog.halt()
            self.jog = None
        self._stop_live()
        if isinstance(self.motion, MarlinMotionController):
            try:
                self.motion.emergency_stop()
            except Exception as exc:
                messagebox.showerror("Emergency stop error", str(exc))
        self.position_text.set("Position unknown — reconnect and home")
        self.status.set("EMERGENCY STOP SENT — disconnect and inspect the microscope")

    def _toggle_live(self) -> None:
        if self.live_thread and self.live_thread.is_alive():
            self._stop_live()
            return
        self._start_live()

    def _start_live(self) -> None:
        if not self._require_connection() or not self.camera or not self.camera.is_connected:
            return
        if self.runner:
            messagebox.showinfo("Acquisition running", "Live view is paused during acquisition.")
            return
        try:
            exposure = float(self.exposure_ms.get())
            if exposure <= 0:
                raise ValueError
            roi = self._current_roi()
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid camera setting",
                                 "Exposure must be greater than zero and the ROI values must be valid.")
            return
        self.live_stop.clear()
        try:
            while True:
                self.live_frame_queue.get_nowait()
        except queue.Empty:
            pass
        self.live_button.configure(text="Stop live view")
        self.status.set("Live view running — stage movement remains available")

        def live_loop():
            try:
                while not self.live_stop.is_set():
                    frame_started = time.monotonic()
                    with self.camera_lock:
                        frame = self.camera.capture(exposure)
                    frame = self._crop_to_roi(frame, roi)
                    if not self.live_stop.is_set():
                        try:
                            self.live_frame_queue.put_nowait(frame)
                        except queue.Full:
                            try:
                                self.live_frame_queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                self.live_frame_queue.put_nowait(frame)
                            except queue.Full:
                                pass
                    self.live_stop.wait(max(0.0, 0.1 - (time.monotonic() - frame_started)))
            except Exception as exc:
                if not self.live_stop.is_set():
                    self.ui_queue.put(("error", type(exc).__name__, str(exc)))
            finally:
                self.ui_queue.put(("live_stopped", threading.current_thread()))

        self.live_thread = threading.Thread(target=live_loop, daemon=True)
        self.live_thread.start()

    def _stop_live(self, wait: bool = False) -> bool:
        self.live_stop.set()
        self.live_button.configure(text="Start live view")
        thread = self.live_thread
        if wait and thread and thread.is_alive() and thread is not threading.current_thread():
            try:
                timeout = min(30.0, max(3.0, float(self.exposure_ms.get()) / 1000.0 + 2.0))
            except (ValueError, tk.TclError):
                timeout = 5.0
            thread.join(timeout=timeout)
            if thread.is_alive():
                messagebox.showerror("Camera busy", "The current camera exposure has not finished yet.")
                return False
        return True

    def _capture_snapshot(self) -> None:
        if not self._require_connection() or not self.camera or not self.camera.is_connected:
            return
        if self.runner:
            messagebox.showinfo("Acquisition running", "Snapshots are unavailable during acquisition.")
            return
        was_live = bool(self.live_thread and self.live_thread.is_alive())
        if not self._stop_live(wait=True):
            return
        try:
            exposure = float(self.exposure_ms.get())
            if exposure <= 0:
                raise ValueError
            roi = self._current_roi()
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid camera setting",
                                 "Exposure must be greater than zero and the ROI values must be valid.")
            return
        output = Path(self.output_dir.get())
        tiff_format = self.tiff_format.get()  # Tk variables are read on the UI thread only
        try:
            psnr = self._jpeg2000_quality() if tiff_format == JPEG2000 else DEFAULT_JPEG2000_PSNR_DB
        except ValueError as exc:
            messagebox.showerror("Invalid JPEG 2000 quality", str(exc))
            return

        def work():
            with self.camera_lock:
                frame = self.camera.capture(exposure)
            frame = self._crop_to_roi(frame, roi)
            path = self._save_snapshot_file(frame, "Snapshot", output, tiff_format, psnr)
            self.ui_queue.put(("snapshot_saved", frame, path, was_live))

        self.status.set("Capturing snapshot…")
        self._thread(work)

    def _save_current_view(self) -> None:
        if self.last_image is None:
            messagebox.showinfo("No image", "Capture an image or start live view first.")
            return
        frame = self.last_image.copy()
        output = Path(self.output_dir.get())
        tiff_format = self.tiff_format.get()
        try:
            psnr = self._jpeg2000_quality() if tiff_format == JPEG2000 else DEFAULT_JPEG2000_PSNR_DB
        except ValueError as exc:
            messagebox.showerror("Invalid JPEG 2000 quality", str(exc))
            return

        def work():
            path = self._save_snapshot_file(frame, "View", output, tiff_format, psnr)
            self.ui_queue.put(("view_saved", path))

        self.status.set("Saving current view…")
        self._thread(work)

    def _save_snapshot_file(self, image: np.ndarray, prefix: str, output: Path,
                            tiff_format: str, psnr_db: float = DEFAULT_JPEG2000_PSNR_DB) -> Path:
        output.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = output / f"{prefix}_{timestamp}.tif"
        write_tiff(path, image, self._current_bits(), tiff_format, jpeg2000_psnr_db=psnr_db)
        return path

    def _current_bits(self) -> int:
        if self.camera is not None:
            return int(self.camera.bit_depth)
        return int(self.bit_depth.get())

    def _show_image(self, image: np.ndarray) -> None:
        if image is None:
            return
        self.last_image = image.copy()
        minimum = int(np.min(image))
        maximum = int(np.max(image))
        mean = float(np.mean(image))
        bits = self._current_bits()
        limit = max_value(bits)
        stats = f"Raw {bits}-bit values — min: {minimum}, max: {maximum}, mean: {mean:.1f} / {limit}"
        if maximum <= 3:
            stats += " — NO SENSOR SIGNAL: check illumination and increase exposure"
        elif maximum >= 0.995 * limit:
            stats += " — SATURATED: reduce exposure"
        self.image_stats.set(stats)
        self._reduced = reduce_for_display(image)
        self._hist_counts = histogram_counts(self._reduced, limit)
        self._render_preview()

    def _render_preview(self) -> None:
        """Redraw the preview from the cached reduced frame (display only)."""
        if self._reduced is None:
            return
        limit = max_value(self._current_bits())
        if self.auto_scale.get():
            self.display_min, self.display_max = auto_range(self._reduced, limit)
        display = to_uint8(self._reduced, self.display_min, self.display_max)
        pil = Image.fromarray(display, mode="L")
        pil.thumbnail((475, 600), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(pil)
        self.preview.configure(image=self.preview_photo, text="")
        self._redraw_histogram()

    def _value_to_x(self, value: float) -> float:
        width = max(self.hist_canvas.winfo_width(), 50)
        limit = max_value(self._current_bits())
        return 6 + (width - 12) * min(max(value, 0.0), limit) / limit

    def _x_to_value(self, x: float) -> float:
        width = max(self.hist_canvas.winfo_width(), 50)
        limit = max_value(self._current_bits())
        return min(max((x - 6) / (width - 12) * limit, 0.0), float(limit))

    def _redraw_histogram(self) -> None:
        canvas = self.hist_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 50)
        height = max(canvas.winfo_height(), 40)
        base, top = height - 4, 14
        if self._hist_counts is not None:
            heights = np.log1p(self._hist_counts.astype(np.float64))
            peak = float(heights.max()) or 1.0
            bins = len(heights)
            points = [6, base]
            for index, value in enumerate(heights):
                x = 6 + (width - 12) * index / (bins - 1)
                points += [x, base - (base - top) * value / peak]
            points += [width - 6, base]
            canvas.create_polygon(points, fill="#8a8a8a", outline="")
        for value, colour, tag in ((self.display_min, "#4aa3ff", "min"),
                                   (self.display_max, "#ff6b4a", "max")):
            x = self._value_to_x(value)
            canvas.create_line(x, 2, x, base, fill=colour, width=2)
            canvas.create_rectangle(x - 4, 0, x + 4, 10, fill=colour, outline="")
        self.display_range_text.set(
            f"Display range: {self.display_min:.0f} – {self.display_max:.0f}"
            f"{' (auto)' if self.auto_scale.get() else ''}")

    def _hist_press(self, event) -> None:
        to_min = abs(event.x - self._value_to_x(self.display_min))
        to_max = abs(event.x - self._value_to_x(self.display_max))
        self._hist_drag_target = "min" if to_min <= to_max else "max"
        self._hist_drag(event)

    def _hist_drag(self, event) -> None:
        if self._hist_drag_target is None:
            return
        value = self._x_to_value(event.x)
        limit = float(max_value(self._current_bits()))
        if self._hist_drag_target == "min":
            self.display_min = min(value, self.display_max - 1.0)
        else:
            self.display_max = max(value, self.display_min + 1.0)
        self.display_min = max(0.0, self.display_min)
        self.display_max = min(limit, self.display_max)
        self.auto_scale.set(False)
        self._render_preview()

    def _full_display_range(self) -> None:
        self.auto_scale.set(False)
        self.display_min, self.display_max = 0.0, float(max_value(self._current_bits()))
        self._render_preview()

    def _thread(self, function) -> None:
        def guarded():
            try:
                function()
            except Exception as exc:
                self.ui_queue.put(("error", type(exc).__name__, str(exc)))
        threading.Thread(target=guarded, daemon=True).start()

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]
                if kind == "error":
                    self.status.set(f"Error: {event[2]}")
                    if self.motion and self.motion.is_connected and self.motion.is_homed:
                        self._display_position(self.motion.position)  # may have been resynced
                    messagebox.showerror(event[1], event[2])
                    if self.runner:
                        self.runner.stop()
                elif kind == "connected":
                    self._start_jog_runner()
                    self._set_connected_state(True)
                    self.status.set("Connected — home XYZ before moving")
                elif kind == "position":
                    self._display_position(event[1])
                elif kind == "status":
                    self.status.set(event[1])
                elif kind == "acquisition_progress":
                    _, message, image, completed, total = event
                    self.status.set(message)
                    self._show_image(image)
                    if total:
                        self.progress.configure(maximum=total, value=completed)
                        self.progress_text.set(f"{completed} / {total}")
                    if self.motion:
                        self._display_position(self.motion.position)
                elif kind == "acquisition_done":
                    if self.runner is event[1]:
                        self.runner = None
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                elif kind == "live_stopped":
                    if self.live_thread is event[1]:
                        self.live_thread = None
                        self.live_button.configure(text="Start live view")
                elif kind == "snapshot_saved":
                    _, frame, path, restart_live = event
                    self._show_image(frame)
                    self.status.set(f"Snapshot saved: {path.name}")
                    if restart_live:
                        self._start_live()
                elif kind == "autofocus_done":
                    self.focus_busy = False
                    if event[1]:
                        self._start_live()
                elif kind == "port_detected":
                    self._port_detected(event[1], event[2])
                elif kind == "view_saved":
                    self.status.set(f"Current view saved: {event[1].name}")
        except queue.Empty:
            pass
        except Exception as exc:
            # A preview conversion problem must not terminate the Tk update
            # callback or bring down the rest of the microscope controls.
            self.status.set(f"Preview error: {type(exc).__name__}: {exc}")

        try:
            latest_frame = self.live_frame_queue.get_nowait()
        except queue.Empty:
            latest_frame = None
        if latest_frame is not None:
            try:
                self._show_image(latest_frame)
            except Exception as exc:
                self.status.set(f"Preview error: {type(exc).__name__}: {exc}")

        if self.root.winfo_exists():
            self.root.after(50, self._drain_ui_queue)

    def _display_position(self, p: Position) -> None:
        self.position_text.set(f"X {p.x:9.4f}    Y {p.y:9.4f}    Z {p.z:9.4f} mm")
        self.goto_x.set(round(p.x, 4))
        self.goto_y.set(round(p.y, 4))
        self.goto_z.set(round(p.z, 4))

    def _set_connected_state(self, connected: bool) -> None:
        self.connect_button.configure(text="Disconnect" if connected else "Connect")
        self.home_button.configure(state="normal" if connected else "disabled")
        camera_state = "normal" if connected else "disabled"
        self.live_button.configure(state=camera_state)
        self.capture_button.configure(state=camera_state)
        self.save_view_button.configure(state="normal" if self.last_image is not None else camera_state)
        if not connected:
            self.position_text.set("X —    Y —    Z —")
        self._update_hardware_fields()

    def _require_connection(self) -> bool:
        if not self.motion or not self.motion.is_connected:
            messagebox.showerror("Not connected", "Connect the microscope first.")
            return False
        return True

    def _require_homed(self) -> bool:
        if not self._require_connection():
            return False
        if not self.motion.is_homed:
            messagebox.showerror("Not homed", "Home XYZ before moving the objective.")
            return False
        return True

    def _on_close(self) -> None:
        if self.runner:
            if not messagebox.askyesno("Acquisition running", "Stop the acquisition and close the application?"):
                return
            self.runner.stop()
        self._autosave()
        self._stop_live(wait=True)
        if self.jog:
            self.jog.stop(wait=True)
        try:
            if self.camera:
                self.camera.disconnect()
            if self.motion:
                self.motion.disconnect()
        finally:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MicroscopeApp(root)
    root.mainloop()

