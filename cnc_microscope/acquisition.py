from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Callable

import numpy as np

from .autofocus import FocusMeasure, get_focus_measure, stack_positions
from .camera import Camera
from .image_io import DEFAULT_JPEG2000_PSNR_DB, JPEG2000, UNCOMPRESSED, validate_psnr, write_tiff
from .models import ImageROI, Position, StagePoint
from .motion import MotionController
from .position_log import LOG_NAME, PositionLog
from .well_detection import WellDetector, get_well_detector


@dataclass(frozen=True)
class AcquisitionSettings:
    repeats: int
    interval_s: float
    exposure_ms: float
    output_dir: Path
    autofocus: bool
    save_stack: bool
    z_range_um: float
    z_spacing_um: float
    backlash_correction: bool = True
    xy_preload_mm: float = 0.25
    z_preload_mm: float = 0.05
    focus_measure_name: str = "Tenengrad"
    custom_focus_path: Path | None = None
    center_well: bool = False
    well_detector_name: str = "Gradient centroid"
    custom_well_path: Path | None = None
    well_x_um_per_pixel: float = 1.0
    well_y_um_per_pixel: float = 1.0
    invert_well_x: bool = False
    invert_well_y: bool = False
    max_center_correction_mm: float = 2.0
    roi: ImageROI | None = None
    tiff_format: str = UNCOMPRESSED
    jpeg2000_psnr_db: float = DEFAULT_JPEG2000_PSNR_DB
    bit_depth: int = 10
    # Optional two-step autofocus: z_range_um / z_spacing_um describe the coarse scan,
    # then a fine scan of fine_range_um / fine_spacing_um is centred on the coarse peak.
    two_step_autofocus: bool = False
    fine_range_um: float = 20.0
    fine_spacing_um: float = 2.0
    # Staggered autofocus: the first time point focuses every position; afterwards time
    # point t (counting from 0) refocuses positions t, t+n, t+2n, ... (1-based).
    stagger_autofocus: bool = False
    stagger_n: int = 3

    def validate_autofocus(self) -> None:
        self.validate_two_step()
        if self.stagger_autofocus and self.stagger_n < 1:
            raise ValueError("Staggered autofocus needs n of at least 1")

    def validate_two_step(self) -> None:
        if not self.two_step_autofocus:
            return
        if self.fine_spacing_um <= 0 or self.fine_range_um <= 0:
            raise ValueError("The fine Z range and spacing must be greater than zero")
        if self.fine_spacing_um >= self.z_spacing_um:
            raise ValueError("The fine spacing must be smaller than the coarse spacing")
        if self.fine_range_um < 2 * self.z_spacing_um:
            raise ValueError(
                "The fine range must be at least twice the coarse spacing "
                f"({2 * self.z_spacing_um:g} µm), so it covers the gap between coarse planes"
            )


@dataclass
class FocusScan:
    """One Z scan (a coarse or fine pass) and its focus scores."""
    label: str
    z: list
    images: list
    scores: list
    best_index: int
    error: str | None

    @property
    def best_z(self) -> float:
        return self.z[self.best_index]


ProgressCallback = Callable[[str, np.ndarray | None, int, int], None]
MOTION_SETTLE_S = 0.10


