import time

import numpy as np
import pytest

from cnc_microscope.acquisition import AcquisitionRunner, AcquisitionSettings
from cnc_microscope.gamepad import (
    BUTTON_BITS, ButtonTracker, GamepadConfig, PadState, actions_for, axis_response,
    decode_state, next_speed_level, radial_response, velocity_from_state,
)
from cnc_microscope.jog import JogRunner, axis_target
from cnc_microscope.models import Position
from cnc_microscope.motion import SimulatedMotionController
from test_marlin_readback import make as make_marlin


def pad(lx=0.0, ly=0.0, rx=0.0, ry=0.0, buttons=()):
    return PadState(frozenset(buttons), lx, ly, rx, ry)


def test_decode_state_buttons_and_axes():
    mask = BUTTON_BITS["A"] | BUTTON_BITS["LB"] | BUTTON_BITS["DPAD_UP"]
    state = decode_state(mask, 32767, -32768, 0, 16384)
    assert state.buttons == {"A", "LB", "DPAD_UP"}
    assert state.lx == 1.0 and state.ly == -1.0 and state.rx == 0.0 and 0.49 < state.ry < 0.51


def test_deadzone_and_dosage_curve():
    cfg = GamepadConfig()
    assert radial_response(0.1, 0.1, cfg.deadzone, cfg.exponent) == (0.0, 0.0)
    light = axis_response(0.4, cfg.deadzone, cfg.exponent)
    firm = axis_response(0.8, cfg.deadzone, cfg.exponent)
    full = axis_response(1.0, cfg.deadzone, cfg.exponent)
    assert 0 < light < firm < full == pytest.approx(1.0)
    assert light < 0.2  # a gentle push is much slower than linear
    # direction is preserved on the diagonal and the magnitude never exceeds 1
    x, y = radial_response(1.0, 1.0, cfg.deadzone, cfg.exponent)
    assert x == pytest.approx(y) and np.hypot(x, y) == pytest.approx(1.0)


def test_velocity_scaling_xy_fast_z_slow_and_inversion():
    cfg = GamepadConfig(z_speed_scale=0.25)
    vx, vy, vz = velocity_from_state(pad(lx=1.0, ry=1.0), cfg, xy_speed=20.0, z_speed=5.0)
    assert vx == pytest.approx(20.0) and vy == 0.0 and vz == pytest.approx(1.25)
    inverted = GamepadConfig(invert_x=True, invert_z=True)
    vx, _, vz = velocity_from_state(pad(lx=1.0, ry=1.0), inverted, 20.0, 5.0)
    assert vx < 0 and vz < 0


def test_config_validation():
    with pytest.raises(ValueError):
        GamepadConfig(deadzone=0.95).validate()
    with pytest.raises(ValueError):
        GamepadConfig(mapping={"capture": "Z9"}).validate()
    GamepadConfig().validate()


def test_button_press_once_and_hold_repeat_for_steps_only():
    cfg = GamepadConfig(repeat_delay_s=0.5, repeat_interval_s=0.1)
    tracker = ButtonTracker(cfg)
    assert set(tracker.update({"A", "DPAD_LEFT"}, 0.0)) == {"A", "DPAD_LEFT"}
    assert tracker.update({"A", "DPAD_LEFT"}, 0.3) == []          # before the repeat delay
    assert tracker.update({"A", "DPAD_LEFT"}, 0.6) == ["DPAD_LEFT"]  # A never repeats
    assert tracker.update({"A", "DPAD_LEFT"}, 0.65) == []
    assert tracker.update({"A", "DPAD_LEFT"}, 0.75) == ["DPAD_LEFT"]
    assert tracker.update(set(), 0.8) == []
    assert tracker.update({"A"}, 0.9) == ["A"]                    # a new press fires again


def test_mapping_matches_the_requested_layout():
    cfg = GamepadConfig()
    assert actions_for(["A", "B", "X", "Y", "LB", "RB", "DPAD_UP"], cfg.mapping) == [
        "add_point", "autofocus", "capture", "speed", "z_down", "z_up", "xy_up"]


def test_speed_cycles_three_levels_and_skips_custom():
    assert [next_speed_level(x) for x in ("Coarse", "Fine", "Super fine")] == [
        "Fine", "Super fine", "Coarse"]
    assert next_speed_level("Custom") == "Coarse"


