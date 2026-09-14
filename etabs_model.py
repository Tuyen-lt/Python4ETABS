"""
Module etabs_model.py
Lop EtabsModel: diem truy cap chinh toi mo hinh ETABS dang mo.
- Thong tin tang: stories(), story_at(z)
- Thong tin cau kien: frames(), beams(), columns(), shells(), walls(), slabs(), points(), frame_info()
- Chon doi tuong: select(), name_from_label(), selected(), clear_selection()
- Thuoc tinh: frame_properties(), area_properties(), materials()
- Noi luc: frame_forces(), pier_forces(), joint_reactions(), beam_forces_by_zone()
- Du lieu khoi luong lon / ghi du lieu: self.bridge (EtabsDataBridge)
"""
from bisect import bisect_left
from numbers import Real
from typing import Iterable, Optional, Union, List, Dict, Any

import pandas as pd

from connection import get_active_etabs, is_model_locked, ensure_unlocked
from data_bridge import EtabsDataBridge


# eUnits cua CSI OAPI: (luc, chieu dai) -> ma
UNITS = {
    ("lb", "in"): 1, ("lb", "ft"): 2, ("kip", "in"): 3, ("kip", "ft"): 4,
    ("kN", "mm"): 5, ("kN", "m"): 6, ("kgf", "mm"): 7, ("kgf", "m"): 8,
    ("N", "mm"): 9, ("N", "m"): 10, ("Ton", "mm"): 11, ("Ton", "m"): 12,
    ("kN", "cm"): 13, ("kgf", "cm"): 14, ("N", "cm"): 15, ("Ton", "cm"): 16,
}

# eFrameDesignOrientation
FRAME_TYPES = {1: "Column", 2: "Beam", 3: "Brace", 4: "Null", 5: "Other"}

FRAME_FORCE_COLS = ["Obj", "ObjSta", "Elm", "ElmSta", "LoadCase", "StepType", "StepNum",
                    "P", "V2", "V3", "T", "M2", "M3"]
PIER_FORCE_COLS = ["Story", "Pier", "LoadCase", "Location", "P", "V2", "V3", "T", "M2", "M3"]
JOINT_REACT_COLS = ["Obj", "Elm", "LoadCase", "StepType", "StepNum", "F1", "F2", "F3", "M1", "M2", "M3"]

# eItemTypeElm
OBJECT, GROUP = 0, 2


class EtabsResultError(Exception):
    """Ngoai le khi ETABS khong tra ve ket qua (chua chay phan tich, sai ten case...)."""
    pass


def _to_list(names: Union[str, Iterable[str], None]) -> List[str]:
    if names is None:
        return []
    return [names] if isinstance(names, str) else list(names)


def _numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def result_to_frame(ret: list, columns: List[str], what: str = "Ket qua") -> pd.DataFrame:
    """
    Chuyen output cua Results.* ([NumberResults, arr1, arr2, ..., ret_code]) thanh DataFrame.
    """
    code = ret[-1]
    if code != 0:
        raise EtabsResultError(
            f"{what}: ETABS tra ve ma loi {code}. Kiem tra mo hinh da chay phan tich "
            f"va ten load case/combo hop le."
        )
    n = ret[0]
    arrays = ret[1:1 + len(columns)]
    return pd.DataFrame({c: list(a)[:n] for c, a in zip(columns, arrays)}, columns=columns)


