import csv
from pathlib import Path

import numpy as np
import pytest

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.models import Position, StagePoint
from cnc_microscope.motion import SimulatedMotionController
from cnc_microscope.position_log import HEADER, LOG_NAME


class PeakCamera:
    is_connected = True

    def __init__(self, motion, peak_z=5.0037):
        self.motion, self.peak_z = motion, peak_z

    def capture(self, exposure_ms):
        amplitude = max(0, 1000 - int(abs(self.motion.position.z - self.peak_z) * 15000))
        image = np.zeros((24, 24), dtype=np.uint16)
        image[:, 12:] = amplitude
        return image


def setup():
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(1, 2, 5.0))
    return motion, PeakCamera(motion)


def settings(tmp_path, **kwargs):
    base = dict(repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path, autofocus=False,
                save_stack=False, z_range_um=100, z_spacing_um=25)
    base.update(kwargs)
    return AcquisitionSettings(**base)


def rows(tmp_path):
    with open(tmp_path / LOG_NAME, newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_one_row_per_image_with_planned_and_actual_positions(tmp_path):
    motion, camera = setup()
    points = [StagePoint("Point 1", 10, 20, 5.0), StagePoint("Point 2", 30, 40, 6.5)]
    AcquisitionRunner(motion, camera).run(points, settings(tmp_path, repeats=2), lambda *a: None)
    data = rows(tmp_path)
    assert list(data[0].keys()) == list(HEADER)
    assert [(r["time_index"], r["point_index"], r["point_name"]) for r in data] == [
        ("1", "1", "Point 1"), ("1", "2", "Point 2"), ("2", "1", "Point 1"), ("2", "2", "Point 2")]
    second = data[1]
    assert (second["planned_x_mm"], second["planned_y_mm"], second["planned_z_mm"]) == ("30.0000", "40.0000", "6.5000")
    assert (second["x_mm"], second["y_mm"], second["z_mm"]) == ("30.0000", "40.0000", "6.5000")
    assert second["image_file"] == "Point0002_Time00001.tif" and second["zstack_file"] == ""
    assert float(data[-1]["elapsed_s"]) >= float(data[0]["elapsed_s"])
    assert len({r["run_started"] for r in data}) == 1


def test_autofocus_and_centering_are_reflected_in_the_actual_position(tmp_path):
    motion, camera = setup()
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 1, 2, 5.0)],
        settings(tmp_path, autofocus=True, save_stack=True, z_range_um=200, z_spacing_um=25,
                 two_step_autofocus=True, fine_range_um=50, fine_spacing_um=5),
        lambda *a: None)
    (row,) = rows(tmp_path)
    assert row["planned_z_mm"] == "5.0000" and row["z_mm"] == "5.0050"   # moved to the sharpest plane
    assert row["zstack_file"] == "Point0001_Time00001_ZStack.tif"
    assert row["zstack_fine_file"] == "Point0001_Time00001_ZStackFine.tif"
    assert (tmp_path / row["image_file"]).exists() and (tmp_path / row["zstack_fine_file"]).exists()


def test_a_second_run_appends_with_a_single_header(tmp_path):
    motion, camera = setup()
    for _ in range(2):
        AcquisitionRunner(motion, camera).run(
            [StagePoint("Point 1", 1, 2, 5.0)], settings(tmp_path), lambda *a: None)
    text = (tmp_path / LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert text[0].startswith("run_started") and sum(l.startswith("run_started") for l in text) == 1
    assert len(text) == 3


def test_an_unwritable_log_warns_once_but_never_stops_the_acquisition(tmp_path):
    (tmp_path / LOG_NAME).mkdir()                       # cannot be opened as a file
    motion, camera = setup()
    messages = []
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 1, 2, 5.0), StagePoint("Point 2", 3, 4, 5.0)],
        settings(tmp_path, repeats=2), lambda message, *_: messages.append(message))
    assert sum("Could not write positions.csv" in m for m in messages) == 1
    assert len(list(tmp_path.glob("Point*_Time*.tif"))) == 4   # all images were still saved
    assert messages[-1] == "Acquisition complete"
