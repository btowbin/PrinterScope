from pathlib import Path

import numpy as np
import pytest
import tifffile

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.models import Position, StagePoint
from cnc_microscope.motion import SimulatedMotionController


class PeakCamera:
    """Sharpness peaks at ``peak_z``; counts captures."""
    is_connected = True

    def __init__(self, motion, peak_z):
        self.motion, self.peak_z, self.count = motion, peak_z, 0

    def capture(self, exposure_ms):
        self.count += 1
        amplitude = max(0, 1000 - int(abs(self.motion.position.z - self.peak_z) * 15000))
        image = np.zeros((24, 24), dtype=np.uint16)
        image[:, 12:] = amplitude
        return image


def setup(peak_z, start_z=5.0):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(1, 2, start_z))
    return motion, PeakCamera(motion, peak_z)


def settings(tmp_path, **kwargs):
    base = dict(repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path, autofocus=True,
                save_stack=False, z_range_um=200, z_spacing_um=25, focus_measure_name="Tenengrad",
                two_step_autofocus=True, fine_range_um=50, fine_spacing_um=5)
    base.update(kwargs)
    return AcquisitionSettings(**base)


def test_fine_scan_resolves_a_peak_between_coarse_planes(tmp_path):
    motion, camera = setup(peak_z=5.0037)
    coarse_only = AcquisitionRunner(motion, camera).autofocus_here(
        settings(tmp_path, two_step_autofocus=False))
    assert coarse_only.z == pytest.approx(5.0)
    camera.count = 0
    best = AcquisitionRunner(motion, camera).autofocus_here(settings(tmp_path))
    assert best.z == pytest.approx(5.005)          # 5 µm fine spacing finds the nearer plane
    assert camera.count == 9 + 11                  # coarse planes plus fine planes
    assert motion.position.z == pytest.approx(5.005)


def test_coarse_peak_at_the_edge_lets_the_fine_scan_extend_beyond(tmp_path):
    motion, camera = setup(peak_z=5.12)             # outside the coarse range 4.9 to 5.1
    messages = []
    best = AcquisitionRunner(motion, camera).autofocus_here(
        settings(tmp_path), lambda message, *_: messages.append(message))
    assert best.z == pytest.approx(5.12)
    assert any("edge of the scan range" in m for m in messages)
    assert any("starting fine scan" in m for m in messages)


def test_acquisition_saves_coarse_and_fine_stacks_and_final_image(tmp_path):
    motion, camera = setup(peak_z=5.0037)
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 1, 2, 5.0)], settings(tmp_path, save_stack=True), lambda *a: None)
    coarse = tifffile.imread(tmp_path / "Point0001_Time00001_ZStack.tif")
    fine = tifffile.imread(tmp_path / "Point0001_Time00001_ZStackFine.tif")
    final = tifffile.imread(tmp_path / "Point0001_Time00001.tif")
    assert coarse.shape[0] == 9 and fine.shape[0] == 11
    sharpest = int(np.argmax([plane.max() for plane in fine]))
    assert sharpest == 4                             # Z 5.005 in the high-to-low scan 5.025 .. 4.975
    assert np.array_equal(final, fine[sharpest])
    assert motion.position.z == pytest.approx(5.005)


def test_single_step_still_writes_one_stack_without_fine_file(tmp_path):
    motion, camera = setup(peak_z=5.0)
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 1, 2, 5.0)],
        settings(tmp_path, two_step_autofocus=False, save_stack=True), lambda *a: None)
    assert (tmp_path / "Point0001_Time00001_ZStack.tif").exists()
    assert not (tmp_path / "Point0001_Time00001_ZStackFine.tif").exists()


@pytest.mark.parametrize("kwargs, message", [
    (dict(fine_spacing_um=0), "greater than zero"),
    (dict(fine_range_um=-5), "greater than zero"),
    (dict(fine_spacing_um=25), "smaller than the coarse spacing"),
    (dict(fine_range_um=40), "at least twice the coarse spacing"),
])
def test_two_step_settings_are_validated(tmp_path, kwargs, message):
    motion, camera = setup(peak_z=5.0)
    bad = settings(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=message):
        AcquisitionRunner(motion, camera).run([StagePoint("P", 1, 2, 5)], bad, lambda *a: None)
    with pytest.raises(ValueError, match=message):
        AcquisitionRunner(motion, camera).autofocus_here(bad)
    assert camera.count == 0                         # nothing was captured or moved


def test_constant_scores_in_coarse_scan_stop_before_the_fine_scan(tmp_path):
    motion, camera = setup(peak_z=50.0)             # focus peak far away: all planes identical
    with pytest.raises(RuntimeError, match="same score"):
        AcquisitionRunner(motion, camera).autofocus_here(settings(tmp_path))
    assert camera.count == 9                         # only the coarse scan ran