class EtabsModel:
    def __init__(self, sap_model=None, pid: Optional[int] = None):
        """
        Ket noi toi ETABS dang mo (hoac dung sap_model co san).
        """
        self.sap_model = sap_model if sap_model is not None else get_active_etabs(pid=pid)
        self.bridge = EtabsDataBridge(self.sap_model)

    # ------------------------------------------------------------------ chung
    @property
    def filename(self) -> str:
        return self.sap_model.GetModelFilename()

    def is_locked(self) -> bool:
        return is_model_locked(self.sap_model)

    def ensure_unlocked(self, operation_name: str = "Thao tac"):
        ensure_unlocked(self.sap_model, operation_name)

    def get_units(self) -> tuple:
        code = self.sap_model.GetPresentUnits()
        return next(k for k, v in UNITS.items() if v == code)

    def set_units(self, force: str = "kN", length: str = "m"):
        """
        Dat don vi hien hanh. VD: set_units('kN', 'm'), set_units('N', 'mm').
        """
        if (force, length) not in UNITS:
            raise ValueError(f"Don vi khong hop le: {force}, {length}. Chon trong {list(UNITS)}")
        ret = self.sap_model.SetPresentUnits(UNITS[(force, length)])
        if ret != 0:
            raise RuntimeError(f"SetPresentUnits that bai (ret={ret})")

    # ------------------------------------------------------------------- tang
    def stories(self) -> pd.DataFrame:
        """
        Danh sach tang tu duoi len: Story, Elevation (cao do dinh tang), Height.
        """
        ret = self.sap_model.Story.GetStories_2()
        base, _, names, elevs, heights = ret[:5]
        df = pd.DataFrame({"Story": list(names), "Elevation": list(elevs), "Height": list(heights)})
        df = df.sort_values("Elevation", ignore_index=True)
        df.attrs["base_elevation"] = base
        bs = self.bridge.pull_table("Tower and Base Story Definitions")
        df.attrs["base_name"] = bs["BSName"].iloc[0] if "BSName" in bs.columns and len(bs) else "Base"
        return df

    def story_at(self, z: Union[float, Iterable[float]], tol: float = 1e-3):
        """
        Xac dinh tang chua cao do z theo quy uoc ETABS: tang X chua (cao do tang duoi, cao do tang X].
        Tra ve ten base story (VD 'Base') neu z tai cao do chan cong trinh, None neu nam ngoai mo hinh.
        z la danh sach -> tra ve list (chi doc bang tang 1 lan, nen dung cach nay khi xu ly nhieu diem).
        """
        st = self.stories()
        args = (st["Elevation"].tolist(), st["Story"].tolist(), st.attrs["base_elevation"], tol,
                st.attrs["base_name"])
        if isinstance(z, Real):
            return story_from_elevation(z, *args)
        return [story_from_elevation(v, *args) for v in z]

    def element_story(self, name: str, kind: str = "frame") -> str:
        """
        Tang cua cau kien theo UniqueName. kind: 'frame' | 'area' | 'point'.
        """
        obj = {"frame": self.sap_model.FrameObj, "area": self.sap_model.AreaObj,
               "point": self.sap_model.PointObj}[kind]
        label, story, ret = obj.GetLabelFromName(str(name))
        if ret != 0:
            raise KeyError(f"Khong tim thay {kind} '{name}'")
        return story

    # -------------------------------------------------------------- cau kien
    def frames(self, story: Optional[str] = None) -> pd.DataFrame:
        """
        Toan bo frame: Story, Label, UniqueName, Type (Beam/Column/Brace), Length, AnalysisSect, DesignSect...
        """
        df = _numeric(self.bridge.pull_table("Frame Assignments - Summary"), ["Length", "AxisAngle"])
        return df[df["Story"] == story].reset_index(drop=True) if story else df

    def beams(self, story: Optional[str] = None) -> pd.DataFrame:
        df = self.frames(story)
        return df[df["Type"] == "Beam"].reset_index(drop=True)

    def columns(self, story: Optional[str] = None) -> pd.DataFrame:
        df = self.frames(story)
        return df[df["Type"] == "Column"].reset_index(drop=True)

    def shells(self, story: Optional[str] = None) -> pd.DataFrame:
        """
        Toan bo area: Story, Label, UniqueName, SectProp, PropType (Slab/Wall/...), Diaphragm, Pier.
        """
        df = _numeric(self.bridge.pull_table("Area Assignments - Summary"), ["AxisAngle"])
        return df[df["Story"] == story].reset_index(drop=True) if story else df

    def walls(self, story: Optional[str] = None) -> pd.DataFrame:
        df = self.shells(story)
        return df[df["PropType"] == "Wall"].reset_index(drop=True)

    def slabs(self, story: Optional[str] = None) -> pd.DataFrame:
        df = self.shells(story)
        return df[df["PropType"] == "Slab"].reset_index(drop=True)

    def points(self, story: Optional[str] = None) -> pd.DataFrame:
        """
        Toan bo point: UniqueName, Story, X, Y, Z...
        """
        df = _numeric(self.bridge.pull_points(), ["X", "Y", "Z"])
        return df[df["Story"] == story].reset_index(drop=True) if story else df

    def frame_info(self, name: str) -> Dict[str, Any]:
        """
        Thong tin chi tiet 1 frame: label, tang, tiet dien, loai, 2 nut va toa do, chieu dai.
        """
        fo, po = self.sap_model.FrameObj, self.sap_model.PointObj
        name = str(name)
        label, story, ret = fo.GetLabelFromName(name)
        if ret != 0:
            raise KeyError(f"Khong tim thay frame '{name}'")
        section = fo.GetSection(name)[0]
        orient = fo.GetDesignOrientation(name)[0]
        pi, pj, _ = fo.GetPoints(name)
        ci = tuple(po.GetCoordCartesian(pi)[:3])
        cj = tuple(po.GetCoordCartesian(pj)[:3])
        length = sum((a - b) ** 2 for a, b in zip(ci, cj)) ** 0.5
        return {
            "name": name, "label": label, "story": story, "section": section,
            "type": FRAME_TYPES.get(orient, "Other"),
            "point_i": pi, "point_j": pj, "coord_i": ci, "coord_j": cj, "length": length,
        }

    # ---------------------------------------------------------------- chon doi tuong
    def _obj(self, kind: str):
        kind = KIND_ALIASES.get(kind.lower())
        if kind is None:
            raise ValueError(f"kind khong hop le. Chon trong {sorted(KIND_ALIASES)}")
        return {"frame": self.sap_model.FrameObj, "area": self.sap_model.AreaObj,
                "point": self.sap_model.PointObj}[kind]

    def name_from_label(self, label: str, story: str, kind: str = "frame") -> str:
        """
        Label + tang -> UniqueName. kind: frame/beam/column/brace | area/shell/wall/slab | point/joint.
        """
        name, ret = self._obj(kind).GetNameFromLabel(str(label), str(story))
        if ret != 0 or not name:
            raise KeyError(f"Khong tim thay {kind} label='{label}' tang='{story}'")
        return name

    def select(self, names=None, kind: str = "frame", labels=None, story=None,
               clear: bool = True) -> List[str]:
        """
        Chon doi tuong tren giao dien ETABS. Tra ve danh sach UniqueName da chon.
        - Theo UniqueName + loai: select(names=['83', '84'], kind='frame')
        - Theo Label + tang:      select(labels=['B1', 'B2'], story='FL1', kind='beam')
          story co the la list cung do dai voi labels.
        clear=True: bo chon cac doi tuong cu truoc.
        """
        obj = self._obj(kind)
        names = _to_list(names)
        labels = _to_list(labels)
        if labels:
            stories = _to_list(story)
            if len(stories) == 1:
                stories = stories * len(labels)
            if len(stories) != len(labels):
                raise ValueError("story phai la 1 ten tang hoac list cung do dai voi labels")
            names += [self.name_from_label(l, s, kind) for l, s in zip(labels, stories)]
        if not names:
            raise ValueError("Can truyen names hoac labels + story")
        if clear:
            self.clear_selection()
        for n in names:
            if obj.SetSelected(str(n), True) != 0:
                raise KeyError(f"Khong chon duoc {kind} '{n}'")
        self.sap_model.View.RefreshView(0, False)
        return [str(n) for n in names]

    def selected(self) -> pd.DataFrame:
        """
        Danh sach doi tuong dang duoc chon: Type (Point/Frame/Area/...), UniqueName.
        """
        n, types, names, _ = self.sap_model.SelectObj.GetSelected()
        return pd.DataFrame({"Type": [SELECT_TYPES.get(t, t) for t in types][:n], "UniqueName": list(names)[:n]})

    def clear_selection(self):
        self.sap_model.SelectObj.ClearSelection()

    # ---------------------------------------------------------------- thuoc tinh
    def materials(self) -> pd.DataFrame:
        """
        Vat lieu: Type, MatType, Grade, UnitWeight, E1, G12, U12, A1, Fc (be tong), Fy/Fu (thep, cot thep).
        """
        df = self.bridge.pull_table("Material Properties - General")[["Material", "Type", "Grade"]]
        df = df.rename(columns={"Type": "MatType"})
        df = df.merge(self.bridge.pull_table("Material Properties - Basic Mechanical Properties"),
                      on="Material", how="left")
        conc = self.bridge.pull_table("Material Properties - Concrete Data")
        if "Fc" in conc.columns:
            df = df.merge(conc[["Material", "Fc"]], on="Material", how="left")
        steel = pd.concat([self.bridge.pull_table(t) for t in
                           ("Material Properties - Steel Data", "Material Properties - Rebar Data",
                            "Material Properties - Tendon Data")], ignore_index=True)
        if "Fy" in steel.columns:
            df = df.merge(steel[["Material", "Fy", "Fu"]].drop_duplicates("Material"), on="Material", how="left")
        return _auto_numeric(df)

    def _assignments(self, prefix: str, skip: Iterable[str] = ()) -> pd.DataFrame:
        """
        Gop moi bang '<prefix>*' co 1 dong/doi tuong theo UniqueName.
        """
        tables = [t for t in self.bridge.get_available_tables() if t.startswith(prefix) and t not in skip]
        base = self.bridge.pull_table(tables.pop(tables.index(prefix + "Summary")))
        for t in tables:
            df = self.bridge.pull_table(t)
            if df.empty or "UniqueName" not in df.columns or df["UniqueName"].duplicated().any():
                continue  # ponytail: bang nhieu dong/doi tuong (tai trong...) bi bo qua, can thi pull_table rieng
            cols = ["UniqueName"] + [c for c in df.columns if c not in base.columns]
            if len(cols) > 1:
                base = base.merge(df[cols], on="UniqueName", how="left")
        return base

    def frame_properties(self) -> pd.DataFrame:
        """
        Toan bo frame kem: assignment (tang, loai, chieu dai, offset, insertion point, local axis...),
        tiet dien (kich thuoc t3/t2, A, I33, I22, J...), stiffness modifier va vat lieu.
        Modifier: Sect_* (cua tiet dien) x Obj_* (gan cho doi tuong, mac dinh 1) = cot hieu dung (AMod, I3Mod...).
        """
        mods = self.bridge.pull_table("Frame Assignments - Property Modifiers")
        df = self._assignments("Frame Assignments - ", skip=["Frame Assignments - Property Modifiers"])

        sect = self.bridge.pull_table("Frame Section Property Definitions - Summary")
        dims = [self.bridge.pull_table(t) for t in self.bridge.get_available_tables()
                if t.startswith("Frame Section Property Definitions - ")]
        dims = [d[["Name"] + [c for c in ("t3", "t2", "tf", "tw") if c in d.columns]] for d in dims
                if "Name" in d.columns and "t3" in d.columns]
        if dims:
            sect = sect.merge(pd.concat(dims, ignore_index=True).drop_duplicates("Name"), on="Name", how="left")
        sect = sect.drop(columns=[c for c in ("Color",) if c in sect.columns])
        sect = sect.rename(columns={**{m: f"Sect_{m}" for m in FRAME_MODS}, "Name": "AnalysisSect"})

        df = df.merge(sect.drop(columns=[c for c in sect.columns if c in df.columns and c != "AnalysisSect"]),
                      on="AnalysisSect", how="left")
        df = _apply_modifiers(df, mods, FRAME_MODS)
        return _auto_numeric(df.merge(self.materials(), on="Material", how="left"))

    def area_properties(self) -> pd.DataFrame:
        """
        Toan bo area (san, vach) kem: assignment, tiet dien (loai, chieu day), stiffness modifier va vat lieu.
        Modifier: Sect_* x Obj_* = cot hieu dung (f11Mod, m11Mod...).
        """
        mods = self.bridge.pull_table("Area Assignments - Stiffness Modifiers")
        df = self._assignments("Area Assignments - ", skip=["Area Assignments - Stiffness Modifiers"])

        sect = self.bridge.pull_table("Area Section Property Definitions - Summary")
        sect = sect.rename(columns={"Name": "SectProp", "Type": "SectType"})
        prop_mods = pd.concat([self.bridge.pull_table(t) for t in
                               ("Slab Property Definitions", "Wall Property Definitions - Specified",
                                "Deck Property Definitions")], ignore_index=True)
        if "Name" in prop_mods.columns:
            keep = ["Name"] + [m for m in AREA_MODS if m in prop_mods.columns]
            prop_mods = prop_mods[keep].drop_duplicates("Name")
            prop_mods = prop_mods.rename(columns={**{m: f"Sect_{m}" for m in AREA_MODS}, "Name": "SectProp"})
            sect = sect.merge(prop_mods, on="SectProp", how="left")

        df = df.merge(sect.drop(columns=[c for c in sect.columns if c in df.columns and c != "SectProp"]),
                      on="SectProp", how="left")
        df = _apply_modifiers(df, mods, AREA_MODS)
        return _auto_numeric(df.merge(self.materials(), on="Material", how="left"))

    # ---------------------------------------------------------------- noi luc
    def select_output(self, cases=None, combos=None):
        """
        Chon load case / combo cho output ket qua. Bo trong ca hai = giu nguyen lua chon hien tai.
        """
        cases, combos = _to_list(cases), _to_list(combos)
        if not cases and not combos:
            return
        setup = self.sap_model.Results.Setup
        setup.DeselectAllCasesAndCombosForOutput()
        for c in cases:
            if setup.SetCaseSelectedForOutput(c) != 0:
                raise EtabsResultError(f"Load case khong ton tai: '{c}'")
        for c in combos:
            if setup.SetComboSelectedForOutput(c) != 0:
                raise EtabsResultError(f"Load combo khong ton tai: '{c}'")

    def frame_forces(self, names=None, cases=None, combos=None) -> pd.DataFrame:
        """
        Noi luc frame (P, V2, V3, T, M2, M3) theo tram. names=None -> toan bo frame (group 'All').
        """
        self.select_output(cases, combos)
        names = _to_list(names)
        if not names:
            ret = self.sap_model.Results.FrameForce("All", GROUP)
            return _numeric(result_to_frame(ret, FRAME_FORCE_COLS, "FrameForce"), FRAME_FORCE_COLS[7:])
        dfs = [result_to_frame(self.sap_model.Results.FrameForce(str(n), OBJECT), FRAME_FORCE_COLS,
                               f"FrameForce '{n}'") for n in names]
        return _numeric(pd.concat(dfs, ignore_index=True), FRAME_FORCE_COLS[7:])

    def pier_forces(self, piers=None, stories=None, cases=None, combos=None) -> pd.DataFrame:
        """
        Noi luc pier vach (Top/Bottom) theo tang.
        """
        self.select_output(cases, combos)
        df = result_to_frame(self.sap_model.Results.PierForce(), PIER_FORCE_COLS, "PierForce")
        piers, stories = _to_list(piers), _to_list(stories)
        if piers:
            df = df[df["Pier"].isin(piers)]
        if stories:
            df = df[df["Story"].isin(stories)]
        return _numeric(df.reset_index(drop=True), PIER_FORCE_COLS[4:])

    def joint_reactions(self, names=None, cases=None, combos=None) -> pd.DataFrame:
        """
        Phan luc goi (F1, F2, F3, M1, M2, M3). names=None -> toan bo goi.
        """
        self.select_output(cases, combos)
        names = _to_list(names)
        if not names:
            ret = self.sap_model.Results.JointReact("All", GROUP)
            return _numeric(result_to_frame(ret, JOINT_REACT_COLS, "JointReact"), JOINT_REACT_COLS[5:])
        dfs = [result_to_frame(self.sap_model.Results.JointReact(str(n), OBJECT), JOINT_REACT_COLS,
                               f"JointReact '{n}'") for n in names]
        return _numeric(pd.concat(dfs, ignore_index=True), JOINT_REACT_COLS[5:])

    def beam_forces_by_zone(self, names=None, cases=None, combos=None,
                            zones=(0.25, 0.5, 0.25), envelope: bool = False) -> pd.DataFrame:
        """
        Gom noi luc dam theo vung chieu dai (mac dinh 0.25L - 0.5L - 0.25L: Start / Middle / End).
        Moi vung tra ve max/min cua P, V2, V3, T, M2, M3.
        envelope=False: tach theo LoadCase (va StepType voi combo bao); True: bao tren moi case/combo da chon.
        names=None -> toan bo dam.
        """
        beams = self.beams()
        names = _to_list(names)
        if names:
            beams = beams[beams["UniqueName"].isin([str(n) for n in names])]
        forces = self.frame_forces(names or None, cases, combos)
        forces = forces[forces["Obj"].isin(beams["UniqueName"])]
        out = zone_envelope(forces, beams.set_index("UniqueName")["Length"], zones, envelope)
        info = beams[["UniqueName", "Story", "Label", "Length", "AnalysisSect"]].rename(columns={"UniqueName": "Obj"})
        return info.merge(out, on="Obj", how="right")


