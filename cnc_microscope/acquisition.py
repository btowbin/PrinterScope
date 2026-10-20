from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Callable

import numpy as np
import tifffile

from .autofocus import FocusMeasure, get_focus_measure, stack_positions
from .camera import Camera
from .models import ImageROI, Position, StagePoint
from .motion import MotionController
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
        if settings.center_well and not settings.autofocus:
            raise ValueError("Well centering requires autofocus to be enabled")
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
        try:
            for time_index in range(1, settings.repeats + 1):
                cycle_start = time.monotonic()
                for point_index, point in enumerate(points, 1):
                    self._check_stop()
                    callback(
                        f"Moving to {point.name} — time {time_index}/{settings.repeats}",
                        None, completed, total,
                    )
                    self._move_to_point(point.position, settings)
                    image = self._capture_at_point(
                        point_index, time_index, point, settings, callback, focus_measure,
                        well_detector,
                    )
                    completed += 1
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

    def _capture_at_point(self, point_index: int, time_index: int, point: StagePoint,
                          settings: AcquisitionSettings, callback: ProgressCallback,
                          focus_measure: FocusMeasure | None,
                          well_detector: WellDetector | None) -> np.ndarray:
        stem = f"Point{point_index:04d}_Time{time_index:05d}"
        should_stack = settings.autofocus or settings.save_stack
        if not should_stack:
            image = self._capture(settings)
            self._save_image(settings.output_dir / f"{stem}.tif", image)
            return image

        # High-to-low scanning keeps every captured Z plane approached in the
        # same Z- direction after the initial backlash preload.
        z_values = list(reversed(stack_positions(point.z, settings.z_range_um,
                                                 settings.z_spacing_um)))
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
            callback(f"{point.name}: Z plane {stack_index}/{len(valid_z)}", frame, 0, 0)

        best_index = int(np.argmax(scores)) if focus_measure is not None else len(images) // 2
        score_error: str | None = None
        if (focus_measure is not None and len(scores) > 1
                and np.ptp(np.asarray(scores, dtype=np.float64)) == 0):
            if all(np.mean(image >= 1018) > 0.95 for image in images):
                score_error = (
                    "Autofocus cannot measure focus because the Z-stack is saturated. "
                    "Reduce the exposure time and try again."
                )
            else:
                score_error = (
                    "Autofocus returned the same score for every Z plane. Increase image "
                    "contrast, adjust exposure, or choose another focus measure."
                )

        if settings.save_stack:
            stack = np.stack(images)
            metadata = {
                "axes": "ZYX",
                "bit_depth": 10,
                "scan_order": "high_to_low_z",
                "z_positions_mm": valid_z,
                "selected_plane_index_zero_based": best_index,
            }
            if scores:
                metadata["focus_scores"] = scores
            if score_error:
                metadata["autofocus_error"] = score_error
            tifffile.imwrite(
                settings.output_dir / f"{stem}_ZStack.tif", stack,
                photometric="minisblack", metadata=metadata,
            )

        if score_error:
            raise RuntimeError(score_error)

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
        self._save_image(settings.output_dir / f"{stem}.tif", final_image)
        return final_image

    @staticmethod
    def _save_image(path: Path, image: np.ndarray) -> None:
        tifffile.imwrite(path, image.astype(np.uint16, copy=False), photometric="minisblack",
                         metadata={"bit_depth": 10})

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

