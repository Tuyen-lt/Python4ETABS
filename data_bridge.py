"""
Module data_bridge.py
Cung cap lop EtabsDataBridge ho tro Pull va Inject du lieu truc tiep voi ETABS trong bo nho Python.

Co che:
1. PULL (Doc): Su dung cDatabaseTables chuyen truc tiep sang pandas DataFrame trong bo nho.
   - pull_table: Doc bat ky bang nao thanh DataFrame.
   - pull_frames: Trich xuat thong tin dac tinh va connectivity cua toan bo Frame.
   - pull_shells: Trich xuat thong tin dac tinh va connectivity cua toan bo Shell (Area).
   - pull_points: Trich xuat toa do Joint Coordinates.

2. INJECT (Ghi/Day): Su dung Direct OAPI Methods it loi nhat cho tung loai du lieu.
   - inject_names: Doi Unique Name cho Frame, Shell, Point.
   - inject_group_definition: Tao dinh nghia nhom (GroupDef.SetGroup).
   - inject_group_assignment: Gan cau kien vao nhom (SetGroupAssign).
   - inject_frame_sections: Dinh nghia cac loai tiet dien Frame (Rectangular, Circle, I, Tube, Pipe).
   - inject_section_assignments: Gan tiet dien cho cau kien Frame va Shell.
"""
from typing import Optional, Dict, Any, List, Union
import logging
import pandas as pd
import comtypes.client

from connection import get_active_etabs, is_model_locked, ensure_unlocked, ModelLockedError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("EtabsDataBridge")


