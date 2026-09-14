"""
Module etabs_model.py
Lop EtabsModel: diem truy cap chinh toi mo hinh ETABS dang mo.
- Thong tin tang: stories(), story_at(z)
- Thong tin cau kien: frames(), beams(), columns(), shells(), walls(), slabs(), points(), frame_info()
- Noi luc: frame_forces(), pier_forces(), joint_reactions()
- Du lieu khoi luong lon / ghi du lieu: self.bridge (EtabsDataBridge)
"""
from bisect import bisect_left
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
        return df

    def story_at(self, z: float, tol: float = 1e-3) -> Optional[str]:
        """
        Xac dinh tang chua cao do z theo quy uoc ETABS: tang X chua (cao do tang duoi, cao do tang X].
        Tra ve 'Base' neu z tai cao do chan cong trinh, None neu nam ngoai mo hinh.
        """
        st = self.stories()
        return story_from_elevation(z, st["Elevation"].tolist(), st["Story"].tolist(),
                                    st.attrs["base_elevation"], tol)

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


def story_from_elevation(z: float, elevations: List[float], names: List[str],
                         base: float, tol: float = 1e-3) -> Optional[str]:
    """
    elevations/names sap xep tu duoi len. Tang X chua z trong (cao do tang duoi, cao do tang X].
    """
    if abs(z - base) <= tol:
        return "Base"
    if z < base or z > elevations[-1] + tol:
        return None
    return names[bisect_left(elevations, z - tol)]
