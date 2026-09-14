import math

import pandas as pd
import pytest

from etabs_python.errors import UnitError
from etabs_python.units import Units, convert_table, convert_value, format_unit, parse_unit


@pytest.mark.parametrize("u,expected", [
    ("kN", (1e3, 1, 0)), ("N-mm", (1.0, 1, 1)), ("kN-m", (1e6, 1, 1)), ("mm", (1.0, 0, 1)),
    ("m", (1e3, 0, 1)), ("cm²", (100.0, 0, 2)), ("cm⁴", (1e4, 0, 4)), ("mm4", (1.0, 0, 4)),
    ("kN/m", (1.0, 1, -1)), ("kN/m³", (1e-6, 1, -3)), ("MPa", (1.0, 1, -2)), ("kPa", (1e-3, 1, -2)),
    ("N/mm2", (1.0, 1, -2)), ("kN-m/m", (1e3, 1, 0)), ("1/m", (1e-3, 0, -1)), ("mm/mm", (1.0, 0, 0)),
])
def test_parse_unit(u, expected):
    f, fp, lp = parse_unit(u)
    assert (fp, lp) == expected[1:] and math.isclose(f, expected[0], rel_tol=1e-12)


@pytest.mark.parametrize("u", ["kg", "ton-m²", "1/C", "cyc/sec", "deg", "kN-m/rad", "mm/sec²"])
def test_non_force_length_units_return_none(u):
    assert parse_unit(u) is None


@pytest.mark.parametrize("u", ["kip", "lb-ft", "in", "ksi", "psi", "kip-ft"])
def test_imperial_raises(u):
    with pytest.raises(UnitError):
        parse_unit(u, table="T", column="C")


def test_format_unit():
    assert format_unit(1, 1, "kN", "m") == "kN-m"
    assert format_unit(1, -3, "kN", "m") == "kN/m³"
    assert format_unit(0, 4, "kN", "mm") == "mm⁴"
    assert format_unit(0, -1, "kN", "m") == "1/m"


def test_convert_section_table():
    df = pd.DataFrame({"Name": ["B"], "Area": [1800.0], "t3": [600.0]})
    df.attrs = {"table": "Frame Section Property Definitions - Summary", "units": {"Area": "cm²", "t3": "mm"}}
    out = convert_table(df, Units())
    assert out["Area"][0] == pytest.approx(180000.0) and out.attrs["units"]["Area"] == "mm²"
    assert out["t3"][0] == 600.0 and out.attrs["units"]["t3"] == "mm"
    assert df["Area"][0] == 1800.0  # input untouched


def test_convert_general_table_keeps_area_load_as_force_per_area():
    g = pd.DataFrame({"Station": [8000.0], "M3": [5e6], "Load": [2.0], "Name": ["x"]})
    g.attrs = {"table": "Element Forces - Beams", "units": {"Station": "mm", "M3": "N-mm", "Load": "kN/m²"}}
    out = convert_table(g, Units())
    assert out["Station"][0] == pytest.approx(8.0) and out["M3"][0] == pytest.approx(5.0)
    assert out["Load"][0] == pytest.approx(2.0) and out.attrs["units"]["Load"] == "kN/m²"


def test_convert_material_table_uses_stress_unit():
    m = pd.DataFrame({"E1": [30000.0], "UnitWeight": [2.5e-5]})
    m.attrs = {"table": "Material Properties - Basic Mechanical Properties",
               "units": {"E1": "N/mm²", "UnitWeight": "N/mm³"}}
    out = convert_table(m, Units())
    assert out["E1"][0] == pytest.approx(30000.0) and out.attrs["units"]["E1"] == "MPa"
    assert out["UnitWeight"][0] == pytest.approx(25.0) and out.attrs["units"]["UnitWeight"] == "kN/m³"


def test_unconvertible_units_are_kept():
    df = pd.DataFrame({"Mass": [5.0], "Angle": [30.0]})
    df.attrs = {"table": "X", "units": {"Mass": "kg", "Angle": "deg"}}
    out = convert_table(df, Units())
    assert out["Mass"][0] == 5.0 and out.attrs["units"] == {"Mass": "kg", "Angle": "deg"}


def test_units_validation_and_convert_value():
    with pytest.raises(ValueError):
        Units(force="kip")
    assert convert_value(7000.0, "mm", Units()) == pytest.approx(7.0)
    assert convert_value(7000.0, "mm", Units(length="mm")) == pytest.approx(7000.0)
    assert convert_value(600.0, "mm", Units(), section=True) == pytest.approx(600.0)
