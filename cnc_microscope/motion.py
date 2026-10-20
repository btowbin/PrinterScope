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


class PositionMismatchError(RuntimeError):
    """The printer reports a position different from the one that was commanded."""


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

    def sync_position(self) -> Position:
        """Re-read the position from the machine (no-op for simulated hardware)."""
        return self.position

    def jog_segment(self, target: Position, feed_mm_min: float) -> None:
        """Start a short move without waiting for it to finish (used for joystick jogging)."""
        self.move_to(target, feed_mm_min)

    def finish_jog(self) -> None:
        """Wait for queued jog segments to finish and confirm the final position."""

    def move_by(self, dx: float = 0, dy: float = 0, dz: float = 0,
                feed_mm_min: float | None = None) -> None:
        p = self.position
        target = Position(p.x + dx, p.y + dy, p.z + dz)
        self.move_to(target, feed_mm_min=feed_mm_min or (300.0 if dz else 1200.0))

    def reposition(self, position: Position, xy_feed_mm_min: float = 6000.0,
                   z_feed_mm_min: float = 1200.0) -> None:
        """Fast repositioning: Z first when going up, XY first when going down.

        Z+ retracts the objective, so the objective is lifted before it travels
        sideways and lowered only after it has arrived above the target.
        """
        self.limits.validate(position)
        start = self.position
        if position.z > start.z:
            steps = [(Position(start.x, start.y, position.z), z_feed_mm_min),
                     (position, xy_feed_mm_min)]
        else:
            steps = [(Position(position.x, position.y, start.z), xy_feed_mm_min),
                     (position, z_feed_mm_min)]
        for target, feed in steps:
            if target != self.position:
                self.move_to(target, feed)

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

    def jog_segment(self, target: Position, feed_mm_min: float) -> None:
        self._require_connection()
        if not self.is_homed:
            raise RuntimeError("Home the microscope before moving")
        self.limits.validate(target)
        self._position = target

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

    POSITION_TOLERANCE_MM = 0.02  # M114 prints two decimals

    def __init__(self, port: str, baudrate: int = 115200, limits: MotionLimits | None = None,
                 position_tolerance_mm: float | None = None):
        super().__init__(limits)
        self.position_tolerance_mm = (self.POSITION_TOLERANCE_MM if position_tolerance_mm is None
                                      else position_tolerance_mm)
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
        # Read the real post-homing coordinates instead of assuming 0, 0, 0, so a
        # firmware home offset (M206) is respected.
        self.query_position()
        self.is_homed = True

    def move_to(self, position: Position, feed_mm_min: float = 1200.0) -> None:
        self._require_connection()
        if not self.is_homed:
            raise RuntimeError("Home the microscope before moving")
        self.limits.validate(position)
        line = f"G0 X{position.x:.4f} Y{position.y:.4f} Z{position.z:.4f} F{feed_mm_min:.1f}"
        try:
            self._command(line, timeout=10)
            self._command("M400", timeout=120)  # Wait until physical movement is complete.
        except Exception:
            self._position_lost()
            raise
        self._verify_reached(position)

    def _verify_reached(self, position: Position) -> None:
        """Read the position back from Marlin and compare it with the commanded one."""
        try:
            reported = self.query_position()    # read back instead of trusting the command
        except Exception:
            self._position_lost()
            raise
        tolerance = self.position_tolerance_mm
        if (abs(reported.x - position.x) > tolerance or abs(reported.y - position.y) > tolerance
                or abs(reported.z - position.z) > tolerance):
            # query_position already stored the printer's value as the current position.
            raise PositionMismatchError(
                f"Printer reports X {reported.x:.2f}, Y {reported.y:.2f}, Z {reported.z:.2f} mm "
                f"but X {position.x:.3f}, Y {position.y:.3f}, Z {position.z:.3f} mm was commanded. "
                "The displayed position was resynchronised from the printer; "
                "check for a stalled axis or a firmware offset."
            )
        # Keep the exact commanded value: M114 only has 0.01 mm resolution.
        self._position = position

    def jog_segment(self, target: Position, feed_mm_min: float) -> None:
        """Queue one short move in the planner without waiting for it or reading back."""
        self._require_connection()
        if not self.is_homed:
            raise RuntimeError("Home the microscope before moving")
        self.limits.validate(target)
        line = f"G0 X{target.x:.4f} Y{target.y:.4f} Z{target.z:.4f} F{feed_mm_min:.1f}"
        try:
            self._command(line, timeout=10)
        except Exception:
            self._position_lost()
            raise
        self._position = target

    def finish_jog(self) -> None:
        target = self._position
        try:
            self._command("M400", timeout=120)
        except Exception:
            self._position_lost()
            raise
        self._verify_reached(target)

    def sync_position(self) -> Position:
        """Re-read XYZ from Marlin, e.g. after the stage was moved from the printer's LCD."""
        self._require_connection()
        return self.query_position()

    def _position_lost(self) -> None:
        """After a failed command the true position is unknown: resync or require homing."""
        try:
            self.query_position()
        except Exception:
            self.is_homed = False

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
        # Marlin halts and must be reset: the position is no longer trustworthy.
        self.is_homed = False

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
    return [info.device for info in serial_port_infos()]


