"""
Bulk rename of ETABS object unique names (frame, area, point).
Reads records from CSV/JSON, optionally checks beam/column/brace and slab/wall orientation,
and refuses to run on a locked model.
"""
import os
import csv
import json
import logging
from typing import Optional, Dict, Any, List, Tuple

from .connection import get_active_etabs, is_model_locked

logger = logging.getLogger("ObjectRename")


class ObjectRenamer:
    def __init__(self, sap_model=None, pid: Optional[int] = None):
        """
        Create a renamer. Attaches to the running ETABS instance when sap_model is not given.
        """
        if sap_model is not None:
            self.sap_model = sap_model
        else:
            self.sap_model = get_active_etabs(pid=pid)

    def check_model_locked(self) -> bool:
        """
        Return True if the ETABS model is locked.
        """
        return is_model_locked(self.sap_model)

    def rename_frame(self, old_name: str, new_name: str, expected_type: Optional[str] = None) -> Tuple[int, str]:
        """
        Rename a frame object (beam, column, brace).
        expected_type: 'beam', 'column', 'brace' or None (no check).
        Returns (ret_code, message); 0 = success.
        """
        if expected_type:
            expected_type = expected_type.lower()
            orient_ret, orientation = self.sap_model.FrameObj.GetDesignOrientation(old_name)
            if orient_ret != 0:
                return orient_ret, f"Frame '{old_name}' not found for orientation check."

            # Orientation codes: 1 = Column, 2 = Beam, 3 = Brace
            if expected_type == "beam" and orientation != 2:
                return -1, f"Frame '{old_name}' has orientation={orientation}, not Beam (expected 2)."
            if expected_type == "column" and orientation != 1:
                return -1, f"Frame '{old_name}' has orientation={orientation}, not Column (expected 1)."
            if expected_type == "brace" and orientation != 3:
                return -1, f"Frame '{old_name}' has orientation={orientation}, not Brace (expected 3)."

        ret = self.sap_model.FrameObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Frame renamed: '{old_name}' -> '{new_name}'"
        return ret, f"Frame rename failed (ret={ret}): '{old_name}' -> '{new_name}'"

    def rename_area(self, old_name: str, new_name: str, expected_type: Optional[str] = None) -> Tuple[int, str]:
        """
        Rename an area object (slab, wall).
        expected_type: 'floor', 'slab', 'wall' or None.
        Returns (ret_code, message); 0 = success.
        """
        if expected_type:
            expected_type = expected_type.lower()
            orient_ret, orientation = self.sap_model.AreaObj.GetDesignOrientation(old_name)
            if orient_ret != 0:
                return orient_ret, f"Area '{old_name}' not found for orientation check."

            # Orientation codes: 1 = Floor/Slab, 2 = Wall
            if expected_type in ["floor", "slab"] and orientation != 1:
                return -1, f"Area '{old_name}' has orientation={orientation}, not Floor/Slab (expected 1)."
            if expected_type == "wall" and orientation != 2:
                return -1, f"Area '{old_name}' has orientation={orientation}, not Wall (expected 2)."

        ret = self.sap_model.AreaObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Area renamed: '{old_name}' -> '{new_name}'"
        return ret, f"Area rename failed (ret={ret}): '{old_name}' -> '{new_name}'"

    def rename_point(self, old_name: str, new_name: str) -> Tuple[int, str]:
        """
        Rename a point object.
        Returns (ret_code, message); 0 = success.
        """
        ret = self.sap_model.PointObj.ChangeName(old_name, new_name)
        if ret == 0:
            return 0, f"Point renamed: '{old_name}' -> '{new_name}'"
        return ret, f"Point rename failed (ret={ret}): '{old_name}' -> '{new_name}'"

    @staticmethod
    def load_records(file_path: str) -> List[Dict[str, str]]:
        """
        Read and normalize records from a CSV or JSON file.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Data file not found: {file_path}")

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
                    raise ValueError("JSON must be a list of objects.")
                for item in data:
                    normalized = {}
                    for k, v in item.items():
                        clean_key = str(k).strip().lower().replace(" ", "_")
                        normalized[clean_key] = str(v).strip() if v is not None else ""
                    records.append(normalized)
        else:
            raise ValueError(f"Unsupported file type: {ext}. Use .csv or .json")

        return records

    def rename_from_file(self, file_path: str) -> Dict[str, Any]:
        """
        Rename objects in bulk from a CSV/JSON file. Stops if the model is locked.
        """
        if self.check_model_locked():
            raise RuntimeError("The ETABS model is locked. Unlock it before renaming.")

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
                msg = (f"Row {idx}: missing required field (type='{elem_type}', "
                       f"old_name='{old_name}', new_name='{new_name}')")
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
                    code, msg = -1, f"Unsupported object type: '{elem_type}'"

                if code == 0:
                    logger.info(f"Row {idx}: {msg}")
                    report["success"] += 1
                    report["details"].append({"row": idx, "status": "success", "message": msg})
                else:
                    logger.error(f"Row {idx}: {msg}")
                    report["failed"] += 1
                    report["details"].append({"row": idx, "status": "failed", "message": msg})

            except Exception as ex:
                msg = f"Row {idx}: exception while processing '{old_name}': {ex}"
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
    Convenience wrapper: rename objects from a file.
    """
    renamer = ObjectRenamer(sap_model=sap_model)
    return renamer.rename_from_file(file_path)
