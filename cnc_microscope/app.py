from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import tifffile
from PIL import Image, ImageTk

from .acquisition import AcquisitionRunner, AcquisitionSettings
from .autofocus import STANDARD_FOCUS_MEASURES
from .camera import DahengCamera, SimulatedCamera
from .models import ImageROI, Position, StagePoint, load_points, save_points
from .motion import MarlinMotionController, SimulatedMotionController, serial_ports
from .well_detection import WELL_DETECTORS


IMX178_MAX_WIDTH = 3088
IMX178_MAX_HEIGHT = 2064


class MicroscopeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CNC Microscope")
        self.root.geometry("1220x790")
        self.root.minsize(1050, 690)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.motion = None
        self.camera = None
        self.runner: AcquisitionRunner | None = None
        self.points: list[StagePoint] = []
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

        self._make_variables()
        self._build_ui()
        self._apply_step_mode()
        self._focus_measure_changed()
        self._refresh_ports()
        self._set_connected_state(False)
        self.root.after(50, self._drain_ui_queue)

    def _make_variables(self) -> None:
        self.simulation = tk.BooleanVar(value=True)
        self.port = tk.StringVar()
        self.baud = tk.StringVar(value="115200")
        self.status = tk.StringVar(value="Ready — simulation mode selected")
        self.position_text = tk.StringVar(value="X —    Y —    Z —")
        self.step_mode = tk.StringVar(value="Fine")
        self.x_step = tk.DoubleVar(value=0.10)
        self.y_step = tk.DoubleVar(value=0.10)
        self.z_step = tk.DoubleVar(value=0.01)
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

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Position.TLabel", font=("Consolas", 13, "bold"))

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
        self.refresh_button = ttk.Button(connection, text="Refresh", command=self._refresh_ports)
        self.refresh_button.grid(row=0, column=3, padx=(0, 14))
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

        main = ttk.Panedwindow(outer, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main, padding=(0, 0, 8, 0))
        right = ttk.Frame(main, padding=(8, 0, 0, 0))
        main.add(left, weight=3)
        main.add(right, weight=2)

        self._build_motion(left)
        self._build_points(left)
        self._build_experiment(left)
        self._build_preview(right)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bottom, length=230, mode="determinate")
        self.progress.pack(side="left", padx=8)
        ttk.Label(bottom, textvariable=self.progress_text, width=10).pack(side="left")

    def _build_motion(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="Objective position", padding=10)
        box.pack(fill="x")
        ttk.Label(box, textvariable=self.position_text, style="Position.TLabel").grid(
            row=0, column=0, columnspan=7, pady=(0, 8), sticky="w")
        profiles = ttk.LabelFrame(box, text="Movement step preset", padding=6)
        profiles.grid(row=1, column=0, columnspan=7, sticky="ew")
        choices = (
            ("Coarse — X/Y 5 mm, Z 0.5 mm", "Coarse"),
            ("Fine — X/Y 0.1 mm, Z 0.01 mm", "Fine"),
            ("Super fine — X/Y 0.0125 mm, Z 0.0025 mm", "Super fine"),
            ("Custom — independent X, Y and Z steps", "Custom"),
        )
        for index, (label, value) in enumerate(choices):
            ttk.Radiobutton(profiles, text=label, value=value, variable=self.step_mode,
                            command=self._apply_step_mode).grid(row=index // 2, column=index % 2,
                                                                padx=(0, 18), pady=2,
                                                                sticky="w")

        custom = ttk.Frame(box)
        custom.grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))
        self.custom_step_entries = []
        for column, (label, variable) in enumerate(
                (("X step (mm)", self.x_step), ("Y step (mm)", self.y_step),
                 ("Z step (mm)", self.z_step))):
            ttk.Label(custom, text=label).grid(row=0, column=column * 2, padx=(0 if column == 0 else 14, 4))
            entry = ttk.Entry(custom, textvariable=variable, width=9)
            entry.grid(row=0, column=column * 2 + 1)
            entry.bind("<KeyRelease>", self._custom_step_changed)
            self.custom_step_entries.append(entry)
        ttk.Label(custom, textvariable=self.step_summary, foreground="#555555").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))

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
        ttk.Button(go, text="Go to position", command=self._go_to).grid(row=0, column=6, padx=4)

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
                                        height=7, selectmode="browse")
        widths = {"name": 145, "x": 85, "y": 85, "z": 85}
        for key, title in (("name", "Name"), ("x", "X (mm)"), ("y", "Y (mm)"), ("z", "Z (mm)")):
            self.points_tree.heading(key, text=title)
            self.points_tree.column(key, width=widths[key], anchor="center")
        self.points_tree.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(box)
        controls.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(controls, text="Add current", command=self._add_point).pack(fill="x", pady=2)
        ttk.Button(controls, text="Move to", command=self._move_to_selected).pack(fill="x", pady=2)
        ttk.Button(controls, text="Remove", command=self._remove_point).pack(fill="x", pady=2)
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
        ttk.Checkbutton(box, text="Autofocus at every position", variable=self.autofocus).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Checkbutton(box, text="Save Z-stack", variable=self.save_stack).grid(
            row=1, column=3, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Label(box, text="Z range (µm)").grid(row=2, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.z_range_um, width=9).grid(row=2, column=1, sticky="w")
        ttk.Label(box, text="Spacing (µm)").grid(row=2, column=2, sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.z_spacing_um, width=9).grid(row=2, column=3, sticky="w")
        ttk.Checkbutton(box, text="Center well after autofocus", variable=self.center_well,
                        command=self._center_well_changed).grid(
            row=2, column=4, columnspan=2, sticky="w", padx=(12, 0))
        ttk.Label(box, text="Focus measure").grid(row=3, column=0, pady=(8, 0), sticky="e", padx=(0, 4))
        self.focus_measure_combo = ttk.Combobox(
            box, textvariable=self.focus_measure_name, values=STANDARD_FOCUS_MEASURES,
            state="readonly", width=25)
        self.focus_measure_combo.grid(row=3, column=1, columnspan=2, pady=(8, 0), sticky="w")
        self.focus_measure_combo.bind("<<ComboboxSelected>>", self._focus_measure_changed)
        ttk.Button(box, text="Well centering settings…", command=self._open_well_settings).grid(
            row=3, column=4, columnspan=2, pady=(8, 0), sticky="e")

        ttk.Label(box, text="Custom file").grid(row=4, column=0, pady=(5, 0), sticky="e", padx=(0, 4))
        self.custom_focus_entry = ttk.Entry(box, textvariable=self.custom_focus_path)
        self.custom_focus_entry.grid(row=4, column=1, columnspan=4, pady=(5, 0), sticky="ew")
        self.custom_focus_button = ttk.Button(box, text="Choose Python file…",
                                              command=self._browse_custom_focus)
        self.custom_focus_button.grid(row=4, column=5, pady=(5, 0))

        ttk.Label(box, text="Output folder").grid(row=5, column=0, pady=(8, 0), sticky="e", padx=(0, 4))
        ttk.Entry(box, textvariable=self.output_dir).grid(row=5, column=1, columnspan=4,
                                                          pady=(8, 0), sticky="ew")
        ttk.Button(box, text="Browse…", command=self._browse_output).grid(row=5, column=5, pady=(8, 0))
        box.columnconfigure(4, weight=1)
        buttons = ttk.Frame(box)
        buttons.grid(row=6, column=0, columnspan=6, sticky="e", pady=(10, 0))
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
        ttk.Label(box, textvariable=self.image_stats).pack(fill="x", pady=(6, 0))
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
        ttk.Label(camera_controls, textvariable=self.roi_summary, foreground="#555555").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
        camera_controls.columnconfigure(0, weight=1)
        camera_controls.columnconfigure(1, weight=1)
        ttk.Label(parent, text=("Images contain 10-bit data stored in a standard 16-bit TIFF container.\n"
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
        self.baud_combo.configure(state="readonly" if enabled else "disabled")

    def _refresh_ports(self) -> None:
        ports = serial_ports()
        self.port_combo["values"] = ports
        if ports and self.port.get() not in ports:
            self.port.set(ports[0])
        self._simulation_changed()

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

        def work():
            motion = (SimulatedMotionController() if use_simulation else
                      MarlinMotionController(selected_port, selected_baud))
            camera = SimulatedCamera(lambda: motion.position.z) if use_simulation else DahengCamera()
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

        self.status.set("Homing XYZ…")
        self._thread(work)

    def _jog(self, dx=0, dy=0, dz=0) -> None:
        try:
            x_amount = float(self.x_step.get())
            y_amount = float(self.y_step.get())
            z_amount = float(self.z_step.get())
            if x_amount <= 0 or y_amount <= 0 or z_amount <= 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid step size", "X, Y and Z step sizes must be greater than zero.")
            return
        feed = self._movement_feed(z_axis=bool(dz))
        self._motion_action(lambda: self.motion.move_by(
            dx * x_amount, dy * y_amount, dz * z_amount, feed_mm_min=feed),
                            "Moving objective…")

    def _go_to(self) -> None:
        try:
            target = Position(float(self.goto_x.get()), float(self.goto_y.get()), float(self.goto_z.get()))
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid position", "Enter numeric X, Y and Z coordinates.")
            return
        self._motion_action(lambda: self._approach_target(target), "Moving to absolute position…")

    def _motion_action(self, action, description: str) -> None:
        if not self._require_homed():
            return

        def work():
            action()
            self.ui_queue.put(("position", self.motion.position))
            self.ui_queue.put(("status", "Move complete"))

        self.status.set(description)
        self._thread(work)

    def _apply_step_mode(self) -> None:
        profile = self.step_mode.get()
        if profile == "Coarse":
            self.x_step.set(5.0)
            self.y_step.set(5.0)
            self.z_step.set(0.50)
        elif profile == "Fine":
            self.x_step.set(0.10)
            self.y_step.set(0.10)
            self.z_step.set(0.01)
        elif profile == "Super fine":
            # Standard Ender 3 v2: 80 steps/mm XY and 400 steps/mm Z.
            self.x_step.set(0.0125)
            self.y_step.set(0.0125)
            self.z_step.set(0.0025)
        entry_state = "normal" if profile == "Custom" else "disabled"
        for entry in self.custom_step_entries:
            entry.configure(state=entry_state)
        self._update_step_summary()

    def _custom_step_changed(self, _event=None) -> None:
        if self.step_mode.get() == "Custom":
            self._update_step_summary()

    def _update_step_summary(self) -> None:
        try:
            self.step_summary.set(
                f"Active steps: X {float(self.x_step.get()):g} mm, "
                f"Y {float(self.y_step.get()):g} mm, Z {float(self.z_step.get()):g} mm"
            )
        except (ValueError, tk.TclError):
            self.step_summary.set("Enter positive numeric step sizes for X, Y and Z")

    def _movement_feed(self, z_axis: bool) -> float:
        profile = self.step_mode.get()
        if profile == "Coarse":
            return 600.0 if z_axis else 3000.0
        if profile == "Super fine":
            return 60.0 if z_axis else 300.0
        return 300.0 if z_axis else 1200.0

    def _backlash_values(self) -> tuple[bool, float, float]:
        enabled = bool(self.backlash_correction.get())
        xy = float(self.xy_preload_mm.get())
        z = float(self.z_preload_mm.get())
        if xy < 0 or z < 0:
            raise ValueError("Backlash preload distances cannot be negative")
        return enabled, xy, z

    def _approach_target(self, target: Position) -> None:
        enabled, xy, z = self._backlash_values()
        if enabled:
            self.motion.approach_to(target, xy, z)
        else:
            self.motion.move_to(target)

    def _add_point(self) -> None:
        if not self._require_homed():
            return
        p = self.motion.position
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
            self._motion_action(lambda: self._approach_target(point.position),
                                f"Moving to {point.name}…")

    def _remove_point(self) -> None:
        selected = self.points_tree.selection()
        if selected:
            del self.points[int(selected[0])]
            for index, point in enumerate(self.points, 1):
                point.name = f"Point {index}"
            self._redraw_points()

    def _redraw_points(self) -> None:
        self.points_tree.delete(*self.points_tree.get_children())
        for index, point in enumerate(self.points):
            self.points_tree.insert("", "end", iid=str(index),
                                    values=(point.name, f"{point.x:.4f}", f"{point.y:.4f}", f"{point.z:.4f}"))

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
                self.points = load_points(Path(path))
                self._redraw_points()
            except Exception as exc:
                messagebox.showerror("Could not load positions", str(exc))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose image output folder", initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)

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
        if roi is None:
            self.roi_summary.set("ROI: full 3088 × 2064 camera image")
        else:
            width = roi.width or IMX178_MAX_WIDTH
            height = roi.height or IMX178_MAX_HEIGHT
            self.roi_summary.set(f"ROI: centered {width} × {height} px")
        window.destroy()
        if was_live:
            self._start_live()

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
        if not self.points:
            messagebox.showerror("No positions", "Add at least one position of interest.")
            return
        try:
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
            )
            if settings.exposure_ms <= 0:
                raise ValueError("Exposure must be greater than zero")
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
        self._stop_live()
        if isinstance(self.motion, MarlinMotionController):
            try:
                self.motion.emergency_stop()
            except Exception as exc:
                messagebox.showerror("Emergency stop error", str(exc))
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

        def work():
            with self.camera_lock:
                frame = self.camera.capture(exposure)
            frame = self._crop_to_roi(frame, roi)
            path = self._save_snapshot_file(frame, "Snapshot", output)
            self.ui_queue.put(("snapshot_saved", frame, path, was_live))

        self.status.set("Capturing snapshot…")
        self._thread(work)

    def _save_current_view(self) -> None:
        if self.last_image is None:
            messagebox.showinfo("No image", "Capture an image or start live view first.")
            return
        frame = self.last_image.copy()
        output = Path(self.output_dir.get())

        def work():
            path = self._save_snapshot_file(frame, "View", output)
            self.ui_queue.put(("view_saved", path))

        self.status.set("Saving current view…")
        self._thread(work)

    def _save_snapshot_file(self, image: np.ndarray, prefix: str, output: Path) -> Path:
        output.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = output / f"{prefix}_{timestamp}.tif"
        tifffile.imwrite(path, image.astype(np.uint16, copy=False), photometric="minisblack",
                         metadata={"bit_depth": 10})
        return path

    def _show_image(self, image: np.ndarray) -> None:
        if image is None:
            return
        self.last_image = image.copy()
        minimum = int(np.min(image))
        maximum = int(np.max(image))
        mean = float(np.mean(image))
        stats = f"Raw 10-bit values — min: {minimum}, max: {maximum}, mean: {mean:.1f} / 1023"
        if maximum <= 3:
            stats += " — NO SENSOR SIGNAL: check illumination and increase exposure"
        elif maximum >= 1018:
            stats += " — SATURATED: reduce exposure"
        self.image_stats.set(stats)
        lo, hi = np.percentile(image, (0.5, 99.5))
        if hi <= lo:
            # A uniform frame still has a meaningful brightness. The previous
            # fallback rendered every uniform image as black, including a
            # mid-grey sensor frame.
            display = np.clip(
                image.astype(np.float32) * (255.0 / 1023.0), 0, 255
            ).astype(np.uint8)
        else:
            display = np.clip((image.astype(np.float32) - lo) * (255 / (hi - lo)), 0, 255).astype(np.uint8)
        pil = Image.fromarray(display, mode="L")
        pil.thumbnail((475, 600), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(pil)
        self.preview.configure(image=self.preview_photo, text="")

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
                    messagebox.showerror(event[1], event[2])
                    if self.runner:
                        self.runner.stop()
                elif kind == "connected":
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
        self._stop_live(wait=True)
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

