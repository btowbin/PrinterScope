from __future__ import annotations

from collections.abc import Callable
import importlib.util
import math
from pathlib import Path

import numpy as np


FocusMeasure = Callable[[np.ndarray], float]
STANDARD_FOCUS_MEASURES = (
    "Tenengrad",
    "Variance of Laplacian",
    "Image variance",
    "Brenner gradient",
    "Custom Python function",
)


def tenengrad(image: np.ndarray) -> float:
    """Gradient-energy focus measure; larger values indicate sharper images."""
    sample = image.astype(np.float32, copy=False)
    gx = sample[:, 2:] - sample[:, :-2]
    gy = sample[2:, :] - sample[:-2, :]
    return float(np.mean(gx[1:-1, :] ** 2) + np.mean(gy[:, 1:-1] ** 2))


def variance_of_laplacian(image: np.ndarray) -> float:
    """Variance of a discrete Laplacian; larger values indicate sharper edges."""
    sample = image.astype(np.float32, copy=False)
    laplacian = (4 * sample[1:-1, 1:-1] - sample[:-2, 1:-1] - sample[2:, 1:-1]
                 - sample[1:-1, :-2] - sample[1:-1, 2:])
    return float(np.var(laplacian))


def image_variance(image: np.ndarray) -> float:
    """Global intensity variance; useful for high-contrast, stable specimens."""
    return float(np.var(image.astype(np.float32, copy=False)))


def brenner_gradient(image: np.ndarray) -> float:
    """Mean squared two-pixel horizontal difference."""
    sample = image.astype(np.float32, copy=False)
    difference = sample[:, 2:] - sample[:, :-2]
    return float(np.mean(difference ** 2))


def get_focus_measure(name: str, custom_path: Path | None = None) -> FocusMeasure:
    standard: dict[str, FocusMeasure] = {
        "Tenengrad": tenengrad,
        "Variance of Laplacian": variance_of_laplacian,
        "Image variance": image_variance,
        "Brenner gradient": brenner_gradient,
    }
    if name in standard:
        return standard[name]
    if name == "Custom Python function":
        if custom_path is None:
            raise ValueError("Choose a custom autofocus Python file")
        return load_custom_focus_measure(custom_path)
    raise ValueError(f"Unknown autofocus measure: {name}")


def load_custom_focus_measure(path: Path) -> FocusMeasure:
    """Load and validate focus_measure(image) from a trusted local Python file."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("The custom autofocus measure must be an existing .py file")
    module_name = f"cnc_microscope_custom_focus_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load custom autofocus file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "focus_measure", None)
    if not callable(function):
        raise ValueError("Custom file must define a callable focus_measure(image) function")

    def checked(image: np.ndarray) -> float:
        try:
            result = function(image)
        except Exception as exc:
            raise RuntimeError(f"Custom focus_measure failed: {exc}") from exc
        if not np.isscalar(result):
            raise ValueError("Custom focus_measure must return one numeric scalar, not an array or collection")
        try:
            score = float(result)
        except (TypeError, ValueError) as exc:
            raise ValueError("Custom focus_measure must return one numeric scalar") from exc
        if not math.isfinite(score):
            raise ValueError("Custom focus_measure must return a finite numeric value")
        return score

    return checked


def stack_positions(center_z: float, range_um: float, spacing_um: float) -> list[float]:
    if spacing_um <= 0:
        raise ValueError("Z spacing must be greater than zero")
    if range_um < 0:
        raise ValueError("Z range cannot be negative")
    half_mm = range_um / 2000.0
    spacing_mm = spacing_um / 1000.0
    count = max(1, int(round((2 * half_mm) / spacing_mm)) + 1)
    return [round(center_z - half_mm + index * (2 * half_mm / max(1, count - 1)), 9)
            for index in range(count)]