# ---- finding the printer ------------------------------------------------------
@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None


# The Ender 3 V2 mainboards use a CH340 USB-serial chip (1A86:7523); other boards and clones
# use these chips too. A match only makes a port a *candidate*: Marlin must still answer.
_CH340_IDS = {(0x1A86, 0x7523), (0x1A86, 0x7522), (0x1A86, 0x55D4)}
_PRINTER_VENDOR_IDS = {0x1A86, 0x10C4, 0x0403, 0x0483, 0x2341, 0x1D50}
_PRINTER_KEYWORDS = ("ch340", "ch341", "usb-serial", "usb serial", "cp210", "ftdi", "stm32",
                     "marlin", "ender", "creality")


def printer_port_score(info: PortInfo) -> int:
    """How likely this serial port is the printer (0 = no reason to think so)."""
    text = info.description.lower()
    if "bluetooth" in text:
        return -1
    score = 0
    if (info.vid, info.pid) in _CH340_IDS:
        score += 100
    elif info.vid in _PRINTER_VENDOR_IDS:
        score += 60
    if any(word in text for word in _PRINTER_KEYWORDS):
        score += 30
    return score


def rank_printer_ports(infos: list[PortInfo]) -> list[PortInfo]:
    """Likely printer ports, best first. Falls back to every non-Bluetooth port when nothing
    looks like a known printer USB chip, so unusual adapters can still be probed."""
    scored = [(printer_port_score(info), info) for info in infos]
    likely = sorted((item for item in scored if item[0] > 0), key=lambda item: -item[0])
    if likely:
        return [info for _score, info in likely]
    return [info for score, info in scored if score == 0]


def serial_port_infos() -> list[PortInfo]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [PortInfo(port.device, port.description or "", port.vid, port.pid)
            for port in list_ports.comports()]


def probe_marlin(port: str, baud: int, *, open_serial=None, reset_wait_s: float = 2.0,
                 reply_timeout_s: float = 3.0) -> bool:
    """True if a Marlin printer answers M115 on this port at this baud rate."""
    if open_serial is None:
        def open_serial(device, rate):
            import serial
            return serial.Serial(device, rate, timeout=0.25, write_timeout=2)
    try:
        connection = open_serial(port, baud)
    except Exception:
        return False  # busy, no permission, unplugged...
    try:
        time.sleep(reset_wait_s)  # Ender boards reset when the port opens
        connection.reset_input_buffer()
        connection.write(b"M115\n")
        connection.flush()
        deadline = time.monotonic() + reply_timeout_s
        while time.monotonic() < deadline:
            line = connection.readline().decode("ascii", errors="ignore")
            if "FIRMWARE_NAME" in line:
                return True
        return False
    except Exception:
        return False
    finally:
        try:
            connection.close()
        except Exception:
            pass


def detect_printer(infos: list[PortInfo], bauds: tuple[int, ...] = (115200, 250000),
                   probe=probe_marlin, progress=None) -> tuple[str, int] | None:
    """Probe the likely ports (best first) and return (port, baud) of the first Marlin
    printer that answers, or None."""
    for info in rank_printer_ports(infos):
        for baud in bauds:
            if progress:
                progress(f"Trying {info.device} at {baud} baud…")
            if probe(info.device, baud):
                return info.device, baud
    return None
