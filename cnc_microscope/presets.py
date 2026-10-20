"""Editable movement presets (step sizes in mm, speeds in mm/s)."""
from __future__ import annotations

from dataclasses import dataclass, replace

PRESET_NAMES = ("Coarse", "Fine", "Super fine", "Custom")


@dataclass(frozen=True)
class StepPreset:
    x_step: float
    y_step: float
    z_step: float
    xy_speed: float  # mm/s
    z_speed: float   # mm/s

    def validate(self) -> None:
        for label, value in (("X step", self.x_step), ("Y step", self.y_step),
                             ("Z step", self.z_step), ("XY speed", self.xy_speed),
                             ("Z speed", self.z_speed)):
            if not value > 0:
                raise ValueError(f"{label} must be greater than zero")


def default_presets() -> dict[str, StepPreset]:
    return {
        "Coarse": StepPreset(5.0, 5.0, 0.5, 50.0, 10.0),
        "Fine": StepPreset(0.1, 0.1, 0.01, 20.0, 5.0),
        # Standard Ender 3 v2: 80 steps/mm XY and 400 steps/mm Z.
        "Super fine": StepPreset(0.0125, 0.0125, 0.0025, 5.0, 1.0),
        "Custom": StepPreset(0.1, 0.1, 0.01, 20.0, 5.0),
    }


PRESET_FIELDS = ("x_step", "y_step", "z_step", "xy_speed", "z_speed")


def with_field(preset: StepPreset, field: str, value: float) -> StepPreset:
    if field not in PRESET_FIELDS:
        raise ValueError(f"Unknown preset field: {field}")
    updated = replace(preset, **{field: float(value)})
    updated.validate()
    return updated