FORCE_COMPONENTS = ["P", "V2", "V3", "T", "M2", "M3"]
FRAME_MODS = ["AMod", "A2Mod", "A3Mod", "JMod", "I2Mod", "I3Mod", "MMod", "WMod"]
AREA_MODS = ["f11Mod", "f22Mod", "f12Mod", "m11Mod", "m22Mod", "m12Mod", "v13Mod", "v23Mod", "MMod", "WMod"]
KIND_ALIASES = {
    "frame": "frame", "beam": "frame", "column": "frame", "brace": "frame",
    "area": "area", "shell": "area", "wall": "area", "slab": "area",
    "point": "point", "joint": "point",
}
NAME_COLS = {"Story", "Label", "UniqueName", "Name", "AnalysisSect", "DesignSect", "SectProp", "Material",
             "Grade", "Pier", "PierName", "Spandrel", "SpandName", "SpandStory", "Diaphragm", "Shape", "Type",
             "PropType", "SectType", "DeckMat"}
# eObjType cua SelectObj.GetSelected
SELECT_TYPES = {1: "Point", 2: "Frame", 3: "Cable", 4: "Tendon", 5: "Area", 6: "Solid", 7: "Link"}


def _auto_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """
    Chuyen cot chuoi sang so neu moi gia tri khac rong deu la so. Cot ten (UniqueName, Label...) giu chuoi.
    """
    for c in df.columns:
        if df[c].dtype == object and c not in NAME_COLS:
            conv = pd.to_numeric(df[c], errors="coerce")
            if conv.notna().sum() == df[c].notna().sum():
                df[c] = conv
    return df


