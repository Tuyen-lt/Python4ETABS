"""
Module Object_rename.py
Chuyen trach doi Unique Name cua cau kien (frame, shell, point) trong ETABS qua Python comtypes.
Ho tro doc du lieu tu CSV/JSON, phan tach dam/cot, san/tuong, kiem tra khoa mo hinh.
"""
import os
import csv
import json
import logging
from typing import Optional, Dict, Any, List, Tuple
import comtypes.client

from connection import get_active_etabs, is_model_locked, ensure_unlocked, ModelLockedError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ObjectRename")


class ObjectRenamer:
    def __init__(self, sap_model=None, pid: Optional[int] = None):
        """
        Khoi tao bo doi ten cau kien.
        Neu sap_model khong duoc cung cap, tu dong ket noi toi instance ETABS dang mo san.
        """
        if sap_model is not None:
            self.sap_model = sap_model
        else:
            self.sap_model = get_active_etabs(pid=pid)

    def check_model_locked(self) -> bool:
        """
        Kiem tra trang thai khoa cua mo hinh ETABS.
        """
        return is_model_locked(self.sap_model)

    def rename_frame(self, old_name: str, new_name: str, expected_type: Optional[str] = None) -> Tuple[int, str]:
        """
        Doi Unique Name cua phan tu Frame (dam, cot, giang).
        expected_type: 'beam', 'column', 'brace', hoac None (khong kiem tra).
        Tra ve (ret_code, message): 0 = thanh cong.
        """
        if expected_type:
            expected_type = expected_type.lower()
            orient_ret, orientation = self.sap_model.FrameObj.GetDesignOrientation(old_name)
            if orient_ret != 0:
                return orient_ret, f"Khong tim thay Frame '{old_name}' de kiem tra orientation."

            # Orientation codes: 1 = Column, 2 = Beam, 3 = Brace
            if expected_type == "beam" and orientation != 2:
                return -1, f"Frame '{old_name}' co orientation={orientation}, khong phai Beam (yeu cau 2)."
            if expected_type == "column" and orientation != 1:
                return -1, f"Frame '{old_name}' co orientation={orientation}, khong phai Column (yeu cau 1)."
            if expected_type == "brace" and orientation != 3:
                return -1, f"Frame '{old_name}' co orientation={orientation}, khong phai Brace (yeu cau 3)."

        ret = self.sap_model.FrameObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Doi ten Frame thanh cong: '{old_name}' -> '{new_name}'"
        return ret, f"Loi doi ten Frame (ret={ret}): '{old_name}' -> '{new_name}'"

    def rename_area(self, old_name: str, new_name: str, expected_type: Optional[str] = None) -> Tuple[int, str]:
        """
        Doi Unique Name cua phan tu Area/Shell (san, tuong).
        expected_type: 'floor', 'slab', 'wall', hoac None.
        Tra ve (ret_code, message): 0 = thanh cong.
        """
        if expected_type:
            expected_type = expected_type.lower()
            orient_ret, orientation = self.sap_model.AreaObj.GetDesignOrientation(old_name)
            if orient_ret != 0:
                return orient_ret, f"Khong tim thay Area '{old_name}' de kiem tra orientation."

            # Orientation codes: 1 = Floor/Slab, 2 = Wall
            if expected_type in ["floor", "slab"] and orientation != 1:
                return -1, f"Area '{old_name}' co orientation={orientation}, khong phai Floor/Slab (yeu cau 1)."
            if expected_type == "wall" and orientation != 2:
                return -1, f"Area '{old_name}' co orientation={orientation}, khong phai Wall (yeu cau 2)."

        ret = self.sap_model.AreaObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Doi ten Area thanh cong: '{old_name}' -> '{new_name}'"
        return ret, f"Loi doi ten Area (ret={ret}): '{old_name}' -> '{new_name}'"

    def rename_point(self, old_name: str, new_name: str) -> Tuple[int, str]:
        """
        Doi Unique Name cua diem (Point/Joint).
        Tra ve (ret_code, message): 0 = thanh cong.
        """
        ret = self.sap_model.PointObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Doi ten Point thanh cong: '{old_name}' -> '{new_name}'"
        return ret, f"Loi doi ten Point (ret={ret}): '{old_name}' -> '{new_name}'"

    @staticmethod
    def load_records(file_path: str) -> List[Dict[str, str]]:
        """
        Doc va chuan hoa cac ban ghi tu file CSV hoac JSON.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Tep du lieu khong ton tai: {file_path}")

        _, ext = os.path.splitext(file_path.lower())
        records = []

        if ext == ".csv":
            with open(file_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    normalized = {}
                    for k, v in row.items():
                        if k is not None:
                            clean_key = k.strip().lower().replace(" ", "_")
                            normalized[clean_key] = v.strip() if v else ""
                    records.append(normalized)

        elif ext == ".json":
            with open(file_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("JSON phai la danh sach doi tuong (list of dicts).")
                for item in data:
                    normalized = {}
                    for k, v in item.items():
                        clean_key = str(k).strip().lower().replace(" ", "_")
                        normalized[clean_key] = str(v).strip() if v is not None else ""
                    records.append(normalized)
        else:
            raise ValueError(f"Dinh dang tep khong ho tro: {ext}. Chi ho tro .csv hoac .json")

        return records

    def rename_from_file(self, file_path: str) -> Dict[str, Any]:
        """
        Doc file CSV/JSON va thuc hien doi Unique Name hang loat.
        Dung tien trinh va bao loi neu mo hinh bi khoa.
        """
        if self.check_model_locked():
            raise RuntimeError("Mo hinh ETABS dang bi KHOA (Locked). DUNG tien trinh va bao loi theo yeu cau.")

        records = self.load_records(file_path)
        report = {
            "total": len(records),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": []
        }

        for idx, row in enumerate(records, start=1):
            elem_type = row.get("type", "").lower()
            old_name = row.get("old_name", "")
            new_name = row.get("new_name", "")

            if not elem_type or not old_name or not new_name:
                msg = f"Dong {idx}: Thieu thong tin truong bat buoc (type='{elem_type}', old_name='{old_name}', new_name='{new_name}')"
                logger.warning(msg)
                report["skipped"] += 1
                report["details"].append({"row": idx, "status": "skipped", "message": msg})
                continue

            try:
                if elem_type in ["frame", "beam", "column", "brace"]:
                    check_type = elem_type if elem_type in ["beam", "column", "brace"] else None
                    code, msg = self.rename_frame(old_name, new_name, expected_type=check_type)

                elif elem_type in ["shell", "area", "floor", "slab", "wall"]:
                    check_type = elem_type if elem_type in ["floor", "slab", "wall"] else None
                    code, msg = self.rename_area(old_name, new_name, expected_type=check_type)

                elif elem_type in ["point", "joint"]:
                    code, msg = self.rename_point(old_name, new_name)

                else:
                    code, msg = -1, f"Loai cau kien khong ho tro: '{elem_type}'"

                if code == 0:
                    logger.info(f"Dong {idx}: {msg}")
                    report["success"] += 1
                    report["details"].append({"row": idx, "status": "success", "message": msg})
                else:
                    logger.error(f"Dong {idx}: {msg}")
                    report["failed"] += 1
                    report["details"].append({"row": idx, "status": "failed", "message": msg})

            except Exception as ex:
                msg = f"Dong {idx}: Ngoai le khi xu ly '{old_name}': {ex}"
                logger.exception(msg)
                report["failed"] += 1
                report["details"].append({"row": idx, "status": "failed", "message": msg})

        try:
            self.sap_model.View.RefreshView(0, False)
        except Exception:
            pass

        return report


def rename_elements_from_file(file_path: str, sap_model=None) -> Dict[str, Any]:
    """
    Ham tien ich goi nhanh doi ten tu tep.
    """
    renamer = ObjectRenamer(sap_model=sap_model)
    return renamer.rename_from_file(file_path)
