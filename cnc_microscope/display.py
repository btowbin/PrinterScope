"""Display-only helpers: reduction, autoscale range, histogram and 8-bit mapping.

Nothing here changes the data that is saved to disk.
"""
from __future__ import annotations

import numpy as np


def reduce_for_display(image: np.ndarray, factor: int = 4) -> np.ndarray:
    """Block-average a frame so previews and histograms stay fast on 6 MP images."""
    if factor <= 1 or min(image.shape) < factor * 16:
        return image.astype(np.float32, copy=False)
    h = image.shape[0] // factor * factor
    w = image.shape[1] // factor * factor
    blocks = image[:h, :w].astype(np.float32).reshape(h // factor, factor, w // factor, factor)
    return blocks.mean(axis=(1, 3))


def auto_range(reduced: np.ndarray, limit: int, low_pct: float = 0.5,
               high_pct: float = 99.5) -> tuple[float, float]:
    lo, hi = np.percentile(reduced, (low_pct, high_pct))
    if hi <= lo:
        # A uniform frame has a meaningful brightness; show it on the full scale
        # instead of rendering it black.
        return 0.0, float(limit)
    return float(lo), float(hi)


def histogram_counts(reduced: np.ndarray, limit: int, bins: int = 256) -> np.ndarray:
    counts, _ = np.histogram(reduced, bins=bins, range=(0, limit + 1))
    return counts


def to_uint8(image: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if hi <= lo:
        raise ValueError("Display maximum must be greater than the minimum")
    scaled = (image.astype(np.float32, copy=False) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)