class EtabsDataBridge:
    def __init__(self, sap_model=None, pid: Optional[int] = None):
        """
        Khoi tao DataBridge. Neu sap_model la None, tu dong ket noi instance dang mo.
        """
        if sap_model is not None:
            self.sap_model = sap_model
        else:
            self.sap_model = get_active_etabs(pid=pid)

    def is_locked(self) -> bool:
        """
        Kiem tra trang thai khoa mo hinh.
        """
        return is_model_locked(self.sap_model)

    def _ensure_unlocked(self, operation_name: str = "Thao tac"):
        """
        Dung va bao loi neu mo hinh bi khoa truoc khi thuc hien ghi du lieu.
        """
        ensure_unlocked(self.sap_model, operation_name)

    # =========================================================================
    # NHOM PULL: Trich xuat du lieu truc tiep vao bo nho (Pandas DataFrame)
    # =========================================================================

    def pull_table(self, table_key: str) -> pd.DataFrame:
        """
        Doc mot bang Database Table tu ETABS va chuyen truc tiep thanh DataFrame trong bo nho.
        """
        try:
            ret = self.sap_model.DatabaseTables.GetTableForDisplayArray(table_key, [], "", 0, [], 0, [])
            fields = list(ret[2]) if (ret and len(ret) > 2 and ret[2]) else []
            table_data = list(ret[4]) if (ret and len(ret) > 4 and ret[4]) else []
            ret_code = ret[-1] if ret else -1

            if ret_code == 0 and fields:
                num_fields = len(fields)
                if num_fields > 0 and len(table_data) > 0:
                    rows = [table_data[i:i + num_fields] for i in range(0, len(table_data), num_fields)]
                    return pd.DataFrame(rows, columns=fields)
                return pd.DataFrame(columns=fields)
        except Exception as e:
            logger.debug(f"GetTableForDisplayArray failed for '{table_key}': {e}")

        try:
            ret_edit = self.sap_model.DatabaseTables.GetTableForEditingArray(table_key, "", 0, [], 0, [])
            fields = list(ret_edit[3]) if (ret_edit and len(ret_edit) > 3 and ret_edit[3]) else []
            table_data = list(ret_edit[5]) if (ret_edit and len(ret_edit) > 5 and ret_edit[5]) else []
            if fields:
                num_fields = len(fields)
                if num_fields > 0 and len(table_data) > 0:
                    rows = [table_data[i:i + num_fields] for i in range(0, len(table_data), num_fields)]
                    return pd.DataFrame(rows, columns=fields)
                return pd.DataFrame(columns=fields)
        except Exception as e:
            logger.debug(f"GetTableForEditingArray failed for '{table_key}': {e}")

        return pd.DataFrame()

    def get_available_tables(self) -> List[str]:
        """
        Lay danh sach toan bo cac bang Database co san trong mo hinh.
        """
        ret = self.sap_model.DatabaseTables.GetAvailableTables(0, [])
        return list(ret[1]) if ret[1] else []

    def pull_frames(self) -> pd.DataFrame:
        """
        Pull toan bo du lieu Frame (Story, Label, Unique Name, Section, Connectivity, Length).
        Tu dong kiem tra cac bang phu hop voi phien ban ETABS.
        """
        for tbl in ["Frame Assignments - Section Properties", "Frame Assignments - Summary", "Frame Section Assignments"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    def pull_shells(self) -> pd.DataFrame:
        """
        Pull toan bo du lieu Shell/Area (Story, Label, Unique Name, Section, Connectivity).
        """
        for tbl in ["Area Assignments - Section Properties", "Area Assignments - Summary", "Area Section Assignments"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    def pull_points(self) -> pd.DataFrame:
        """
        Pull toan bo toa do Joint Coordinates (Point, X, Y, Z).
        """
        for tbl in ["Point Object Connectivity", "Joint Coordinates", "Objects and Elements - Joints"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    # =========================================================================
    # NHOM INJECT: Ghi va cap nhat du lieu truc tiep qua OAPI
    # =========================================================================

    def inject_names(self, rename_records: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Doi Unique Name cho Frame, Shell, Point.
        rename_records: List[{'type': 'frame'|'shell'|'point', 'old_name': '...', 'new_name': '...'}]
        """
        self._ensure_unlocked("Doi ten cau kien")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, row in enumerate(rename_records, start=1):
            elem_type = row.get("type", "").lower()
            old_name = row.get("old_name", "")
            new_name = row.get("new_name", "")

            if not elem_type or not old_name or not new_name:
                results["failed"] += 1
                results["errors"].append(f"Dong {idx}: Thieu thong tin type, old_name hoac new_name")
                continue

            ret = -1
            if elem_type in ["frame", "beam", "column", "brace"]:
                ret = self.sap_model.FrameObj.ChangeName(old_name, new_name)
            elif elem_type in ["shell", "area", "floor", "slab", "wall"]:
                ret = self.sap_model.AreaObj.ChangeName(old_name, new_name)
            elif elem_type in ["point", "joint"]:
                ret = self.sap_model.PointObj.ChangeName(old_name, new_name)
            else:
                results["failed"] += 1
                results["errors"].append(f"Dong {idx}: Loai cau kien '{elem_type}' khong hop le")
                continue

            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Dong {idx}: Doi ten that bai (ret={ret}) cho {elem_type} '{old_name}' -> '{new_name}'")

        self.refresh_view()
        return results

    def inject_group_definition(self, group_names: Union[str, List[str]]) -> Dict[str, Any]:
        """
        Dinh nghia mot hoac nhieu nhom (Group) moi trong ETABS.
        """
        self._ensure_unlocked("Dinh nghia nhom")
        if isinstance(group_names, str):
            group_names = [group_names]

        results = {"success": 0, "failed": 0, "errors": []}
        for name in group_names:
            ret = self.sap_model.GroupDef.SetGroup(name)
            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Khong the tao group '{name}' (ret={ret})")

        return results

    def inject_group_assignment(self, group_name: str, elements: Dict[str, List[str]], remove: bool = False) -> Dict[str, Any]:
        """
        Gan cac cau kien vao mot nhom cu the.
        elements: Dict gom cac key:
            'frames': danh sach ten frame
            'shells': danh sach ten shell
            'points': danh sach ten point
        """
        self._ensure_unlocked("Gan cau kien vao nhom")
        # Dam bao group da duoc dinh nghia
        self.sap_model.GroupDef.SetGroup(group_name)

        results = {"assigned": 0, "failed": 0, "errors": []}

        # Gan Frames
        for frame in elements.get("frames", []):
            ret = self.sap_model.FrameObj.SetGroupAssign(frame, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Loi gan Frame '{frame}' vao group '{group_name}' (ret={ret})")

        # Gan Shells
        for shell in elements.get("shells", []):
            ret = self.sap_model.AreaObj.SetGroupAssign(shell, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Loi gan Shell '{shell}' vao group '{group_name}' (ret={ret})")

        # Gan Points
        for point in elements.get("points", []):
            ret = self.sap_model.PointObj.SetGroupAssign(point, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Loi gan Point '{point}' vao group '{group_name}' (ret={ret})")

        return results

    def inject_frame_sections(self, section_definitions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Dinh nghia hang loat tiet dien Frame moi (Rectangle, Circle, I-Section, Tube, Pipe).

        Cau truc moi item trong section_definitions:
        - Chu nhat: {'type': 'rect', 'name': 'B300x600', 'mat': 'C30', 'depth': 0.6, 'width': 0.3}
        - Tron:     {'type': 'circle', 'name': 'C500', 'mat': 'C30', 'dia': 0.5}
        - Thep I:   {'type': 'i', 'name': 'H400x200', 'mat': 'SS400', 'depth': 0.4, 'width': 0.2, 'tf': 0.013, 'tw': 0.008}
        - Thep hop: {'type': 'tube', 'name': 'BOX200', 'mat': 'SS400', 'depth': 0.2, 'width': 0.2, 'tf': 0.008, 'tw': 0.008}
        - Thep ong: {'type': 'pipe', 'name': 'PIPE219', 'mat': 'SS400', 'dia': 0.219, 'tw': 0.006}
        """
        self._ensure_unlocked("Dinh nghia tiet dien")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, sec in enumerate(section_definitions, start=1):
            sec_type = sec.get("type", "").lower()
            name = sec.get("name", "")
            mat = sec.get("mat", "")

            if not name or not mat:
                results["failed"] += 1
                results["errors"].append(f"Muc {idx}: Thieu ten tiet dien ('name') hoac vat lieu ('mat')")
                continue

            ret = -1
            try:
                if sec_type in ["rect", "rectangular", "concrete_rect"]:
                    t3 = float(sec.get("depth", sec.get("t3", 0)))
                    t2 = float(sec.get("width", sec.get("t2", 0)))
                    ret = self.sap_model.PropFrame.SetRectangle(name, mat, t3, t2)

                elif sec_type in ["circle", "circular", "concrete_circle"]:
                    t3 = float(sec.get("dia", sec.get("t3", 0)))
                    ret = self.sap_model.PropFrame.SetCircle(name, mat, t3)

                elif sec_type in ["i", "i_section", "steel_i", "wide_flange"]:
                    t3 = float(sec.get("depth", sec.get("t3", 0)))
                    t2 = float(sec.get("width", sec.get("t2", 0)))
                    tf = float(sec.get("tf", 0))
                    tw = float(sec.get("tw", 0))
                    t2b = float(sec.get("t2b", t2))
                    tfb = float(sec.get("tfb", tf))
                    ret = self.sap_model.PropFrame.SetISection(name, mat, t3, t2, tf, tw, t2b, tfb)

                elif sec_type in ["tube", "box", "steel_box"]:
                    t3 = float(sec.get("depth", sec.get("t3", 0)))
                    t2 = float(sec.get("width", sec.get("t2", 0)))
                    tf = float(sec.get("tf", 0))
                    tw = float(sec.get("tw", 0))
                    ret = self.sap_model.PropFrame.SetTube(name, mat, t3, t2, tf, tw)

                elif sec_type in ["pipe", "steel_pipe"]:
                    t3 = float(sec.get("dia", sec.get("t3", 0)))
                    tw = float(sec.get("tw", 0))
                    ret = self.sap_model.PropFrame.SetPipe(name, mat, t3, tw)

                else:
                    results["failed"] += 1
                    results["errors"].append(f"Muc {idx} ('{name}'): Loai tiet dien '{sec_type}' khong duoc ho tro")
                    continue

                if ret == 0:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Muc {idx} ('{name}'): Tao tiet dien that bai (ret={ret})")

            except Exception as ex:
                results["failed"] += 1
                results["errors"].append(f"Muc {idx} ('{name}'): Ngoai le khi tao tiet dien: {ex}")

        return results

    def inject_section_assignments(self, assignments: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Gan tiet dien cho danh sach Frame hoac Shell.
        assignments: List[{'name': '1', 'section': 'B300x600', 'type': 'frame'|'shell'}]
        """
        self._ensure_unlocked("Gan tiet dien")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, item in enumerate(assignments, start=1):
            name = item.get("name", "")
            section = item.get("section", "")
            elem_type = item.get("type", "frame").lower()

            if not name or not section:
                results["failed"] += 1
                results["errors"].append(f"Muc {idx}: Thieu 'name' hoac 'section'")
                continue

            ret = -1
            if elem_type in ["frame", "beam", "column", "brace"]:
                ret = self.sap_model.FrameObj.SetSection(name, section)
            elif elem_type in ["shell", "area", "floor", "slab", "wall"]:
                ret = self.sap_model.AreaObj.SetProperty(name, section)
            else:
                results["failed"] += 1
                results["errors"].append(f"Muc {idx}: Loai cau kien '{elem_type}' khong hop le cho gán tiet dien")
                continue

            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Muc {idx}: Gan tiet dien '{section}' cho {elem_type} '{name}' that bai (ret={ret})")

        self.refresh_view()
        return results

    def refresh_view(self):
        """
        Lam moi khung nhin 3D/Plan trong ETABS.
        """
        try:
            self.sap_model.View.RefreshView(0, False)
        except Exception:
            pass

    def execute_batch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thuc thi mot chuoi thao tac tu dong hoa (Tao nhom, Dinh nghia tiet dien, Doi ten, Gan nhom, Gan tiet dien).
        Nhan vao payload da qua chuan hoa (tu data_parser).
        """
        self._ensure_unlocked("Thuc thi batch")

        report = {
            "model_file": self.sap_model.GetModelFilename(),
            "status": "success",
            "summary": {},
            "errors": []
        }

        # 1. Tao Groups
        groups = payload.get("create_groups", [])
        if groups:
            res_grp = self.inject_group_definition(groups)
            report["summary"]["create_groups"] = res_grp
            if res_grp["errors"]:
                report["errors"].extend(res_grp["errors"])

        # 2. Dinh nghia Frame Sections
        sections = payload.get("frame_sections", [])
        if sections:
            res_sec = self.inject_frame_sections(sections)
            report["summary"]["frame_sections"] = res_sec
            if res_sec["errors"]:
                report["errors"].extend(res_sec["errors"])

        # 3. Doi ten cau kien
        renames = payload.get("rename", [])
        if renames:
            res_ren = self.inject_names(renames)
            report["summary"]["rename"] = res_ren
            if res_ren["errors"]:
                report["errors"].extend(res_ren["errors"])

        # 4. Gan cau kien vao Group
        assign_groups = payload.get("assign_groups", {})
        if assign_groups:
            grp_assign_res = {"assigned": 0, "failed": 0, "details": {}}
            for grp_name, elements in assign_groups.items():
                res_ga = self.inject_group_assignment(grp_name, elements)
                grp_assign_res["assigned"] += res_ga["assigned"]
                grp_assign_res["failed"] += res_ga["failed"]
                grp_assign_res["details"][grp_name] = res_ga
                if res_ga["errors"]:
                    report["errors"].extend(res_ga["errors"])
            report["summary"]["assign_groups"] = grp_assign_res

        # 5. Gan tiet dien
        sec_assigns = payload.get("assign_sections", [])
        if sec_assigns:
            res_sa = self.inject_section_assignments(sec_assigns)
            report["summary"]["assign_sections"] = res_sa
            if res_sa["errors"]:
                report["errors"].extend(res_sa["errors"])

        # 6. Refresh view
        self.refresh_view()

        if report["errors"]:
            report["status"] = "completed_with_errors"

        return report
