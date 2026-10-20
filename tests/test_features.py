from pathlib import Path

import numpy as np
import pytest
import tifffile

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.camera import DahengCamera, SimulatedCamera
from cnc_microscope.image_io import DEFLATE, JPEG2000, TIFF_FORMATS, UNCOMPRESSED, write_tiff
from cnc_microscope.models import Position, StagePoint, update_points
from cnc_microscope.motion import SimulatedMotionController
from cnc_microscope.presets import default_presets, with_field


LOSSLESS_FORMATS = (UNCOMPRESSED, DEFLATE)


def _smooth_noisy(bits: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:128, 0:160]
    top = (1 << bits) - 1
    base = 0.5 * top + 0.3 * top * np.sin(x / 17.0) * np.cos(y / 23.0)
    return (base + rng.normal(0, top / 170, base.shape)).clip(0, top).astype(np.uint16)


@pytest.mark.parametrize("fmt", LOSSLESS_FORMATS)
@pytest.mark.parametrize("bits", [10, 14])
def test_lossless_tiff_formats_are_bit_exact(tmp_path: Path, fmt, bits):
    rng = np.random.default_rng(1)
    image = rng.integers(0, 1 << bits, (64, 80)).astype(np.uint16)
    path = tmp_path / "a.tif"
    write_tiff(path, image, bits, fmt)
    assert np.array_equal(tifffile.imread(path), image)
    with tifffile.TiffFile(path) as tif:
        compression = tif.pages[0].compression.name
    assert compression == {UNCOMPRESSED: "NONE", DEFLATE: "ADOBE_DEFLATE"}[fmt]


def test_jpeg2000_is_labelled_lossy():
    assert "lossy" in JPEG2000.lower() and "lossless" not in JPEG2000.lower()


@pytest.mark.parametrize("bits", [10, 14])
def test_jpeg2000_is_lossy_and_quality_follows_target_psnr(tmp_path: Path, bits):
    image = _smooth_noisy(bits)
    top = (1 << bits) - 1
    results = {}
    for target in (40.0, 55.0):
        path = tmp_path / f"q{int(target)}.tif"
        write_tiff(path, image, bits, JPEG2000, jpeg2000_psnr_db=target)
        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression.name == "JPEG2000"
        decoded = tifffile.imread(path).astype(float)
        mse = np.mean((decoded - image.astype(float)) ** 2)
        results[target] = (10 * np.log10(top ** 2 / mse) if mse else np.inf, path.stat().st_size)
    assert not np.array_equal(tifffile.imread(tmp_path / "q40.tif"), image)  # genuinely lossy
    for target, (psnr, _size) in results.items():
        assert psnr >= target - 3, (target, psnr)  # reaches roughly the requested quality
    assert results[55.0][0] > results[40.0][0]
    assert results[55.0][1] > results[40.0][1]  # better quality costs bytes


def test_jpeg2000_quality_is_validated(tmp_path: Path):
    with pytest.raises(ValueError):
        write_tiff(tmp_path / "x.tif", _smooth_noisy(10), 10, JPEG2000, jpeg2000_psnr_db=5)


def test_compression_shrinks_smooth_images(tmp_path: Path):
    image = np.tile(np.arange(256, dtype=np.uint16) * 4, (256, 1))
    sizes = {}
    for fmt in TIFF_FORMATS:
        path = tmp_path / f"{TIFF_FORMATS.index(fmt)}.tif"
        write_tiff(path, image, 10, fmt)
        sizes[fmt] = path.stat().st_size
    assert sizes[DEFLATE] < sizes[UNCOMPRESSED]
    assert sizes[JPEG2000] < sizes[UNCOMPRESSED]


def test_acquisition_saves_14bit_jpeg2000_stack(tmp_path: Path):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(1, 2, 5))
    camera = SimulatedCamera(lambda: motion.position.z, bit_depth=14)
    camera.connect()
    settings = AcquisitionSettings(
        repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path,
        autofocus=True, save_stack=True, z_range_um=100, z_spacing_um=50,
        tiff_format=JPEG2000, bit_depth=14,
    )
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 1, 2, 5)], settings, lambda *a: None)
    image = tifffile.imread(tmp_path / "Point0001_Time00001.tif")
    stack = tifffile.imread(tmp_path / "Point0001_Time00001_ZStack.tif")
    assert 1023 < int(image.max()) <= 16383  # genuinely 14-bit scaled data
    assert stack.shape[0] == 3
    # JPEG 2000 is lossy here, so compare within the quantisation error.
    assert np.abs(image.astype(int) - stack[1].astype(int)).max() < 300


def test_simulated_camera_follows_bit_depth():
    camera = SimulatedCamera(bit_depth=10)
    camera.connect()
    assert int(camera.capture(0.01).max()) <= 1023
    camera.set_bit_depth(14)
    assert 1023 < int(camera.capture(0.01).max()) <= 16383
    with pytest.raises(ValueError):
        camera.set_bit_depth(12)


