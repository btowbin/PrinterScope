"""Example custom autofocus measure for the CNC microscope application."""

import numpy as np


def focus_measure(image: np.ndarray) -> float:
    """Return one finite scalar score; larger values must mean better focus."""
    pixels = image.astype(np.float32, copy=False)
    horizontal_edges = pixels[:, 1:] - pixels[:, :-1]
    vertical_edges = pixels[1:, :] - pixels[:-1, :]
    return float(np.mean(horizontal_edges ** 2) + np.mean(vertical_edges ** 2))

