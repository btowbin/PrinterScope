from __future__ import annotations

from collections.abc import Callable
import importlib.util
import math
from pathlib import Path

import numpy as np


WellDetector = Callable[[np.ndarray], tuple[float, float]]
WELL_DETECTORS = (
    "Gradient centroid",
    "Custom Python function",
)


def gradient_centroid(image: np.ndarray) -> tuple[float, float]:
    """Estimate a well center from the centroid of strong, smoothed edges."""
    if image.ndim != 2 or min(image.shape) < 8:
        raise ValueError("Well detection requires a two-dimensional image of at least 8 × 8 pixels")
    scale = max(1, int(math.ceil(max(image.shape) / 1024)))
    sample = image[::scale, ::scale].astype(np.float32, copy=False)

    # Suppress small bacteria and texture while retaining the larger well boundary.
    smooth = sample.copy()
    for _ in range(6):
        padded = np.pad(smooth, 1, mode="edge")
        smooth = (padded[1:-1, 1:-1] + padded[:-2, 1:-1] + padded[2:, 1:-1]
                  + padded[1:-1, :-2] + padded[1:-1, 2:]) / 5.0
    gx = smooth[1:-1, 2:] - smooth[1:-1, :-2]
    gy = smooth[2:, 1:-1] - smooth[:-2, 1:-1]
    magnitude = gx * gx + gy * gy
    threshold = float(np.percentile(magnitude, 92.0))
    weights = np.where(magnitude >= threshold, magnitude, 0.0)
    total = float(np.sum(weights))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("No sufficiently strong well boundary was found")
    yy, xx = np.mgrid[1:image.shape[0] - 1, 1:image.shape[1] - 1]
    x = float(np.sum(xx * weights) / total) * scale
    y = float(np.sum(yy * weights) / total) * scale
    return x, y


def get_well_detector(name: str, custom_path: Path | None = None) -> WellDetector:
    if name == "Gradient centroid":
        return checked_well_detector(gradient_centroid)
    if name == "Custom Python function":
        if custom_path is None:
            raise ValueError("Choose a custom well-detection Python file")
        return load_custom_well_detector(custom_path)
    raise ValueError(f"Unknown well detector: {name}")


def load_custom_well_detector(path: Path) -> WellDetector:
    """Load find_well_center(image) from a trusted local Python file."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("The custom well detector must be an existing .py file")
    module_name = f"cnc_microscope_custom_well_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load custom well-detection file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "find_well_center", None)
    if not callable(function):
        raise ValueError("Custom file must define a callable find_well_center(image) function")
    return checked_well_detector(function)


def checked_well_detector(function: Callable) -> WellDetector:
    def checked(image: np.ndarray) -> tuple[float, float]:
        try:
            result = function(image)
        except Exception as exc:
            raise RuntimeError(f"Well detector failed: {exc}") from exc
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise ValueError("Well detector must return (x_pixel, y_pixel)")
        try:
            x, y = float(result[0]), float(result[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Well-center coordinates must be numeric") from exc
        height, width = image.shape[:2]
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Well-center coordinates must be finite")
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"Detected well center ({x:.1f}, {y:.1f}) is outside the {width} × {height} image"
            )
        return x, y

    return checked