def _apply_modifiers(df: pd.DataFrame, obj_mods: pd.DataFrame, mod_cols: List[str]) -> pd.DataFrame:
    """
    Obj_* = modifier gan cho doi tuong (khong co trong bang -> 1), cot hieu dung = Sect_* x Obj_*.
    """
    present = [m for m in mod_cols if m in obj_mods.columns]
    obj_mods = obj_mods[["UniqueName"] + present].rename(columns={m: f"Obj_{m}" for m in present})
    df = df.merge(obj_mods, on="UniqueName", how="left")
    for m in present:
        obj = pd.to_numeric(df[f"Obj_{m}"], errors="coerce").fillna(1.0)
        sect = pd.to_numeric(df[f"Sect_{m}"], errors="coerce").fillna(1.0) if f"Sect_{m}" in df.columns else 1.0
        df[f"Obj_{m}"] = obj
        df[m] = sect * obj
    return df


def zone_names(zones) -> List[str]:
    return ["Start", "Middle", "End"] if len(zones) == 3 else [f"Z{i + 1}" for i in range(len(zones))]


def zone_envelope(forces: pd.DataFrame, lengths: pd.Series, zones=(0.25, 0.5, 0.25),
                  envelope: bool = False, tol: float = 1e-6) -> pd.DataFrame:
    """
    forces: output frame_forces (Obj, ObjSta, LoadCase, StepType, P..M3); lengths: Series UniqueName -> chieu dai.
    Tram nam dung ranh gioi 2 vung duoc tinh cho ca 2 vung.
    """
    if abs(sum(zones) - 1.0) > 1e-6:
        raise ValueError(f"Tong ty le zones phai = 1, dang la {sum(zones)}")
    rel = forces["ObjSta"] / forces["Obj"].map(lengths).astype(float)
    keys = ["Obj"] if envelope else ["Obj", "LoadCase", "StepType"]
    parts, start = [], 0.0
    for name, ratio in zip(zone_names(zones), zones):
        end = start + ratio
        part = forces[(rel >= start - tol) & (rel <= end + tol)].assign(Zone=name, ZoneStart=start, ZoneEnd=end)
        parts.append(part)
        start = end
    if not parts or forces.empty:
        return pd.DataFrame(columns=keys + ["Zone", "ZoneStart", "ZoneEnd"])
    grouped = pd.concat(parts).groupby(keys + ["Zone", "ZoneStart", "ZoneEnd"], sort=False)[FORCE_COMPONENTS]
    out = grouped.agg(["max", "min"])
    out.columns = [f"{c}_{s}" for c, s in out.columns]
    return out.reset_index().sort_values(keys + ["ZoneStart"], ignore_index=True)


def story_from_elevation(z: float, elevations: List[float], names: List[str],
                         base: float, tol: float = 1e-3, base_name: str = "Base") -> Optional[str]:
    """
    elevations/names sap xep tu duoi len. Tang X chua z trong (cao do tang duoi, cao do tang X].
    """
    if abs(z - base) <= tol:
        return base_name
    if z < base or z > elevations[-1] + tol:
        return None
    return names[bisect_left(elevations, z - tol)]
