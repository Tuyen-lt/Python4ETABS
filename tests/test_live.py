"""
Read-only checks against a running ETABS (skipped when ETABS is not available).
Object selection is changed during the test and restored afterwards; output/display case selection may change.
Set ETABS_EXPORT_XLSX to an Excel export of the open model to also compare ExcelSource with LiveSource.
"""
import os

import pandas as pd
import pytest

from etabs_python import Engine, ExcelSource
from etabs_python.ops_model import AREA_MODS, FRAME_MODS
from etabs_python.sources import N_MM

FINISHED = 4  # Analyze.GetCaseStatus


@pytest.fixture(scope="module")
def eng(etabs_live):
    return Engine(etabs_live)


@pytest.fixture(scope="module")
def sap(etabs_live):
    return etabs_live.sap_model


@pytest.fixture(scope="module")
def finished_case(sap):
    _, names, status, _ = sap.Analyze.GetCaseStatus()
    done = [n for n, s in zip(names, status) if s == FINISHED]
    if not done:
        pytest.skip("No finished analysis case in the open model")
    return done[0]


def _select_case(sap, case):
    sap.Results.Setup.DeselectAllCasesAndCombosForOutput()
    assert sap.Results.Setup.SetCaseSelectedForOutput(case) == 0


def test_stories_match_api(eng, etabs_live, sap):
    st = eng.run("stories")
    with etabs_live.present_units(N_MM):
        ret = sap.Story.GetStories_2()
    api = sorted(zip(ret[3], ret[2]))
    assert st["Story"].tolist() == [n for _, n in api]
    assert (st["Elevation"] * 1000).tolist() == pytest.approx([e for e, _ in api], abs=1.0)
    assert st.attrs["base_elevation"] * 1000 == pytest.approx(ret[0], abs=1.0)


def test_story_at_matches_point_stories(eng):
    pts = eng.run("points")
    found = eng.run("story_at", z=pts["Z"].tolist())
    mismatch = [(n, z, s, f) for n, z, s, f in zip(pts["UniqueName"], pts["Z"], pts["Story"], found) if s != f]
    assert not mismatch, mismatch[:5]


def _check_modifiers(df, mods, get_obj, get_sect, key):
    changed = df[(df[[f"Sect_{m}" for m in mods]] != 1).any(axis=1) | (df[[f"Obj_{m}" for m in mods]] != 1).any(axis=1)]
    for _, r in changed.head(50).iterrows():
        obj, sect = get_obj(r["UniqueName"])[0], get_sect(r[key])[0]
        for i, m in enumerate(mods):
            assert r[f"Obj_{m}"] == pytest.approx(obj[i]), (r["UniqueName"], m)
            assert r[f"Sect_{m}"] == pytest.approx(sect[i]), (r[key], m)
            assert r[m] == pytest.approx(obj[i] * sect[i]), (r["UniqueName"], m)
    return len(changed)


def test_frame_and_area_modifiers_match_api(eng, sap):
    fp = eng.run("frame_properties")
    assert fp["UniqueName"].is_unique and len(fp) == len(eng.run("frames"))
    _check_modifiers(fp, FRAME_MODS, sap.FrameObj.GetModifiers, sap.PropFrame.GetModifiers, "AnalysisSect")
    ap = eng.run("area_properties")
    assert ap["UniqueName"].is_unique and len(ap) == len(eng.run("shells"))
    _check_modifiers(ap, AREA_MODS, sap.AreaObj.GetModifiers, sap.PropArea.GetModifiers, "SectProp")


def test_beam_forces_table_matches_results_api(eng, etabs_live, sap, finished_case):
    beam = eng.run("beams").iloc[0]["UniqueName"]
    table = eng.run("beam_forces", names=[beam], cases=[finished_case]).sort_values(["Station", "M3"])
    with etabs_live.present_units(N_MM):
        _select_case(sap, finished_case)
        r = sap.Results.FrameForce(beam, 0)
    api = pd.DataFrame({"Station": list(r[2])[:r[0]], "M3": list(r[13])[:r[0]]}).sort_values(["Station", "M3"])
    assert len(table) == len(api)
    assert table["Station"].tolist() == pytest.approx((api["Station"] / 1000).tolist(), abs=1e-4)
    assert table["M3"].tolist() == pytest.approx((api["M3"] / 1e6).tolist(), rel=1e-3, abs=1e-3)