class AcquisitionRunner:
    def __init__(self, motion: MotionController, camera: Camera):
        self.motion = motion
        self.camera = camera
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self, points: list[StagePoint], settings: AcquisitionSettings,
            callback: ProgressCallback) -> None:
        if not points:
            raise ValueError("Define at least one stage position")
        if settings.repeats < 1:
            raise ValueError("Repeats must be at least 1")
        if settings.interval_s < 0:
            raise ValueError("Interval cannot be negative")
        if settings.tiff_format == JPEG2000:
            validate_psnr(settings.jpeg2000_psnr_db)
        if settings.center_well and not settings.autofocus:
            raise ValueError("Well centering requires autofocus to be enabled")
        if settings.autofocus:
            settings.validate_autofocus()
        if settings.center_well:
            if settings.well_x_um_per_pixel <= 0 or settings.well_y_um_per_pixel <= 0:
                raise ValueError("Well-centering pixel calibrations must be greater than zero")
            if settings.max_center_correction_mm <= 0:
                raise ValueError("Maximum well-centering correction must be greater than zero")
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        focus_measure = (get_focus_measure(settings.focus_measure_name, settings.custom_focus_path)
                         if settings.autofocus else None)
        well_detector = (get_well_detector(settings.well_detector_name, settings.custom_well_path)
                         if settings.center_well else None)
        total = len(points) * settings.repeats
        completed = 0
        completed_normally = False
        focus_memory: dict[int, Position] = {}  # point index -> last focused (and centred) position
        log = PositionLog(settings.output_dir / LOG_NAME)
        run_start = time.monotonic()
        log_warned = False
        try:
            for time_index in range(1, settings.repeats + 1):
                cycle_start = time.monotonic()
                for point_index, point in enumerate(points, 1):
                    self._check_stop()
                    do_af = self.should_autofocus(point_index, time_index, settings, focus_memory)
                    note = ("" if do_af or not settings.autofocus
                            else " (autofocus skipped, using the last focused position)")
                    callback(
                        f"Moving to {point.name} — time {time_index}/{settings.repeats}{note}",
                        None, completed, total,
                    )
                    remembered = focus_memory.get(point_index)
                    self._move_to_point(
                        point.position if do_af or remembered is None else remembered, settings)
                    image = self._capture_at_point(
                        point_index, time_index, point, settings, callback, focus_measure,
                        well_detector, do_af,
                    )
                    if do_af:
                        focus_memory[point_index] = self.motion.position
                    completed += 1
                    try:
                        self._log_position(log, time.monotonic() - run_start, time_index,
                                           point_index, point, settings, do_af)
                    except OSError as exc:  # e.g. the CSV is open and locked in Excel
                        if not log_warned:
                            log_warned = True
                            callback(f"Could not write {LOG_NAME}: {exc}", None, completed, total)
                    callback(f"Saved {point.name}", image, completed, total)
                if time_index < settings.repeats:
                    remaining = settings.interval_s - (time.monotonic() - cycle_start)
                    if remaining > 0:
                        callback(f"Waiting {remaining:.1f} s for the next time point", None,
                                 completed, total)
                        if self.stop_event.wait(remaining):
                            self._check_stop()
            completed_normally = True
        finally:
            if self.stop_event.is_set():
                callback("Stopped", None, completed, total)
            elif completed_normally:
                callback("Acquisition complete", None, completed, total)

    @staticmethod
    def should_autofocus(point_index: int, time_index: int, settings: AcquisitionSettings,
                         focus_memory: dict) -> bool:
        """Whether this position is autofocused at this time point (both counted from 1)."""
        if not settings.autofocus:
            return False
        if not settings.stagger_autofocus:
            return True
        t = time_index - 1  # the first time point is t = 0: every position is focused
        if t == 0 or point_index not in focus_memory:
            return True
        return (point_index - t) % settings.stagger_n == 0

    def _log_position(self, log: PositionLog, elapsed_s: float, time_index: int,
                      point_index: int, point: StagePoint, settings: AcquisitionSettings,
                      do_af: bool = True) -> None:
        stem = f"Point{point_index:04d}_Time{time_index:05d}"
        scanned = do_af if settings.autofocus else settings.save_stack
        stacked = settings.save_stack and scanned
        fine = stacked and settings.autofocus and settings.two_step_autofocus
        actual = self.motion.position  # where the saved image was captured
        log.append(
            elapsed_s=elapsed_s, time_index=time_index, point_index=point_index,
            point_name=point.name, planned=(point.x, point.y, point.z),
            actual=(actual.x, actual.y, actual.z), image_file=f"{stem}.tif",
            zstack_file=f"{stem}_ZStack.tif" if stacked else "",
            zstack_fine_file=f"{stem}_ZStackFine.tif" if fine else "",
            autofocus="yes" if settings.autofocus and do_af else "no",
        )

    def _capture_at_point(self, point_index: int, time_index: int, point: StagePoint,
                          settings: AcquisitionSettings, callback: ProgressCallback,
                          focus_measure: FocusMeasure | None,
                          well_detector: WellDetector | None, do_af: bool = True) -> np.ndarray:
        stem = f"Point{point_index:04d}_Time{time_index:05d}"
        # Without autofocus, a scan only happens to save a stack; with autofocus, it
        # happens only at the time points where this position is (re)focused.
        scan = do_af if settings.autofocus else settings.save_stack
        if not scan:
            image = self._capture(settings)
            self._save_image(settings.output_dir / f"{stem}.tif", image, settings)
            return image

        scans = self._focus_scans(point, settings, callback, focus_measure)
        if settings.save_stack:
            for scan in scans:
                self._save_stack(settings.output_dir, stem, scan, settings, len(scans) > 1)
        score_error = next((scan.error for scan in scans if scan.error), None)
        if score_error:
            raise RuntimeError(score_error)
        final = scans[-1]
        valid_z, images, best_index = final.z, final.images, final.best_index

        best_z = valid_z[best_index]
        callback(
            f"{point.name}: best focus is stack plane {best_index + 1}/{len(valid_z)} "
            f"at Z {best_z:.4f} mm",
            images[best_index], 0, 0,
        )
        best_position = Position(point.x, point.y, best_z)
        if settings.backlash_correction:
            self.motion.approach_z(best_position, settings.z_preload_mm, feed_mm_min=300)
        else:
            self.motion.move_to(best_position, feed_mm_min=300)
        time.sleep(MOTION_SETTLE_S)
        focused_image = self._capture(settings)

        if well_detector is not None:
            x_pixel, y_pixel = well_detector(focused_image)
            height, width = focused_image.shape[:2]
            error_x = x_pixel - (width - 1) / 2.0
            error_y = y_pixel - (height - 1) / 2.0
            direction_x = -1.0 if settings.invert_well_x else 1.0
            direction_y = -1.0 if settings.invert_well_y else 1.0
            dx = error_x * settings.well_x_um_per_pixel * direction_x / 1000.0
            dy = error_y * settings.well_y_um_per_pixel * direction_y / 1000.0
            correction = float(np.hypot(dx, dy))
            if correction > settings.max_center_correction_mm:
                raise ValueError(
                    f"Detected well requires a {correction:.3f} mm XY correction, exceeding "
                    f"the configured {settings.max_center_correction_mm:.3f} mm safety limit"
                )
            centered_position = Position(point.x + dx, point.y + dy, best_z)
            callback(
                f"Well at ({x_pixel:.1f}, {y_pixel:.1f}) px; centering by "
                f"X {dx:+.4f}, Y {dy:+.4f} mm",
                focused_image, 0, 0,
            )
            self._move_to_point(centered_position, settings)
            final_image = self._capture(settings)
        else:
            final_image = focused_image

        # The saved exposure is captured at the final focused and optionally centered position.
        self._save_image(settings.output_dir / f"{stem}.tif", final_image, settings)
        return final_image

    def _run_scan(self, point: StagePoint, settings: AcquisitionSettings,
                  callback: ProgressCallback, focus_measure: FocusMeasure | None,
                  range_um: float, spacing_um: float, label: str) -> FocusScan:
        valid_z, images, scores = self._scan_stack(point, settings, callback, focus_measure,
                                                   range_um, spacing_um, label)
        best_index = int(np.argmax(scores)) if focus_measure is not None else len(images) // 2
        error = self._focus_error(images, scores, settings, focus_measure is not None)
        return FocusScan(label, valid_z, images, scores, best_index, error)

    def _focus_scans(self, point: StagePoint, settings: AcquisitionSettings,
                     callback: ProgressCallback, focus_measure: FocusMeasure | None):
        """Run the coarse scan and, when enabled, a fine scan centred on its peak."""
        two_step = settings.two_step_autofocus and focus_measure is not None
        first = self._run_scan(point, settings, callback, focus_measure,
                               settings.z_range_um, settings.z_spacing_um,
                               "coarse" if two_step else "")
        scans = [first]
        if two_step and first.error is None:
            if len(first.z) > 1 and first.best_index in (0, len(first.z) - 1):
                callback(f"{point.name}: coarse peak is at the edge of the scan range; "
                         "the fine scan will extend beyond it", None, 0, 0)
            callback(f"{point.name}: coarse focus at Z {first.best_z:.4f} mm, starting fine scan",
                     first.images[first.best_index], 0, 0)
            centre = StagePoint(point.name, point.x, point.y, first.best_z)
            scans.append(self._run_scan(centre, settings, callback, focus_measure,
                                        settings.fine_range_um, settings.fine_spacing_um, "fine"))
        return scans

    @staticmethod
    def _save_stack(output_dir: Path, stem: str, scan: FocusScan,
                    settings: AcquisitionSettings, two_step: bool) -> None:
        metadata = {
            "axes": "ZYX",
            "scan_order": "high_to_low_z",
            "z_positions_mm": scan.z,
            "selected_plane_index_zero_based": scan.best_index,
        }
        if two_step:
            metadata["scan"] = scan.label
        if scan.scores:
            metadata["focus_scores"] = scan.scores
        if scan.error:
            metadata["autofocus_error"] = scan.error
        suffix = "_ZStackFine" if scan.label == "fine" else "_ZStack"
        write_tiff(output_dir / f"{stem}{suffix}.tif", np.stack(scan.images),
                   settings.bit_depth, settings.tiff_format, metadata, settings.jpeg2000_psnr_db)

    def _scan_stack(self, point: StagePoint, settings: AcquisitionSettings,
                    callback: ProgressCallback, focus_measure: FocusMeasure | None,
                    range_um: float | None = None, spacing_um: float | None = None,
                    label: str = ""):
        """Capture the Z-stack around ``point`` (high to low); return planes, images, scores."""
        range_um = settings.z_range_um if range_um is None else range_um
        spacing_um = settings.z_spacing_um if spacing_um is None else spacing_um
        # High-to-low scanning keeps every captured Z plane approached in the
        # same Z- direction after the initial backlash preload.
        z_values = list(reversed(stack_positions(point.z, range_um, spacing_um)))
        valid_z = [z for z in z_values if self.motion.limits.z_min <= z <= self.motion.limits.z_max]
        if not valid_z:
            raise ValueError(f"Z-stack at {point.name} is outside the configured travel limits")
        images: list[np.ndarray] = []
        scores: list[float] = []
        for stack_index, z in enumerate(valid_z, 1):
            self._check_stop()
            target = Position(point.x, point.y, z)
            if stack_index == 1 and settings.backlash_correction:
                self.motion.approach_z(target, settings.z_preload_mm, feed_mm_min=300)
            else:
                self.motion.move_to(target, feed_mm_min=300)
            time.sleep(MOTION_SETTLE_S)
            frame = self._capture(settings)
            images.append(frame)
            if focus_measure is not None:
                scores.append(focus_measure(frame))
            name = f"{point.name}: {label} Z plane" if label else f"{point.name}: Z plane"
            callback(f"{name} {stack_index}/{len(valid_z)}", frame, 0, 0)
        return valid_z, images, scores

    @staticmethod
    def _focus_error(images, scores, settings: AcquisitionSettings, has_measure: bool) -> str | None:
        if not (has_measure and len(scores) > 1
                and np.ptp(np.asarray(scores, dtype=np.float64)) == 0):
            return None
        saturation = 0.995 * ((1 << settings.bit_depth) - 1)
        if all(np.mean(image >= saturation) > 0.95 for image in images):
            return ("Autofocus cannot measure focus because the Z-stack is saturated. "
                    "Reduce the exposure time and try again.")
        return ("Autofocus returned the same score for every Z plane. Increase image "
                "contrast, adjust exposure, or choose another focus measure.")

    def autofocus_here(self, settings: AcquisitionSettings,
                       callback: ProgressCallback | None = None) -> Position:
        """Scan a Z-stack around the current position and move to the sharpest plane."""
        callback = callback or (lambda *args: None)
        start = self.motion.position
        point = StagePoint("Current position", start.x, start.y, start.z)
        settings.validate_two_step()
        focus_measure = get_focus_measure(settings.focus_measure_name, settings.custom_focus_path)
        scans = self._focus_scans(point, settings, callback, focus_measure)
        error = next((scan.error for scan in scans if scan.error), None)
        if error:
            raise RuntimeError(error)
        final = scans[-1]
        valid_z, images, best_index = final.z, final.images, final.best_index
        best = Position(start.x, start.y, valid_z[best_index])
        if settings.backlash_correction:
            self.motion.approach_z(best, settings.z_preload_mm, feed_mm_min=300)
        else:
            self.motion.move_to(best, feed_mm_min=300)
        callback(f"Autofocus: best plane {best_index + 1}/{len(valid_z)} at Z {best.z:.4f} mm",
                 images[best_index], 0, 0)
        return best

    @staticmethod
    def _save_image(path: Path, image: np.ndarray, settings: AcquisitionSettings) -> None:
        write_tiff(path, image, settings.bit_depth, settings.tiff_format,
                   jpeg2000_psnr_db=settings.jpeg2000_psnr_db)

    def _move_to_point(self, position: Position, settings: AcquisitionSettings) -> None:
        if settings.backlash_correction:
            self.motion.approach_to(position, settings.xy_preload_mm,
                                    settings.z_preload_mm)
        else:
            self.motion.move_to(position)
        time.sleep(MOTION_SETTLE_S)

    def _capture(self, settings: AcquisitionSettings) -> np.ndarray:
        image = self.camera.capture(settings.exposure_ms)
        if settings.roi is not None:
            image = settings.roi.crop(image)
        return np.ascontiguousarray(image)

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise InterruptedError("Acquisition stopped by user")

