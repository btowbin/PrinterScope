from pathlib import Path
from types import SimpleNamespace

import numpy as np
import tifffile

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.autofocus import (
    STANDARD_FOCUS_MEASURES,
    get_focus_measure,
    load_custom_focus_measure,
    stack_positions,
    tenengrad,
)
from cnc_microscope.camera import DahengCamera, SimulatedCamera
from cnc_microscope.models import ImageROI, Position, StagePoint
from cnc_microscope.motion import SimulatedMotionController
from cnc_microscope.well_detection import gradient_centroid, load_custom_well_detector


class RecordingMotion(SimulatedMotionController):
    def __init__(self):
        super().__init__()
        self.targets = []

    def move_to(self, position, feed_mm_min=1200.0):
        self.targets.append(position)
        super().move_to(position, feed_mm_min)


def test_stack_positions_are_centered():
    positions = stack_positions(10.0, range_um=100, spacing_um=25)
    assert positions[0] == 9.95
    assert positions[-1] == 10.05
    assert len(positions) == 5


def test_tenengrad_prefers_sharp_edges():
    sharp = np.zeros((100, 100), dtype=np.uint16)
    sharp[:, 50:] = 1023
    blurred = sharp.copy().astype(np.float32)
    for _ in range(15):
        blurred = (blurred + np.roll(blurred, 1, 1) + np.roll(blurred, -1, 1)) / 3
    assert tenengrad(sharp) > tenengrad(blurred)


def test_all_standard_focus_measures_return_finite_scores():
    image = np.arange(10000, dtype=np.uint16).reshape(100, 100) % 1024
    for name in STANDARD_FOCUS_MEASURES[:-1]:
        assert np.isfinite(get_focus_measure(name)(image))


def test_custom_focus_measure_contract(tmp_path: Path):
    custom = tmp_path / "custom_measure.py"
    custom.write_text(
        "def focus_measure(image):\n    return float(image.max() - image.min())\n",
        encoding="utf-8",
    )
    measure = load_custom_focus_measure(custom)
    image = np.array([[10, 25]], dtype=np.uint16)
    assert measure(image) == 15.0


def test_gradient_well_detector_finds_circular_center():
    height, width = 180, 220
    expected_x, expected_y = 116, 83
    yy, xx = np.mgrid[:height, :width]
    radius = np.hypot(xx - expected_x, yy - expected_y)
    image = np.where(radius < 55, 700, 150).astype(np.uint16)
    x, y = gradient_centroid(image)
    assert abs(x - expected_x) < 1.0
    assert abs(y - expected_y) < 1.0


def test_custom_well_detector_contract(tmp_path: Path):
    custom = tmp_path / "well_detector.py"
    custom.write_text(
        "def find_well_center(image):\n"
        "    return image.shape[1] / 2, image.shape[0] / 2\n",
        encoding="utf-8",
    )
    detector = load_custom_well_detector(custom)
    assert detector(np.zeros((20, 30), dtype=np.uint16)) == (15.0, 10.0)


def test_image_roi_crop_and_remaining_extent():
    image = np.arange(80, dtype=np.uint16).reshape(8, 10)
    explicit = ImageROI(x=2, y=1, width=4, height=3).crop(image)
    remaining = ImageROI(x=7, y=6, width=0, height=0).crop(image)
    centered = ImageROI(width=4, height=2, centered=True).crop(image)
    assert explicit.shape == (3, 4)
    assert explicit[0, 0] == image[1, 2]
    assert remaining.shape == (2, 3)
    assert centered.shape == (2, 4)
    assert centered[0, 0] == image[3, 3]


def test_backlash_approach_finishes_xyz_in_fixed_directions():
    motion = RecordingMotion()
    motion.connect()
    motion.home()
    target = Position(10, 20, 5)
    motion.approach_to(target, xy_preload_mm=0.25, z_preload_mm=0.05)
    assert motion.targets[-3].x < motion.targets[-2].x
    assert motion.targets[-3].y < motion.targets[-2].y
    assert motion.targets[-2].z > motion.targets[-1].z
    assert motion.position == target