def test_daheng_bit_depth_switch_and_unsupported_14bit_restores_format():
    applied = []

    class Entry:
        MONO10, MONO10_PACKED, MONO14, MONO14_PACKED = "m10", "m10p", "m14", "m14p"

    class PixelFormat:
        def set(self, value):
            if value.startswith("m14"):
                raise RuntimeError("not supported")
            applied.append(value)

    camera = DahengCamera()
    camera._gx = type("GX", (), {"GxPixelFormatEntry": Entry})
    camera.device = type("Device", (), {"PixelFormat": PixelFormat()})()
    with pytest.raises(RuntimeError, match="14-bit"):
        camera.set_bit_depth(14)
    assert camera.bit_depth == 10 and applied[-1] == "m10"


def test_presets_edit_and_custom_is_independent():
    presets = default_presets()
    presets["Custom"] = with_field(presets["Custom"], "x_step", 0.37)
    coarse = with_field(presets["Coarse"], "z_speed", 15)
    assert coarse.z_speed == 15 and presets["Custom"].x_step == 0.37
    assert default_presets()["Coarse"].z_speed == 10  # defaults untouched
    with pytest.raises(ValueError):
        with_field(presets["Fine"], "x_step", 0)


POINTS = [StagePoint("Point 1", 10, 10, 5), StagePoint("Point 2", 20, 30, 6)]
CURRENT = Position(11, 12, 4)


def coords(points):
    return [(p.x, p.y, p.z) for p in points]


def test_update_selected_point_modes():
    assert coords(update_points(POINTS, 0, CURRENT, "point_xyz")) == [(11, 12, 4), (20, 30, 6)]
    assert coords(update_points(POINTS, 0, CURRENT, "point_xy")) == [(11, 12, 5), (20, 30, 6)]
    assert coords(update_points(POINTS, 0, CURRENT, "point_z")) == [(10, 10, 4), (20, 30, 6)]


def test_shift_all_points_modes():
    assert coords(update_points(POINTS, 0, CURRENT, "all_xyz")) == [(11, 12, 4), (21, 32, 5)]
    assert coords(update_points(POINTS, 0, CURRENT, "all_xy")) == [(11, 12, 5), (21, 32, 6)]
    assert coords(update_points(POINTS, 0, CURRENT, "all_z")) == [(10, 10, 4), (20, 30, 5)]
    assert coords(POINTS) == [(10, 10, 5), (20, 30, 6)]  # input unchanged


def test_update_points_rejects_bad_input():
    with pytest.raises(ValueError):
        update_points(POINTS, 0, CURRENT, "bogus")
    with pytest.raises(IndexError):
        update_points(POINTS, 5, CURRENT, "all_xyz")


class Recorder(SimulatedMotionController):
    def __init__(self):
        super().__init__()
        self.log = []

    def move_to(self, position, feed_mm_min=1200.0):
        self.log.append((position, feed_mm_min))
        super().move_to(position, feed_mm_min)


def make(start):
    motion = Recorder()
    motion.connect()
    motion.home()
    motion.move_to(start)
    motion.log.clear()
    return motion


def test_reposition_up_moves_z_first_then_xy():
    motion = make(Position(5, 5, 2))
    motion.reposition(Position(50, 60, 10), 6000, 1200)
    assert motion.log == [(Position(5, 5, 10), 1200), (Position(50, 60, 10), 6000)]


def test_reposition_down_moves_xy_first_then_z():
    motion = make(Position(5, 5, 10))
    motion.reposition(Position(50, 60, 2), 6000, 1200)
    assert motion.log == [(Position(50, 60, 10), 6000), (Position(50, 60, 2), 1200)]


def test_reposition_skips_noop_moves_and_validates_first():
    motion = make(Position(5, 5, 3))
    motion.reposition(Position(5, 5, 8))
    assert motion.log == [(Position(5, 5, 8), 1200.0)]
    motion.log.clear()
    with pytest.raises(ValueError):
        motion.reposition(Position(9999, 5, 3))
    assert motion.log == []  # nothing moved before the limit error


def test_display_helpers():
    from cnc_microscope.display import auto_range, histogram_counts, reduce_for_display, to_uint8

    rng = np.random.default_rng(2)
    image = rng.integers(100, 600, (256, 320)).astype(np.uint16)
    reduced = reduce_for_display(image)
    assert reduced.shape == (64, 80)
    lo, hi = auto_range(reduced, 1023)
    assert 100 <= lo < hi <= 600
    counts = histogram_counts(reduced, 1023)
    assert counts.shape == (256,) and counts.sum() == reduced.size
    out = to_uint8(np.array([[0, 100, 200]], dtype=np.uint16), 100, 200)
    assert out.tolist() == [[0, 0, 255]]
    assert auto_range(np.full((32, 32), 500, dtype=np.float32), 1023) == (0.0, 1023.0)
    with pytest.raises(ValueError):
        to_uint8(image, 5, 5)
