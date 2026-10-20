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

