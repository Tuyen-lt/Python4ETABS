"""
Read-only checks against a running ETABS (skipped when ETABS is not available).
Object selection is changed during the test and restored afterwards; output/display case selection may change.
"""
import pytest

from etabs_python import Engine


@pytest.fixture(scope="module")
def eng(etabs_live):
    return Engine(etabs_live)


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
