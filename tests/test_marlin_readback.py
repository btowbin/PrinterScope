import re

import pytest

from cnc_microscope.models import Position
from cnc_microscope.motion import MarlinMotionController, PositionMismatchError


class FakeMarlin:
    """Minimal Marlin stand-in: tracks a position and answers M114."""

    def __init__(self, home=(0.0, 0.0, 0.0)):
        self.is_open = True
        self.home = home
        self.pos = [0.0, 0.0, 0.0]
        self.stall = [0.0, 0.0, 0.0]   # silently lost distance per axis on the next G0
        self.fail_m114 = False
        self.out = []
        self.commands = []

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def write(self, data):
        for line in data.decode().strip().splitlines():
            self.commands.append(line)
            if line.startswith("G28"):
                self.pos = list(self.home)
            elif line.startswith("G0"):
                target = [float(v) for v in re.findall(r"[XYZ]([-\d.]+)", line)]
                self.pos = [t - s for t, s in zip(target, self.stall)]
            elif line == "M114":
                if self.fail_m114:
                    self.out.append("garbage")
                else:
                    x, y, z = self.pos
                    self.out.append(f"X:{x:.2f} Y:{y:.2f} Z:{z:.2f} E:0.00 Count X:0 Y:0 Z:0")
            self.out.append("ok")

    def readline(self):
        return (self.out.pop(0) + "\n").encode() if self.out else b""


def make(home=(0.0, 0.0, 0.0)):
    fake = FakeMarlin(home)
    motion = MarlinMotionController("fake")
    motion._serial = fake
    motion.is_connected = True
    return motion, fake


def test_home_reads_firmware_position_including_offset():
    motion, _ = make(home=(5.0, 2.5, 1.0))
    motion.home()
    assert motion.position == Position(5.0, 2.5, 1.0)
    assert motion.is_homed


def test_move_verified_and_keeps_exact_commanded_value():
    motion, fake = make()
    motion.home()
    target = Position(10.0, 20.0, 5.0025)
    motion.move_to(target)
    assert motion.position == target
    assert "M114" in fake.commands


def test_lost_steps_raise_and_resync_from_printer():
    motion, fake = make()
    motion.home()
    fake.stall = [0.0, 0.5, 0.0]
    with pytest.raises(PositionMismatchError, match="resynchronised"):
        motion.move_to(Position(10.0, 20.0, 5.0))
    assert motion.position == Position(10.0, 19.5, 5.0)  # the printer's value, not the command
    assert motion.is_homed


def test_small_deviation_within_tolerance_is_accepted():
    motion, fake = make()
    motion.home()
    fake.stall = [0.004, 0.0, 0.0]
    motion.move_to(Position(10.0, 20.0, 5.0))


def test_sync_position_picks_up_external_movement():
    motion, fake = make()
    motion.home()
    fake.pos = [33.0, 44.0, 6.0]  # moved from the printer's LCD
    assert motion.sync_position() == Position(33.0, 44.0, 6.0)
    assert motion.position == Position(33.0, 44.0, 6.0)


def test_emergency_stop_requires_rehoming():
    motion, fake = make()
    motion.home()
    motion.emergency_stop()
    assert not motion.is_homed
    assert fake.commands[-1] == "M112"
    with pytest.raises(RuntimeError, match="Home"):
        motion.move_to(Position(1, 1, 1))


def test_unreadable_position_after_move_unhomes():
    motion, fake = make()
    motion.home()
    fake.fail_m114 = True
    with pytest.raises(RuntimeError, match="Could not read position"):
        motion.move_to(Position(1.0, 1.0, 1.0))
    assert not motion.is_homed
