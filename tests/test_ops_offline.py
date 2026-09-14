import pytest

import etabs_python  # noqa: F401  registers operations
from etabs_python.engine import Engine
from etabs_python.errors import EngineError
from etabs_python.sources import ExcelSource


@pytest.fixture
def eng(export_xlsx):
    return Engine(ExcelSource(export_xlsx))


# ---------------------------------------------------------------- model
def test_stories_and_story_at(eng):
    st = eng.run("stories")
    assert st["Story"].tolist() == ["FL1", "FL2"]
    assert st["Elevation"].tolist() == pytest.approx([4.0, 7.5])
    assert st.attrs["base_name"] == "Base" and st.attrs["base_elevation"] == 0
    assert eng.run("story_at", z=[0.0, 2.0, 4.0, 4.0005, 5.0, 7.5, 9.0]) == \
        ["Base", "FL1", "FL1", "FL1", "FL2", "FL2", None]
    assert eng.run("story_at", z=4.0) == "FL1"


def test_story_at_matches_point_stories(eng):
    pts = eng.run("points")
    assert eng.run("story_at", z=pts["Z"].tolist()) == pts["Story"].tolist()


def test_element_lists(eng):
    assert eng.run("frames")["UniqueName"].tolist() == ["10", "11", "12"]
    assert eng.run("beams")["UniqueName"].tolist() == ["10", "11"]
    assert eng.run("columns", story="FL1")["Label"].tolist() == ["C1"]
    assert eng.run("columns", story="FL2").empty
    assert eng.run("walls")["Label"].tolist() == ["W1"]
    assert eng.run("slabs")["Label"].tolist() == ["F1"]
    assert eng.run("points", story="FL1")["UniqueName"].tolist() == ["2", "4"]


def test_materials(eng):
    m = eng.run("materials").set_index("Material")
    assert m.loc["C30", "MatType"] == "Concrete" and m.loc["C30", "E1"] == 32000 and m.loc["C30", "Fc"] == 30


def test_frame_properties(eng):
    fp = eng.run("frame_properties").set_index("UniqueName")
    assert fp.loc["10", "Sect_I3Mod"] == 0.7 and fp.loc["10", "Obj_I3Mod"] == 0.5
    assert fp.loc["10", "I3Mod"] == pytest.approx(0.35) and fp.loc["10", "JMod"] == pytest.approx(0.1)
    assert fp.loc["11", "I3Mod"] == pytest.approx(0.7) and fp.loc["12", "I3Mod"] == 1.0
    assert fp.loc["10", "t3"] == 600 and fp.loc["12", "t2"] == 500 and fp.loc["10", "Area"] == pytest.approx(180000.0)
    assert fp.loc["10", "OffsetI"] == pytest.approx(0.25) and fp.loc["10", "Length"] == 8.0
    assert fp.loc["10", "E1"] == 32000 and fp.loc["10", "Fc"] == 30 and fp.loc["10", "MatType"] == "Concrete"
    assert fp.attrs["units"]["I33"] == "mm⁴" and fp.attrs["units"]["Length"] == "m"
    assert not [c for c in fp.columns if c.endswith(("_x", "_y"))]


def test_area_properties(eng):
    ap = eng.run("area_properties").set_index("UniqueName")
    assert ap.loc["20", "m11Mod"] == pytest.approx(0.125) and ap.loc["20", "f11Mod"] == 1.0
    assert ap.loc["21", "m11Mod"] == pytest.approx(0.7) and ap.loc["21", "Sect_m11Mod"] == 0.7
    assert ap.loc["20", "TotalThick"] == 200
    assert ap.loc["20", "E1"] == 32000
    assert not [c for c in ap.columns if c.endswith(("_x", "_y"))]


# ---------------------------------------------------------------- forces
def test_beam_forces_by_zone(eng):
    z = eng.run("beam_forces_by_zone", combos=["ULS1", "ULS2"])
    b1 = z[(z["UniqueName"] == "10") & (z["OutputCase"] == "ULS1")]
    assert b1["Zone"].tolist() == ["Start", "Middle", "End"]
    assert b1["M3_min"].tolist() == [0, 2, 6] and b1["M3_max"].tolist() == [2, 6, 8]
    assert b1["V2_max"].tolist() == [10, 8, 4]
    b2 = z[z["UniqueName"] == "11"]
    assert b2["M3_max"].tolist() == [15, 45, 60] and b2["M3_min"].tolist() == [0, 15, 45]
    assert {"Story", "Label", "Length", "AnalysisSect"} <= set(z.columns)


def test_beam_forces_by_zone_envelope_and_names(eng):
    z = eng.run("beam_forces_by_zone", names=["10"], combos=["ULS1", "ULS2"], envelope=True)
    assert len(z) == 3
    assert z["M3_min"].tolist() == [-2, -6, -8] and z["M3_max"].tolist() == [2, 6, 8]
    assert "OutputCase" not in z.columns


def test_zones_must_sum_to_one(eng):
    with pytest.raises(EngineError):
        eng.run("beam_forces_by_zone", zones=[0.3, 0.3])


def test_other_force_tables(eng):
    assert eng.run("beam_forces", names="11")["M3"].tolist() == [0, 15, 30, 45, 60]
    assert eng.run("column_forces")["P"].tolist() == [-100, -90]
    assert eng.run("pier_forces", piers="P1")["Location"].tolist() == ["Top", "Bottom"]
    assert eng.run("joint_reactions")["FZ"].sum() == 700
