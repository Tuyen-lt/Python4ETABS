"""Test EtabsModel. Phan offline luon chay; phan live can ETABS dang mo (read-only)."""
import sys
import time

import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etabs_model import (EtabsModel, EtabsResultError, story_from_elevation, _apply_modifiers,
                         result_to_frame, zone_envelope, PIER_FORCE_COLS, FRAME_MODS, AREA_MODS)


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
    # zone_envelope: dam L=8, tram 0..8, M3 = ObjSta -> Start [0,2], Middle [2,6], End [6,8]
    f = pd.DataFrame({"Obj": "B", "ObjSta": [float(i) for i in range(9)], "LoadCase": "SW",
                      "StepType": "", "P": 0.0, "V2": 0.0, "V3": 0.0, "T": 0.0, "M2": 0.0,
                      "M3": [float(i) for i in range(9)]})
    z = zone_envelope(f, pd.Series({"B": 8.0}))
    assert z["Zone"].tolist() == ["Start", "Middle", "End"], z
    assert z["M3_min"].tolist() == [0, 2, 6] and z["M3_max"].tolist() == [2, 6, 8], z
    z = zone_envelope(pd.concat([f, f.assign(LoadCase="LL", M3=-f["M3"])]), pd.Series({"B": 8.0}), envelope=True)
    assert len(z) == 3 and z["M3_min"].tolist() == [-2, -6, -8] and z["M3_max"].tolist() == [2, 6, 8], z
    try:
        zone_envelope(f, pd.Series({"B": 8.0}), zones=(0.3, 0.3))
        raise AssertionError("tong zones != 1 phai raise")
    except ValueError:
        pass
    # _apply_modifiers: Sect x Obj, doi tuong khong co trong bang modifier -> Obj = 1
    props = pd.DataFrame({"UniqueName": ["1", "2"], "Sect_I3Mod": [0.7, 0.35], "Sect_AMod": [1.0, 1.0]})
    obj = pd.DataFrame({"UniqueName": ["1"], "I3Mod": ["0.5"], "AMod": ["0.8"]})
    r = _apply_modifiers(props, obj, ["AMod", "I3Mod"])
    assert r["Obj_I3Mod"].tolist() == [0.5, 1.0] and r["I3Mod"].tolist() == [0.35, 0.35], r
    assert r["AMod"].tolist() == [0.8, 1.0], r
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

    s = m.sap_model
    # --- chon doi tuong (khoi phuc lua chon cu sau khi test)
    old = m.selected()
    bm0 = bm.iloc[0]
    assert m.name_from_label(bm0["Label"], bm0["Story"], "beam") == bm0["UniqueName"]
    got = m.select(labels=[bm0["Label"]], story=bm0["Story"], kind="beam")
    got += m.select(names=[co.iloc[0]["UniqueName"]], kind="frame", clear=False)
    sel = m.selected()
    assert sorted(sel["UniqueName"]) == sorted(got) and set(sel["Type"]) == {"Frame"}, sel
    print("select:", sel.to_dict("records"))
    m.clear_selection()
    assert m.selected().empty
    for r in old.itertuples():
        kind = {"Frame": "frame", "Area": "area", "Point": "point"}.get(r.Type)
        if kind:
            m._obj(kind).SetSelected(r.UniqueName, True)

    # --- thuoc tinh frame: doi chieu modifier 2 cap + vat lieu voi API
    t1 = time.time()
    fp = m.frame_properties()
    print(f"frame_properties: {fp.shape} in {time.time() - t1:.1f}s")
    assert len(fp) == len(fr) and fp["UniqueName"].is_unique
    sect_ne = (fp[[f"Sect_{c}" for c in FRAME_MODS]] != 1).any(axis=1)
    obj_ne = (fp[[f"Obj_{c}" for c in FRAME_MODS]] != 1).any(axis=1)
    checked = pd.concat([fp[sect_ne & obj_ne].head(10), fp[sect_ne].head(10), fp[obj_ne].head(10)]).drop_duplicates("UniqueName")
    print(f"frame co Sect modifier != 1: {int(sect_ne.sum())}, Obj modifier != 1: {int(obj_ne.sum())}")
    for r in pd_rows(checked if len(checked) else fp.head(5)):
        obj_mod = s.FrameObj.GetModifiers(r["UniqueName"])[0]
        sect_mod = s.PropFrame.GetModifiers(r["AnalysisSect"])[0]
        for i, c in enumerate(FRAME_MODS):
            assert abs(r[f"Obj_{c}"] - obj_mod[i]) < 1e-6, (r["UniqueName"], c, r[f"Obj_{c}"], obj_mod)
            assert abs(r[f"Sect_{c}"] - sect_mod[i]) < 1e-6, (r["AnalysisSect"], c, r[f"Sect_{c}"], sect_mod)
            assert abs(r[c] - obj_mod[i] * sect_mod[i]) < 1e-6
        assert r["Material"] == s.PropFrame.GetMaterial(r["AnalysisSect"])[0]
        assert abs(r["E1"] - s.PropMaterial.GetMPIsotropic(r["Material"])[0]) < 1e-3 * r["E1"]
    print(f"frame modifier (Sect x Obj) + vat lieu khop API tren {len(checked)} frame")

    # --- thuoc tinh area
    ap = m.area_properties()
    print(f"area_properties: {ap.shape}")
    assert len(ap) == len(m.shells()) and ap["UniqueName"].is_unique
    sect_ne = (ap[[f"Sect_{c}" for c in AREA_MODS if f"Sect_{c}" in ap.columns]] != 1).any(axis=1)
    obj_ne = (ap[[f"Obj_{c}" for c in AREA_MODS]] != 1).any(axis=1)
    checked = pd.concat([ap[sect_ne & obj_ne].head(10), ap[sect_ne].head(10), ap[obj_ne].head(10)]).drop_duplicates("UniqueName")
    print(f"area co Sect modifier != 1: {int(sect_ne.sum())}, Obj modifier != 1: {int(obj_ne.sum())}")
    for r in pd_rows(checked):
        obj_mod = s.AreaObj.GetModifiers(r["UniqueName"])[0]
        sect_mod = s.PropArea.GetModifiers(r["SectProp"])[0]
        for i, c in enumerate(AREA_MODS):
            assert abs(r[f"Obj_{c}"] - obj_mod[i]) < 1e-6, (r["UniqueName"], c, r[f"Obj_{c}"], obj_mod)
            assert abs(r[f"Sect_{c}"] - sect_mod[i]) < 1e-6, (r["SectProp"], c, r[f"Sect_{c}"], sect_mod)
            assert abs(r[c] - obj_mod[i] * sect_mod[i]) < 1e-6, (r["UniqueName"], c, r[c], obj_mod, sect_mod)
    print(f"area modifier (Sect x Obj) khop API tren {len(checked)} area")

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

        # --- noi luc dam theo vung 0.25-0.5-0.25: doi chieu tinh tay tu frame_forces
        b = bm.iloc[0]
        bz = m.beam_forces_by_zone(b["UniqueName"], cases=case)
        raw = m.frame_forces(b["UniqueName"], cases=case)
        L = float(b["Length"])
        assert raw["ObjSta"].max() <= L + 1e-3, (raw["ObjSta"].max(), L)
        for zone, a, e in [("Start", 0, 0.25), ("Middle", 0.25, 0.75), ("End", 0.75, 1.0)]:
            seg = raw[(raw["ObjSta"] >= a * L - 1e-3) & (raw["ObjSta"] <= e * L + 1e-3)]
            row_z = bz[bz["Zone"] == zone].iloc[0]
            if len(seg):
                assert abs(row_z["M3_max"] - seg["M3"].max()) < 1e-6 and abs(row_z["V2_min"] - seg["V2"].min()) < 1e-6
        print(f"[{case}] beam_forces_by_zone {b['Label']}@{b['Story']} L={L}:")
        print(bz[["Zone", "M3_max", "M3_min", "V2_max", "V2_min"]].to_string())
        t1 = time.time()
        allz = m.beam_forces_by_zone(cases=case)
        assert set(allz["Zone"]) == {"Start", "Middle", "End"}
        print(f"[{case}] beam_forces_by_zone toan bo: {len(allz)} dong / {allz['Obj'].nunique()} dam in {time.time() - t1:.1f}s")

        pf = m.pier_forces(cases=case)
        print(f"[{case}] pier_forces={len(pf)}, frame_forces All={len(m.frame_forces(cases=case))}")
    print(f"live OK in {time.time() - t:.1f}s")


def pd_rows(df):
    return (r for _, r in df.iterrows())


if __name__ == "__main__":
    offline()
    live()