def test_axis_target_respects_bounds():
    assert axis_target(5.0, 10.0, 0.1, 0.0, 100.0) == pytest.approx(6.0)
    assert axis_target(99.9, 10.0, 0.1, 0.0, 100.0) == 100.0
    assert axis_target(100.0, 10.0, 0.1, 0.0, 100.0) == 100.0
    assert axis_target(0.0, -10.0, 0.1, 0.0, 100.0) == 0.0
    assert axis_target(5.0, 0.0, 0.1, 10.0, 100.0) == 5.0       # at rest: never pulled to a bound
    assert axis_target(5.0, -1.0, 0.1, 10.0, 100.0) == 5.0      # below a raised floor: no further down


def homed_sim(start=(10.0, 10.0, 10.0)):
    motion = SimulatedMotionController()
    motion.connect()
    motion.home()
    motion.move_to(Position(*start))
    return motion


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_jog_runner_moves_at_requested_speed_and_stops():
    motion = homed_sim()
    runner = JogRunner(lambda: motion, tick_s=0.02)
    runner.start()
    runner.set_velocity(10.0, 0.0, -2.0)
    time.sleep(0.5)
    runner.set_velocity(0.0, 0.0, 0.0)
    assert wait_until(lambda: not runner.is_busy())
    runner.stop(wait=True)
    p = motion.position
    assert 14.0 < p.x < 16.0                  # ~10 mm/s for ~0.5 s
    assert 9.0 < p.z < 9.2                    # Z moved slowly down
    assert p.y == 10.0


def test_jog_runner_stops_at_limits_and_z_floor():
    motion = homed_sim(start=(219.9, 10.0, 3.0))
    runner = JogRunner(lambda: motion, z_floor=lambda: 2.5, tick_s=0.02)
    runner.start()
    runner.set_velocity(100.0, 0.0, -10.0)
    time.sleep(0.5)
    runner.stop(wait=True)
    assert motion.position.x == 220.0
    assert motion.position.z == pytest.approx(2.5)


def test_jog_runner_runs_tasks_serially_and_reports_errors():
    motion = homed_sim()
    errors, order = [], []
    runner = JogRunner(lambda: motion, tick_s=0.02, on_error=errors.append)
    runner.start()
    runner.submit(lambda: order.append(1))
    runner.submit(lambda: (_ for _ in ()).throw(ValueError("limit")))
    runner.submit(lambda: order.append(3))
    assert wait_until(lambda: order == [1, 3])
    runner.stop(wait=True)
    assert len(errors) == 1 and isinstance(errors[0], ValueError)


def test_jog_runner_blocked_means_no_motion():
    motion = homed_sim()
    runner = JogRunner(lambda: motion, blocked=lambda: True, tick_s=0.02)
    runner.start()
    runner.set_velocity(50.0, 0.0, 0.0)
    time.sleep(0.2)
    runner.stop(wait=True)
    assert motion.position == Position(10.0, 10.0, 10.0)


def test_marlin_jog_streams_without_blocking_then_verifies_once():
    motion, fake = make_marlin()
    motion.home()
    fake.commands.clear()
    runner = JogRunner(lambda: motion, tick_s=0.02, max_ahead_s=0.1)
    runner.start()
    runner.set_velocity(10.0, 0.0, 0.0)
    time.sleep(0.4)
    runner.set_velocity(0.0, 0.0, 0.0)
    assert wait_until(lambda: not runner.is_busy())
    runner.stop(wait=True)
    commands = fake.commands
    assert sum(c.startswith("G0") for c in commands) >= 5
    assert commands.count("M400") == 1 and commands[-1] == "M114"
    assert "M114" not in commands[:-1]            # no readback between segments
    assert motion.position.x == pytest.approx(fake.pos[0], abs=0.011)


def test_autofocus_here_moves_to_sharpest_plane(tmp_path):
    motion = homed_sim(start=(1.0, 2.0, 5.05))

    class Cam:
        is_connected = True

        def capture(self, exposure_ms):
            amplitude = max(0, 1000 - int(abs(motion.position.z - 5.0) * 15000))
            image = np.zeros((24, 24), dtype=np.uint16)
            image[:, 12:] = amplitude
            return image

    settings = AcquisitionSettings(
        repeats=1, interval_s=0, exposure_ms=0.01, output_dir=tmp_path, autofocus=True,
        save_stack=False, z_range_um=200, z_spacing_um=25, focus_measure_name="Tenengrad")
    best = AcquisitionRunner(motion, Cam()).autofocus_here(settings)
    assert best.z == pytest.approx(5.0) and motion.position.z == pytest.approx(5.0)
    assert (motion.position.x, motion.position.y) == (1.0, 2.0)
