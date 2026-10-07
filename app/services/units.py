from __future__ import annotations

from typing import Literal

AreaUnit = Literal["m2", "km2", "ha", "acre", "ft2", "mi2"]
LengthUnit = Literal["m", "km", "ft", "mi"]

_AREA = {
    "m2": 1.0,
    "km2": 1e-6,
    "ha": 1e-4,
    "acre": 1 / 4046.8564224,
    "ft2": 1 / 0.09290304,
    "mi2": 1 / 2_589_988.110336,
}
_LENGTH = {"m": 1.0, "km": 1e-3, "ft": 1 / 0.3048, "mi": 1 / 1609.344}


def area(value_m2: float | None, unit: AreaUnit) -> float | None:
    return None if value_m2 is None else round(value_m2 * _AREA[unit], 6)


def length(value_m: float | None, unit: LengthUnit) -> float | None:
    return None if value_m is None else round(value_m * _LENGTH[unit], 6)
