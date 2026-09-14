"""Test EtabsModel. Phan offline luon chay; phan live can ETABS dang mo (read-only)."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etabs_model import (EtabsModel, EtabsResultError, story_from_elevation,
                         result_to_frame, PIER_FORCE_COLS)


def offline():
    elevs, names = [0.0, 3.0, 6.0], ["S1", "S2", "S3"]
    base = -3.0
    assert story_from_elevation(-3.0, elevs, names, base) == "Base"
    assert story_from_elevation(-3.0, elevs, names, base, base_name="B2") == "B2"
    assert story_from_elevation(-1.0, elevs, names, base) == "S1"
    assert story_from_elevation(0.0, elevs, names, base) == "S1"      # dung cao do tang -> thuoc tang do
    assert story_from_elevation(0.0005, elevs, names, base) == "S1"   # trong dung sai
    assert story_from_elevation(1.5, elevs, names, base) == "S2"
    assert story_from_elevation(6.0, elevs, names, base) == "S3"
    assert story_from_elevation(7.0, elevs, names, base) is None
    assert story_from_elevation(-5.0, elevs, names, base) is None

    ret = [2, ("S1", "S1"), ("P1", "P1"), ("C", "C"), ("Top", "Bottom"),
           (-10.0, -12.0), (1, 1), (2, 2), (0, 0), (3, 3), (4, 4), 0]
    df = result_to_frame(ret, PIER_FORCE_COLS)
    assert df.shape == (2, 10) and df["P"].tolist() == [-10.0, -12.0]
    assert result_to_frame([0, (), (), (), (), (), (), (), (), (), (), 0], PIER_FORCE_COLS).empty
    try:
        result_to_frame([0] + [()] * 10 + [1], PIER_FORCE_COLS)
        raise AssertionError("ret != 0 phai raise")
    except EtabsResultError:
        pass
    print("offline OK")


def live():
    t = time.time()
    m = EtabsModel()
    print("Model:", m.filename, "units:", m.get_units())

    st = m.stories()
    print(f"stories: {len(st)}, base={st.attrs['base_name']}@{st.attrs['base_elevation']}, top={st['Story'].iloc[-1]}")

    pts = m.points()
    sample = pts  # toan bo diem
    found = m.story_at(sample["Z"])
    mismatch = [(r.UniqueName, r.Z, r.Story, s) for r, s in zip(sample.itertuples(), found) if s != r.Story]
    assert m.story_at(float(sample["Z"].iloc[0])) == found[0]
    print(f"story_at vs ETABS Story tren {len(sample)} diem: {len(mismatch)} lech", mismatch[:5])
    assert not mismatch

    fr, bm, co = m.frames(), m.beams(), m.columns()
    print(f"frames={len(fr)} beams={len(bm)} columns={len(co)} walls={len(m.walls())} slabs={len(m.slabs())}")
    assert len(fr) > 0 and len(bm) + len(co) <= len(fr)

    row = co.iloc[0]
    info = m.frame_info(row["UniqueName"])
    print("frame_info:", info)
    assert info["story"] == row["Story"] and info["label"] == row["Label"]
    assert info["section"] == row["AnalysisSect"] and info["type"] == "Column", info
    assert abs(info["length"] - row["Length"]) < 1.0
    assert m.element_story(row["UniqueName"]) == row["Story"]

    _, cases, status, _ = m.sap_model.Analyze.GetCaseStatus()
    done = [c for c, s in zip(cases, status) if s == 4]  # 4 = Finished
    if not done:
        try:
            m.frame_forces(row["UniqueName"])
            raise AssertionError("chua phan tich ma khong raise")
        except EtabsResultError as e:
            print("noi luc: chua co ket qua phan tich ->", e)
    else:
        case = done[0]
        jr = m.joint_reactions(cases=case)
        fz = m.sap_model.Results.BaseReact()[6][0]
        print(f"[{case}] joint_reactions={len(jr)} sum F3={jr['F3'].sum():.1f} BaseReact FZ={fz:.1f}")
        assert abs(jr["F3"].sum() - fz) <= 1e-6 * abs(fz) + 1

        ff = m.frame_forces(row["UniqueName"], cases=case)
        m.sap_model.DatabaseTables.SetLoadCasesSelectedForDisplay([case])
        tb = m.bridge.pull_table("Element Forces - Columns")
        tb = tb[(tb["UniqueName"] == row["UniqueName"]) & (tb["OutputCase"] == case)]
        for c in ["P", "V2", "V3", "T", "M2", "M3"]:
            assert sorted(ff[c].round(0)) == sorted(tb[c].astype(float).round(0)), c
        print(f"[{case}] frame_forces {row['UniqueName']}: {len(ff)} tram, khop bang Element Forces - Columns")

        pf = m.pier_forces(cases=case)
        print(f"[{case}] pier_forces={len(pf)}, frame_forces All={len(m.frame_forces(cases=case))}")
    print(f"live OK in {time.time() - t:.1f}s")


if __name__ == "__main__":
    offline()
    live()
