import csv
import re

import numpy as np
import pytest

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.models import Position, StagePoint
from cnc_microscope.motion import SimulatedMotionController
from cnc_microscope.position_log import LOG_NAME


def settings(tmp_path, **kwargs):
    base = dict(repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path, autofocus=True,
                save_stack=False, z_range_um=200, z_spacing_um=20, stagger_autofocus=True,
                stagger_n=3)
    base.update(kwargs)
    return AcquisitionSettings(**base)


def schedule(n_positions, n_times, n, **kwargs):
    cfg = AcquisitionSettings(repeats=n_times, interval_s=0, exposure_ms=1, output_dir=None,
                              autofocus=True, save_stack=False, z_range_um=0, z_spacing_um=1,
                              stagger_autofocus=True, stagger_n=n, **kwargs)
    memory = {p: Position(0, 0, 0) for p in range(1, n_positions + 1)}
    return [[p for p in range(1, n_positions + 1)
             if AcquisitionRunner.should_autofocus(p, t, cfg, memory)]
            for t in range(1, n_times + 1)]


def test_schedule_matches_the_requested_pattern():
    # time 1 (t = 0): all positions; then t=1: 1, 1+n, ...; t=2: 2, 2+n, ...; and so on
    assert schedule(7, 5, 3) == [[1, 2, 3, 4, 5, 6, 7], [1, 4, 7], [2, 5], [3, 6], [1, 4, 7]]


def test_every_position_is_refocused_once_per_n_time_points():
    plan = schedule(10, 7, 3)                      # time points 2..4 form one full cycle
    cycle = [p for step in plan[1:4] for p in step]
    assert sorted(cycle) == list(range(1, 11))


def test_n_equals_one_focuses_everything_every_time():
    assert schedule(3, 3, 1) == [[1, 2, 3]] * 3


def test_without_stagger_or_autofocus():
    memory = {1: Position(0, 0, 0)}
    on = AcquisitionSettings(repeats=2, interval_s=0, exposure_ms=1, output_dir=None,
                             autofocus=True, save_stack=False, z_range_um=0, z_spacing_um=1)
    off = AcquisitionSettings(repeats=2, interval_s=0, exposure_ms=1, output_dir=None,
                              autofocus=False, save_stack=False, z_range_um=0, z_spacing_um=1,
                              stagger_autofocus=True)
    assert AcquisitionRunner.should_autofocus(1, 2, on, memory) is True
    assert AcquisitionRunner.should_autofocus(1, 1, off, memory) is False


def test_a_position_without_a_remembered_focus_is_always_focused():
    cfg = AcquisitionSettings(repeats=3, interval_s=0, exposure_ms=1, output_dir=None,
                              autofocus=True, save_stack=False, z_range_um=0, z_spacing_um=1,
                              stagger_autofocus=True, stagger_n=5)
    assert AcquisitionRunner.should_autofocus(2, 3, cfg, {}) is True


class DriftingCamera:
    """The sharpest plane moves up 20 µm per time point; counts captures."""
    is_connected = True

    def __init__(self, motion):
        self.motion, self.peak_z, self.captures = motion, 5.0, 0

    def capture(self, exposure_ms):
        self.captures += 1
        amplitude = max(0, 1000 - int(abs(self.motion.position.z - self.peak_z) * 15000))
        image = np.zeros((24, 24), dtype=np.uint16)
        image[:, 12:] = amplitude
        return image


def run(tmp_path, **kwargs):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    camera = DriftingCamera(motion)

    def callback(message, *_):
        match = re.search(r"time (\d+)/", message)
        if match:
            camera.peak_z = 5.0 + 0.02 * (int(match.group(1)) - 1)

    points = [StagePoint(f"Point {i}", 10 * i, 5, 5.0) for i in range(1, 5)]
    AcquisitionRunner(motion, camera).run(points, settings(tmp_path, repeats=4, stagger_n=2, **kwargs), callback)
    with open(tmp_path / LOG_NAME, newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return rows, camera


def test_skipped_positions_are_imaged_at_their_last_focus_and_logged(tmp_path):
    rows, _ = run(tmp_path)
    table = {(int(r["time_index"]), int(r["point_index"])): (r["autofocus"], float(r["z_mm"]))
             for r in rows}
    # n = 2: time 1 all; time 2 (t=1) positions 1,3; time 3 (t=2) positions 2,4; time 4 (t=3) 1,3
    assert [[p for p in range(1, 5) if table[(t, p)][0] == "yes"] for t in range(1, 5)] == [
        [1, 2, 3, 4], [1, 3], [2, 4], [1, 3]]
    for p in range(1, 5):                           # every position starts focused at Z ~ 5.0
        assert table[(1, p)][1] == pytest.approx(5.0, abs=0.011)
    assert table[(2, 1)][1] == pytest.approx(5.02, abs=0.011)   # refocused: followed the drift
    assert table[(2, 2)][1] == table[(1, 2)][1]                 # skipped: stayed at last focus
    assert table[(3, 2)][1] == pytest.approx(5.04, abs=0.011)   # refocused one time point later
    assert table[(3, 1)][1] == table[(2, 1)][1]
    assert table[(4, 1)][1] == pytest.approx(5.06, abs=0.011) and table[(4, 2)][1] == table[(3, 2)][1]


def test_skipped_time_points_capture_a_single_frame_and_no_stack(tmp_path):
    rows, camera = run(tmp_path, save_stack=True)
    skipped = [r for r in rows if r["autofocus"] == "no"]
    assert len(skipped) == 6
    assert all(r["zstack_file"] == "" for r in skipped)
    assert all((tmp_path / r["zstack_file"]).exists() for r in rows if r["autofocus"] == "yes")
    assert not any(f.name.startswith("Point0002_Time00002") and "ZStack" in f.name
                   for f in tmp_path.iterdir())
    assert all((tmp_path / r["image_file"]).exists() for r in rows)   # every image still saved
    # 10 focused images (11 planes + final frame each) + 6 plain frames
    assert camera.captures == 10 * (11 + 1) + 6


def test_stagger_validation(tmp_path):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    with pytest.raises(ValueError, match="at least 1"):
        AcquisitionRunner(motion, DriftingCamera(motion)).run(
            [StagePoint("P", 1, 1, 5)], settings(tmp_path, stagger_n=0), lambda *a: None)
