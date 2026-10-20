from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float


@dataclass
class StagePoint:
    name: str
    x: float
    y: float
    z: float

    @property
    def position(self) -> Position:
        return Position(self.x, self.y, self.z)


@dataclass(frozen=True)
class ImageROI:
    """Software image ROI; centered mode calculates its origin from the frame size."""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    centered: bool = False

    def crop(self, image: np.ndarray) -> np.ndarray:
        if image.ndim != 2:
            raise ValueError("ROI requires a two-dimensional monochrome image")
        if self.x < 0 or self.y < 0 or self.width < 0 or self.height < 0:
            raise ValueError("ROI X, Y, width and height cannot be negative")
        image_height, image_width = image.shape
        width = self.width or (image_width if self.centered else image_width - self.x)
        height = self.height or (image_height if self.centered else image_height - self.y)
        x = (image_width - width) // 2 if self.centered else self.x
        y = (image_height - height) // 2 if self.centered else self.y
        if width <= 0 or height <= 0:
            raise ValueError("ROI does not contain any pixels")
        if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
            raise ValueError(
                f"ROI ({x}, {y}, {width}, {height}) exceeds the "
                f"{image_width} × {image_height} camera image"
            )
        return image[y:y + height, x:x + width]


def save_points(path: Path, points: list[StagePoint]) -> None:
    path.write_text(json.dumps([asdict(p) for p in points], indent=2), encoding="utf-8")


def load_points(path: Path) -> list[StagePoint]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [StagePoint(**item) for item in data]



UPDATE_MODES = ("point_xyz", "point_xy", "point_z", "all_xyz", "all_xy", "all_z")


def update_points(points: list[StagePoint], index: int, current: Position,
                  mode: str) -> list[StagePoint]:
    """Return a new point list after re-teaching or shifting positions.

    ``point_*`` modes copy the chosen axes of ``current`` onto points[index].
    ``all_*`` modes compute the shift between ``current`` and the stored
    points[index] and apply the same shift to every point on the chosen axes.
    """
    if mode not in UPDATE_MODES:
        raise ValueError(f"Unknown update mode: {mode}")
    if not 0 <= index < len(points):
        raise IndexError("No position selected")
    scope, axes = mode.split("_", 1)
    use_xy, use_z = "xy" in axes, axes.endswith("z")
    reference = points[index]
    dx, dy, dz = (current.x - reference.x, current.y - reference.y,
                  current.z - reference.z)
    result: list[StagePoint] = []
    for i, p in enumerate(points):
        if scope == "point" and i != index:
            result.append(StagePoint(p.name, p.x, p.y, p.z))
            continue
        result.append(StagePoint(
            p.name,
            p.x + dx if use_xy else p.x,
            p.y + dy if use_xy else p.y,
            p.z + dz if use_z else p.z,
        ))
    return result


class PointsHistory:
    """Undo history for the positions list: snapshots taken *before* each change."""

    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self._stack: list[tuple[str, list[StagePoint]]] = []

    def record(self, label: str, points: list[StagePoint]) -> None:
        self._stack.append((label, [StagePoint(p.name, p.x, p.y, p.z) for p in points]))
        del self._stack[:-self.limit]

    def can_undo(self) -> bool:
        return bool(self._stack)

    def next_label(self) -> str | None:
        return self._stack[-1][0] if self._stack else None

    def undo(self) -> tuple[str, list[StagePoint]]:
        """Pop the latest snapshot: (what was undone, the list as it was before)."""
        if not self._stack:
            raise IndexError("Nothing to undo")
        return self._stack.pop()

    def clear(self) -> None:
        self._stack.clear()

