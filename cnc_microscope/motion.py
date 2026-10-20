from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
import threading
import time

from .models import Position


@dataclass(frozen=True)
class MotionLimits:
    x_min: float = 0.0
    x_max: float = 220.0
    y_min: float = 0.0
    y_max: float = 220.0
    z_min: float = 0.0
    z_max: float = 250.0

    def validate(self, p: Position) -> None:
        if not (self.x_min <= p.x <= self.x_max):
            raise ValueError(f"X {p.x:.3f} mm is outside {self.x_min}–{self.x_max} mm")
        if not (self.y_min <= p.y <= self.y_max):
            raise ValueError(f"Y {p.y:.3f} mm is outside {self.y_min}–{self.y_max} mm")
        if not (self.z_min <= p.z <= self.z_max):
            raise ValueError(f"Z {p.z:.3f} mm is outside {self.z_min}–{self.z_max} mm")


class MotionController(ABC):
    def __init__(self, limits: MotionLimits | None = None) -> None:
        self.limits = limits or MotionLimits()
        self._position = Position(0.0, 0.0, 0.0)
        self.is_connected = False
        self.is_homed = False

    @property
    def position(self) -> Position:
        return self._position

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def home(self) -> None: ...

    @abstractmethod
    def move_to(self, position: Position, feed_mm_min: float = 1200.0) -> None: ...

    def move_by(self, dx: float = 0, dy: float = 0, dz: float = 0,
                feed_mm_min: float | None = None) -> None:
        p = self.position
        target = Position(p.x + dx, p.y + dy, p.z + dz)
        self.move_to(target, feed_mm_min=feed_mm_min or (300.0 if dz else 1200.0))

    def approach_to(self, position: Position, xy_preload_mm: float = 0.25,
                    z_preload_mm: float = 0.05, feed_mm_min: float = 1200.0) -> None:
        """Approach a target with final X+, Y+, and Z- moves to take up backlash."""
        self.limits.validate(position)
        if xy_preload_mm < 0 or z_preload_mm < 0:
            raise ValueError("Backlash preload distances cannot be negative")
        preload = Position(position.x - xy_preload_mm, position.y - xy_preload_mm,
                           position.z + z_preload_mm)
        try:
            self.limits.validate(preload)
        except ValueError as exc:
            raise ValueError(
                "Position is too close to a travel limit for the configured backlash preload"
            ) from exc

        # Retract first, preload XY below the target, finish XY in the positive
        # direction, then approach the sample in the negative Z direction.
        safe_z = max(self.position.z, preload.z)
        self.move_to(Position(self.position.x, self.position.y, safe_z), feed_mm_min)
        self.move_to(Position(preload.x, preload.y, safe_z), feed_mm_min)
        self.move_to(Position(position.x, position.y, safe_z), feed_mm_min)
        self.move_to(position, min(feed_mm_min, 300.0))

    def approach_z(self, position: Position, z_preload_mm: float = 0.05,
                   feed_mm_min: float = 300.0) -> None:
        """Approach a Z target in the negative direction after a positive preload."""
        self.limits.validate(position)
        if z_preload_mm < 0:
            raise ValueError("Z backlash preload cannot be negative")
        preload = Position(position.x, position.y, position.z + z_preload_mm)
        try:
            self.limits.validate(preload)
        except ValueError as exc:
            raise ValueError(
                "Z position is too close to the upper travel limit for backlash correction"
            ) from exc
        self.move_to(preload, feed_mm_min)
        self.move_to(position, feed_mm_min)


class SimulatedMotionController(MotionController):
    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False
        self.is_homed = False

    def home(self) -> None:
        self._require_connection()
        time.sleep(0.35)
        self._position = Position(0.0, 0.0, 0.0)
        self.is_homed = True

    def move_to(self, position: Position, feed_mm_min: float = 1200.0) -> None:
        self._require_connection()
        if not self.is_homed:
            raise RuntimeError("Home the microscope before moving")
        self.limits.validate(position)
        time.sleep(0.04)
        self._position = position

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Motion controller is not connected")


class MarlinMotionController(MotionController):
    """Small, synchronous Marlin serial driver for an Ender 3 class machine."""

    def __init__(self, port: str, baudrate: int = 115200, limits: MotionLimits | None = None):
        super().__init__(limits)
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is not installed") from exc
        self._serial = serial.Serial(self.port, self.baudrate, timeout=0.25, write_timeout=2)
        time.sleep(2.0)  # Ender boards often reset when the serial port opens.
        self._serial.reset_input_buffer()
        self._command("M115", timeout=5)
        self._command("G90")
        self.is_connected = True

    def disconnect(self) -> None:
        if self._serial is not None:
            self._serial.close()
        self._serial = None
        self.is_connected = False
        self.is_homed = False

    def home(self) -> None:
        self._require_connection()
        self._command("G28", timeout=120)
        self._command("G90")
        self._position = Position(0.0, 0.0, 0.0)
        self.is_homed = True

    def move_to(self, position: Position, feed_mm_min: float = 1200.0) -> None:
        self._require_connection()
        if not self.is_homed:
            raise RuntimeError("Home the microscope before moving")
        self.limits.validate(position)
        line = f"G0 X{position.x:.4f} Y{position.y:.4f} Z{position.z:.4f} F{feed_mm_min:.1f}"
        self._command(line, timeout=10)
        self._command("M400", timeout=120)  # Wait until physical movement is complete.
        self._position = position

    def query_position(self) -> Position:
        response = self._command("M114", timeout=5, return_lines=True)
        match = re.search(r"X:([-+\d.]+)\s+Y:([-+\d.]+)\s+Z:([-+\d.]+)", " ".join(response))
        if not match:
            raise RuntimeError("Could not read position from Marlin")
        self._position = Position(*(float(value) for value in match.groups()))
        return self._position

    def emergency_stop(self) -> None:
        if self._serial is not None:
            self._serial.write(b"M112\n")

    def _require_connection(self) -> None:
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("Motion controller is not connected")

    def _command(self, command: str, timeout: float = 10, return_lines: bool = False):
        self._require_serial()
        with self._lock:
            self._serial.write((command.strip() + "\n").encode("ascii"))
            self._serial.flush()
            deadline = time.monotonic() + timeout
            lines: list[str] = []
            while time.monotonic() < deadline:
                raw = self._serial.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                lines.append(line)
                lowered = line.lower()
                if lowered.startswith("error") or "halted" in lowered:
                    raise RuntimeError(f"Marlin rejected {command!r}: {line}")
                if lowered.startswith("ok"):
                    return lines if return_lines else None
            raise TimeoutError(f"Timed out waiting for Marlin after {command!r}")

    def _require_serial(self) -> None:
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("Serial port is not open")


def serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [port.device for port in list_ports.comports()]

