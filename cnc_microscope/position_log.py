"""CSV log of where every acquired image was taken."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

LOG_NAME = "positions.csv"
HEADER = (
    "run_started", "timestamp", "elapsed_s", "time_index", "point_index", "point_name",
    "planned_x_mm", "planned_y_mm", "planned_z_mm", "x_mm", "y_mm", "z_mm",
    "image_file", "zstack_file", "zstack_fine_file", "autofocus",
)


class PositionLog:
    """Appends one row per saved image; the file is closed after every row.

    Closing each time means the log survives a crash, and an editor such as Excel
    that opens the file between rows only delays the write instead of corrupting it.
    """

    def __init__(self, path: Path, run_started: datetime | None = None):
        self.path = Path(path)
        self.run_started = (run_started or datetime.now()).isoformat(timespec="seconds")

    def append(self, *, elapsed_s: float, time_index: int, point_index: int, point_name: str,
               planned: tuple[float, float, float], actual: tuple[float, float, float],
               image_file: str, zstack_file: str = "", zstack_fine_file: str = "",
               autofocus: str = "no") -> None:
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        row = [self.run_started, datetime.now().isoformat(timespec="seconds"),
               f"{elapsed_s:.2f}", time_index, point_index, point_name,
               *(f"{value:.4f}" for value in planned), *(f"{value:.4f}" for value in actual),
               image_file, zstack_file, zstack_fine_file, autofocus]
        with open(self.path, "a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if is_new:
                writer.writerow(HEADER)
            writer.writerow(row)
