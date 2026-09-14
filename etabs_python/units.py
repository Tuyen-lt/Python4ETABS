"""
SI unit handling.

Unit strings as written by ETABS ("kN-m", "N/mm²", "cm⁴", "kN/m³", "MPa") are parsed into powers of
force and length plus a factor to the N/mm base. Tables are converted to a target `Units` system:

- force: N | kN
- length (general: member length, coordinates, elevations, stations, offsets): mm | m
- section (dimensions and properties of section/property definition tables): mm | m
- stress (pressure tokens Pa/kPa/MPa and force/length² in material tables): kPa | MPa

Units not built from force and length (kg, sec, deg, 1/C...) are left unchanged.
Imperial units raise UnitError.
"""
import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import pandas as pd

from .errors import UnitError

FORCE = {"N": 1.0, "kN": 1e3, "MN": 1e6}
LENGTH = {"mm": 1.0, "cm": 10.0, "m": 1e3}
PRESSURE = {"Pa": 1e-6, "kPa": 1e-3, "MPa": 1.0}  # in N/mm²
IMPERIAL = {"kip", "kips", "kipf", "lb", "lbs", "lbf", "in", "ft", "yd", "ksi", "psi", "psf", "ksf"}

SECTION_TABLE_PREFIXES = (
    "Frame Section Property Definitions",
    "Area Section Property Definitions",
    "Slab Property Definitions",
    "Wall Property Definitions",
    "Deck Property Definitions",
    "Reinforcing Bar Sizes",
)

_TO_DIGITS = str.maketrans("¹²³⁴⁵⁶", "123456")
_TO_SUPER = {2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶"}
_TOKEN = re.compile(r"^([A-Za-z]+)(\d*)$")
_PRESSURE_TOKEN = re.compile(r"(?<![A-Za-z])[kM]?Pa(?![A-Za-z])")


@dataclass(frozen=True)
class Units:
    force: str = "kN"
    length: str = "m"
    section: str = "mm"
    stress: str = "MPa"

    def __post_init__(self):
        checks = [("force", ("N", "kN")), ("length", ("mm", "m")), ("section", ("mm", "m")),
                  ("stress", ("kPa", "MPa"))]
        for field, allowed in checks:
            if getattr(self, field) not in allowed:
                raise ValueError(f"Units.{field} must be one of {allowed}, got {getattr(self, field)!r}")

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def parse_unit(unit, table: str = "", column: str = "") -> Optional[Tuple[float, int, int]]:
    """
    Parse a unit string into (factor to N/mm base, force power, length power).
    Returns None when the unit is empty or not built only from force/length tokens.
    """
    if unit is None or not str(unit).strip():
        return None
    text = str(unit).strip().translate(_TO_DIGITS)
    numerator, _, denominator = text.partition("/")
    tokens = [(t, 1) for t in numerator.split("-") if t] + [(t, -1) for t in denominator.split("-") if t]
    names = [(_TOKEN.match(t), sign) for t, sign in tokens if t != "1"]
    if any(m is not None and m.group(1) in IMPERIAL for m, _ in names):
        raise UnitError(table, column, str(unit))

    factor, fp, lp = 1.0, 0, 0
    for m, sign in names:
        if m is None:
            return None
        name, power = m.group(1), sign * int(m.group(2) or 1)
        if name in FORCE:
            factor *= FORCE[name] ** power
            fp += power
        elif name in LENGTH:
            factor *= LENGTH[name] ** power
            lp += power
        elif name in PRESSURE:
            factor *= PRESSURE[name] ** power
            fp += power
            lp -= 2 * power
        else:
            return None
    return factor, fp, lp


def format_unit(fp: int, lp: int, force: str, length: str) -> str:
    """Format force/length powers as a unit string, e.g. (1, 1) -> 'kN-m', (0, 4) -> 'mm⁴'."""
    def part(name, power):
        return name if power == 1 else name + _TO_SUPER.get(power, str(power))

    num, den = [], []
    for name, power in ((force, fp), (length, lp)):
        if power > 0:
            num.append(part(name, power))
        elif power < 0:
            den.append(part(name, -power))
    text = "-".join(num) if num else ("1" if den else "")
    return text + ("/" + "-".join(den) if den else "")


def is_section_table(table: str) -> bool:
    return (table or "").startswith(SECTION_TABLE_PREFIXES)


def _target(unit, units: Units, table: str, column: str = "") -> Optional[Tuple[float, str]]:
    """Return (multiplier from source unit, target unit string), or None if the column is not converted."""
    parsed = parse_unit(unit, table, column)
    if parsed is None:
        return None
    factor, fp, lp = parsed
    if fp == 0 and lp == 0:
        return None
    if fp == 1 and lp == -2 and (_PRESSURE_TOKEN.search(str(unit)) or (table or "").startswith("Material Properties")):
        return factor / PRESSURE[units.stress], units.stress
    length = units.section if is_section_table(table) else units.length
    return factor / (FORCE[units.force] ** fp * LENGTH[length] ** lp), format_unit(fp, lp, units.force, length)


def convert_table(df: pd.DataFrame, units: Units) -> pd.DataFrame:
    """
    Convert unit-bearing columns of a table to `units`. Uses df.attrs["table"] and df.attrs["units"]
    (column -> unit string) and returns a new DataFrame with updated attrs["units"].
    """
    table = df.attrs.get("table", "")
    out = df.copy()
    new_units = {}
    for column, unit in dict(df.attrs.get("units", {})).items():
        if column not in out.columns:
            continue
        target = _target(unit, units, table, column)
        if target is None:
            new_units[column] = unit
            continue
        multiplier, target_unit = target
        out[column] = pd.to_numeric(out[column], errors="coerce") * multiplier
        new_units[column] = target_unit
    out.attrs = dict(df.attrs)
    out.attrs["units"] = new_units
    return out


def convert_value(value: float, unit: str, units: Units, section: bool = False, table: str = "") -> float:
    """Convert a single value. section=True uses the section length unit."""
    table = table or (SECTION_TABLE_PREFIXES[0] if section else "")
    target = _target(unit, units, table)
    return value * target[0] if target else value
