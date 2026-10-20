"""Example custom well detector for the CNC microscope application."""

import numpy as np


def find_well_center(image: np.ndarray) -> tuple[float, float]:
    """Return the well center as zero-based (x_pixel, y_pixel) coordinates."""
    pixels = image.astype(np.float32, copy=False)
    threshold = np.percentile(pixels, 25)
    mask = pixels <= threshold
    if not np.any(mask):
        raise ValueError("No dark well region found")
    y_pixels, x_pixels = np.nonzero(mask)
    return float(np.mean(x_pixels)), float(np.mean(y_pixels))