def test_simulated_acquisition_writes_named_10bit_tiff(tmp_path: Path):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(1, 2, 5))
    camera = SimulatedCamera(lambda: motion.position.z)
    camera.connect()
    runner = AcquisitionRunner(motion, camera)
    settings = AcquisitionSettings(
        repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path,
        autofocus=False, save_stack=False, z_range_um=100, z_spacing_um=25,
        roi=ImageROI(10, 20, 100, 80),
    )
    runner.run([StagePoint("Point 1", 1, 2, 5)], settings, lambda *args: None)
    path = tmp_path / "Point0001_Time00001.tif"
    assert path.exists()
    image = tifffile.imread(path)
    assert image.dtype == np.uint16
    assert int(image.max()) <= 1023
    assert image.shape == (80, 100)


def test_autofocus_final_image_matches_selected_stack_plane(tmp_path: Path):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(1, 2, 5))

    class PositionEncodedCamera:
        is_connected = True

        def capture(self, exposure_ms):
            amplitude = max(0, 1000 - int(abs(motion.position.z - 5.0) * 15000))
            image = np.zeros((24, 24), dtype=np.uint16)
            image[:, 12:] = amplitude
            return image

    settings = AcquisitionSettings(
        repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path,
        autofocus=True, save_stack=True, z_range_um=100, z_spacing_um=25,
        focus_measure_name="Tenengrad",
    )
    AcquisitionRunner(motion, PositionEncodedCamera()).run(
        [StagePoint("Point 1", 1, 2, 5)], settings, lambda *args: None,
    )

    stack = tifffile.imread(tmp_path / "Point0001_Time00001_ZStack.tif")
    final_image = tifffile.imread(tmp_path / "Point0001_Time00001.tif")
    assert np.array_equal(final_image, stack[2])
    assert np.isclose(motion.position.z, 5.0)


def test_daheng_capture_starts_triggers_and_stops_for_each_frame():
    operations = []

    class Feature:
        def set(self, value):
            operations.append(("exposure", value))

    class Trigger:
        def send_command(self):
            operations.append("trigger")

    class Raw:
        def get_status(self):
            return 0

        def get_numpy_array(self):
            return np.full((3, 4), 511, dtype=np.uint16)

    class Stream:
        def get_image(self, timeout):
            operations.append("get_image")
            return Raw()

    class Device:
        ExposureTime = Feature()
        TriggerSoftware = Trigger()
        data_stream = [Stream()]

        def stream_on(self):
            operations.append("stream_on")

        def stream_off(self):
            operations.append("stream_off")

    camera = DahengCamera()
    camera.device = Device()
    camera._gx = SimpleNamespace(GxFrameStatusList=SimpleNamespace(SUCCESS=0))
    image = camera.capture(2.5)

    assert operations == [
        ("exposure", 2500.0), "stream_on", "trigger", "get_image", "stream_off",
    ]
    assert np.all(image == 511)


def test_well_centering_moves_xy_after_autofocus(tmp_path: Path):
    detector_file = tmp_path / "offset_well.py"
    detector_file.write_text(
        "def find_well_center(image):\n"
        "    return image.shape[1] / 2 + 10, image.shape[0] / 2 - 5\n",
        encoding="utf-8",
    )
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    camera = SimulatedCamera(lambda: motion.position.z)
    camera.connect()
    settings = AcquisitionSettings(
        repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path,
        autofocus=True, save_stack=False, z_range_um=0, z_spacing_um=10,
        center_well=True, well_detector_name="Custom Python function",
        custom_well_path=detector_file, well_x_um_per_pixel=10,
        well_y_um_per_pixel=10, max_center_correction_mm=1,
    )
    AcquisitionRunner(motion, camera).run(
        [StagePoint("Point 1", 10, 20, 5)], settings, lambda *args: None)
    assert np.isclose(motion.position.x, 10.105)
    assert np.isclose(motion.position.y, 19.955)
    assert np.isclose(motion.position.z, 5.0)

