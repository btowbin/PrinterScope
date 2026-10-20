"""Gamepad (XInput) input: decoding, joystick response curves and button mapping.

The hardware read is isolated in ``XInputBackend`` (Windows only, ctypes, no
extra packages); everything else is pure logic that can be tested without a pad.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import math

BUTTON_BITS = {
    "DPAD_UP": 0x0001, "DPAD_DOWN": 0x0002, "DPAD_LEFT": 0x0004, "DPAD_RIGHT": 0x0008,
    "START": 0x0010, "BACK": 0x0020, "LTHUMB": 0x0040, "RTHUMB": 0x0080,
    "LB": 0x0100, "RB": 0x0200, "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
}

# action -> button. L1/R1 are the shoulder bumpers (LB/RB on an Xbox-style pad).
DEFAULT_MAPPING = {
    "xy_left": "DPAD_LEFT", "xy_right": "DPAD_RIGHT", "xy_up": "DPAD_UP", "xy_down": "DPAD_DOWN",
    "z_down": "LB", "z_up": "RB",
    "add_point": "A", "autofocus": "B", "capture": "X", "speed": "Y",
}
REPEATABLE_ACTIONS = ("xy_left", "xy_right", "xy_up", "xy_down", "z_down", "z_up")
SPEED_LEVELS = ("Coarse", "Fine", "Super fine")


@dataclass(frozen=True)
class PadState:
    buttons: frozenset
    lx: float
    ly: float
    rx: float
    ry: float


def _norm(value: int) -> float:
    return max(-1.0, min(1.0, value / 32767.0))


def decode_state(button_mask: int, lx: int, ly: int, rx: int, ry: int) -> PadState:
    names = frozenset(name for name, bit in BUTTON_BITS.items() if button_mask & bit)
    return PadState(names, _norm(lx), _norm(ly), _norm(rx), _norm(ry))


@dataclass
class GamepadConfig:
    deadzone: float = 0.15
    exponent: float = 2.0        # 1 = linear, 2 = gentle near the centre (dosage)
    z_speed_scale: float = 0.25  # Z stick speed as a fraction of the preset's Z speed
    invert_x: bool = False
    invert_y: bool = False
    invert_z: bool = False
    z_floor_mm: float = 0.0      # lowest Z the gamepad may reach (objective safety)
    repeat_delay_s: float = 0.5
    repeat_interval_s: float = 0.15
    mapping: dict = field(default_factory=lambda: dict(DEFAULT_MAPPING))

    def validate(self) -> None:
        if not 0.0 <= self.deadzone < 0.9:
            raise ValueError("Deadzone must be between 0 and 0.9")
        if not 0.5 <= self.exponent <= 4.0:
            raise ValueError("Response exponent must be between 0.5 and 4")
        if not 0.0 < self.z_speed_scale <= 1.0:
            raise ValueError("Z speed scale must be greater than 0 and at most 1")
        if self.repeat_delay_s < 0 or self.repeat_interval_s <= 0:
            raise ValueError("Repeat timings must be positive")
        unknown = [b for b in self.mapping.values() if b not in BUTTON_BITS]
        if unknown:
            raise ValueError(f"Unknown button(s) in mapping: {unknown}")


def radial_response(x: float, y: float, deadzone: float, exponent: float) -> tuple[float, float]:
    """Dosage curve for a two-axis stick: radial deadzone, then magnitude**exponent."""
    raw = math.hypot(x, y)  # can exceed 1 at the corners of a square-gated stick
    magnitude = min(raw, 1.0)
    if magnitude <= deadzone:
        return 0.0, 0.0
    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    gain = (scaled ** exponent) / raw
    return x * gain, y * gain


def axis_response(value: float, deadzone: float, exponent: float) -> float:
    return radial_response(value, 0.0, deadzone, exponent)[0]


def velocity_from_state(state: PadState, config: GamepadConfig, xy_speed: float,
                        z_speed: float) -> tuple[float, float, float]:
    """Stick positions to axis velocities in mm/s (left stick XY, right stick Z)."""
    rx, ry = radial_response(state.lx, state.ly, config.deadzone, config.exponent)
    vz = axis_response(state.ry, config.deadzone, config.exponent)
    vx = rx * xy_speed * (-1.0 if config.invert_x else 1.0)
    vy = ry * xy_speed * (-1.0 if config.invert_y else 1.0)
    vz = vz * z_speed * config.z_speed_scale * (-1.0 if config.invert_z else 1.0)
    return vx, vy, vz


def next_speed_level(current: str) -> str:
    if current not in SPEED_LEVELS:
        return SPEED_LEVELS[0]
    return SPEED_LEVELS[(SPEED_LEVELS.index(current) + 1) % len(SPEED_LEVELS)]


class ButtonTracker:
    """Turns held-button snapshots into press events, with hold-to-repeat for steps."""

    def __init__(self, config: GamepadConfig):
        self.config = config
        self._held: dict[str, list[float]] = {}  # button -> [pressed_at, last_fired_at]

    def reset(self) -> None:
        self._held.clear()

    def update(self, buttons, now: float) -> list[str]:
        mapping = self.config.mapping
        repeatable = {mapping[a] for a in REPEATABLE_ACTIONS if a in mapping}
        events: list[str] = []
        for name in list(self._held):
            if name not in buttons:
                del self._held[name]
        for name in buttons:
            if name not in self._held:
                self._held[name] = [now, now]
                events.append(name)
            elif name in repeatable:
                pressed_at, last = self._held[name]
                if (now - pressed_at >= self.config.repeat_delay_s
                        and now - last >= self.config.repeat_interval_s):
                    self._held[name][1] = now
                    events.append(name)
        return events


def actions_for(button_events, mapping: dict) -> list[str]:
    reverse = {button: action for action, button in mapping.items()}
    return [reverse[b] for b in button_events if b in reverse]


class _XGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _XState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _XGamepad)]


class XInputBackend:
    """Reads the first connected XInput pad (Windows). ``available`` is False elsewhere."""

    def __init__(self):
        self._get_state = None
        self._index = 0
        if not hasattr(ctypes, "WinDLL"):
            return
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                library = ctypes.WinDLL(name)
            except OSError:
                continue
            function = library.XInputGetState
            function.argtypes = [ctypes.c_ulong, ctypes.POINTER(_XState)]
            function.restype = ctypes.c_ulong
            self._get_state = function
            return

    @property
    def available(self) -> bool:
        return self._get_state is not None

    def read(self) -> PadState | None:
        if self._get_state is None:
            return None
        state = _XState()
        for offset in range(4):
            index = (self._index + offset) % 4
            if self._get_state(index, ctypes.byref(state)) == 0:  # ERROR_SUCCESS
                self._index = index
                pad = state.Gamepad
                return decode_state(pad.wButtons, pad.sThumbLX, pad.sThumbLY,
                                    pad.sThumbRX, pad.sThumbRY)
        return None