def test_joint_reactions_sum_matches_base_reaction(eng, etabs_live, sap, finished_case):
    jr = eng.run("joint_reactions", cases=[finished_case])
    with etabs_live.present_units(N_MM):
        _select_case(sap, finished_case)
        fz = sap.Results.BaseReact()[6][0]
    assert jr["FZ"].sum() * 1000 == pytest.approx(fz, rel=1e-3, abs=1.0)


def test_beam_forces_by_zone_matches_manual(eng, finished_case):
    b = eng.run("beams").iloc[0]
    z = eng.run("beam_forces_by_zone", names=[b["UniqueName"]], cases=[finished_case])
    raw = eng.run("beam_forces", names=[b["UniqueName"]], cases=[finished_case])
    length = b["Length"]
    for zone, start, end in (("Start", 0, 0.25), ("Middle", 0.25, 0.75), ("End", 0.75, 1.0)):
        seg = raw[(raw["Station"] >= start * length - 1e-6) & (raw["Station"] <= end * length + 1e-6)]
        row = z[z["Zone"] == zone]
        if len(seg):
            assert row["M3_max"].iloc[0] == pytest.approx(seg["M3"].max())
            assert row["V2_min"].iloc[0] == pytest.approx(seg["V2"].min())


@pytest.mark.skipif(not os.environ.get("ETABS_EXPORT_XLSX"), reason="ETABS_EXPORT_XLSX not set")
def test_excel_export_matches_live(eng):
    xl = Engine(ExcelSource(os.environ["ETABS_EXPORT_XLSX"]))

    def compare(table, key, columns, **kw):
        a = xl.table(table, **kw).set_index(key)
        b = eng.table(table, **kw).set_index(key)
        common = a.index.intersection(b.index)
        assert len(common) > 0, table
        for c in columns:
            assert a.loc[common, c].tolist() == pytest.approx(b.loc[common, c].tolist(), rel=1e-3, abs=1e-6, nan_ok=True), (table, c)
            assert a.attrs["units"][c] == b.attrs["units"][c]

    compare("Frame Assignments - Summary", "UniqueName", ["Length"])
    compare("Frame Section Property Definitions - Summary", "Name", ["Area", "I33"])
    beams_xl = xl.table("Element Forces - Beams")
    combo, beam = beams_xl["OutputCase"].iloc[0], beams_xl["UniqueName"].iloc[0]
    a = beams_xl[(beams_xl["OutputCase"] == combo) & (beams_xl["UniqueName"] == beam)].sort_values(["Station", "M3"])
    b = eng.table("Element Forces - Beams", combos=[combo])
    b = b[b["UniqueName"] == beam].sort_values(["Station", "M3"])
    assert a["Station"].tolist() == pytest.approx(b["Station"].tolist(), abs=1e-4)
    assert a["M3"].tolist() == pytest.approx(b["M3"].tolist(), rel=1e-3, abs=1e-3)


def test_select_by_label_and_name_restores_selection(eng):
    before = eng.run("selected")
    beams, cols = eng.run("beams"), eng.run("columns")
    b = beams.iloc[0]
    assert eng.run("name_from_label", label=b["Label"], story=b["Story"], kind="beam") == b["UniqueName"]
    try:
        got = eng.run("select", labels=[b["Label"]], story=b["Story"], kind="beam")
        got += eng.run("select", names=[cols.iloc[0]["UniqueName"]], kind="frame", clear=False)
        sel = eng.run("selected")
        assert sorted(sel["UniqueName"]) == sorted(got) and set(sel["Type"]) == {"Frame"}
        eng.run("clear_selection")
        assert eng.run("selected").empty
    finally:
        eng.run("clear_selection")
        for r in before.itertuples():
            kind = {"Frame": "frame", "Area": "area", "Point": "point"}.get(r.Type)
            if kind:
                eng.run("select", names=[r.UniqueName], kind=kind, clear=False)


def test_frame_info_matches_table(eng):
    c = eng.run("columns").iloc[0]
    info = eng.run("frame_info", name=c["UniqueName"])
    assert info["story"] == c["Story"] and info["section"] == c["AnalysisSect"] and info["type"] == "Column"
    assert info["length"] == pytest.approx(c["Length"], abs=1e-3)
