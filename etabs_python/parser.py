"""
Payload parsing: normalizes JSON strings, dicts, Excel (.xlsx) or CSV files into the
standard batch payload consumed by EtabsDataBridge.execute_batch.
"""
from typing import Union, Dict, Any, List
from pathlib import Path
import json
import logging
import pandas as pd

logger = logging.getLogger("DataParser")


def parse_json_payload(data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalize a JSON string or dict into the standard batch payload.

    Returned structure:
    {
        "create_groups": List[str],
        "rename": List[{"type": str, "old_name": str, "new_name": str}],
        "assign_groups": Dict[str, Dict[str, List[str]]],
        "frame_sections": List[dict],
        "assign_sections": List[dict]
    }
    """
    if isinstance(data, str):
        try:
            payload = json.loads(data)
        except Exception as e:
            raise ValueError(f"Cannot parse JSON string: {e}")
    elif isinstance(data, dict):
        payload = data
    else:
        raise TypeError(f"Input must be a JSON str or dict, got: {type(data)}")

    standard_payload = {
        "create_groups": [],
        "rename": [],
        "assign_groups": {},
        "frame_sections": [],
        "assign_sections": []
    }

    # 1. Groups
    raw_groups = payload.get("create_groups") or payload.get("groups") or []
    if isinstance(raw_groups, list):
        standard_payload["create_groups"] = [str(g).strip() for g in raw_groups if str(g).strip()]
    elif isinstance(raw_groups, str):
        standard_payload["create_groups"] = [raw_groups.strip()]

    # 2. Rename
    raw_rename = payload.get("rename") or payload.get("rename_records") or []
    if isinstance(raw_rename, list):
        for item in raw_rename:
            if not isinstance(item, dict):
                continue
            elem_type = item.get("type") or item.get("elem_type") or item.get("element_type", "")
            old_name = item.get("old_name") or item.get("old") or item.get("current_name", "")
            new_name = item.get("new_name") or item.get("new") or item.get("target_name", "")
            if elem_type and old_name and new_name:
                standard_payload["rename"].append({
                    "type": str(elem_type).strip().lower(),
                    "old_name": str(old_name).strip(),
                    "new_name": str(new_name).strip()
                })

    # 3. Assign Groups
    raw_assign = payload.get("assign_groups") or payload.get("group_assignments") or {}
    if isinstance(raw_assign, dict):
        # Form { "G1": { "frames": [...], "shells": [...], "points": [...] } }
        for g_name, items in raw_assign.items():
            g_str = str(g_name).strip()
            if not g_str:
                continue
            standard_payload["assign_groups"][g_str] = {
                "frames": [str(x).strip() for x in items.get("frames", [])],
                "shells": [str(x).strip() for x in items.get("shells", [])],
                "points": [str(x).strip() for x in items.get("points", [])]
            }
    elif isinstance(raw_assign, list):
        # List form: [ {"group": "G1", "type": "frame", "name": "B1"}, ... ]
        for item in raw_assign:
            if not isinstance(item, dict):
                continue
            g_name = str(item.get("group") or item.get("group_name", "")).strip()
            elem_type = str(item.get("type", "")).strip().lower()
            obj_name = str(item.get("name") or item.get("object_name", "")).strip()
            if not g_name or not elem_type or not obj_name:
                continue

            if g_name not in standard_payload["assign_groups"]:
                standard_payload["assign_groups"][g_name] = {"frames": [], "shells": [], "points": []}

            if elem_type in ["frame", "beam", "column", "brace"]:
                standard_payload["assign_groups"][g_name]["frames"].append(obj_name)
            elif elem_type in ["shell", "area", "floor", "slab", "wall"]:
                standard_payload["assign_groups"][g_name]["shells"].append(obj_name)
            elif elem_type in ["point", "joint"]:
                standard_payload["assign_groups"][g_name]["points"].append(obj_name)

    # 4. Frame Sections Definition
    raw_sections = payload.get("frame_sections") or payload.get("sections") or []
    if isinstance(raw_sections, list):
        standard_payload["frame_sections"] = [s for s in raw_sections if isinstance(s, dict)]

    # 5. Section Assignments
    raw_sec_assign = payload.get("assign_sections") or payload.get("section_assignments") or []
    if isinstance(raw_sec_assign, list):
        standard_payload["assign_sections"] = [s for s in raw_sec_assign if isinstance(s, dict)]

    return standard_payload


def parse_excel_file(file_input: Any) -> Dict[str, Any]:
    """
    Read an Excel file (.xlsx) or bytes buffer into the standard payload.
    Recognized sheets:
    - Groups: column 'Name' or 'Group'
    - Rename: columns 'Type', 'Old_Name' (or 'Old'), 'New_Name' (or 'New')
    - Assign: columns 'Group', 'Type', 'Name' (or 'ObjectName')
    - Sections: columns 'Name', 'Type', 'Mat', 'Depth', 'Width', ...
    - AssignSections: columns 'Name', 'Section', 'Type'
    """
    if isinstance(file_input, (str, Path)):
        path = Path(file_input)
        if not path.exists():
            raise FileNotFoundError(f"Excel file not found: {file_input}")
        excel_obj = pd.ExcelFile(path)
    else:
        excel_obj = pd.ExcelFile(file_input)

    sheet_names = {s.lower().strip(): s for s in excel_obj.sheet_names}

    payload: Dict[str, Any] = {
        "create_groups": [],
        "rename": [],
        "assign_groups": {},
        "frame_sections": [],
        "assign_sections": []
    }

    # 1. Sheet Groups
    for key in ["groups", "group"]:
        if key in sheet_names:
            df = excel_obj.parse(sheet_names[key])
            col = [c for c in df.columns if str(c).lower() in ["name", "group", "group_name"]]
            if col:
                payload["create_groups"] = [str(x).strip() for x in df[col[0]].dropna().tolist()]
            elif len(df.columns) > 0:
                payload["create_groups"] = [str(x).strip() for x in df.iloc[:, 0].dropna().tolist()]
            break

    # 2. Sheet Rename
    for key in ["rename", "renames", "rename_records"]:
        if key in sheet_names:
            df = excel_obj.parse(sheet_names[key])
            type_col = next((c for c in df.columns if str(c).lower() in ["type", "elem_type", "element_type"]), None)
            old_col = next((c for c in df.columns if str(c).lower() in ["old_name", "old", "current_name"]), None)
            new_col = next((c for c in df.columns if str(c).lower() in ["new_name", "new", "target_name"]), None)

            if type_col and old_col and new_col:
                for _, row in df.dropna(subset=[type_col, old_col, new_col]).iterrows():
                    payload["rename"].append({
                        "type": str(row[type_col]).strip().lower(),
                        "old_name": str(row[old_col]).strip(),
                        "new_name": str(row[new_col]).strip()
                    })
            break

    # 3. Sheet Assign
    for key in ["assign", "assign_groups", "group_assign"]:
        if key in sheet_names:
            df = excel_obj.parse(sheet_names[key])
            grp_col = next((c for c in df.columns if str(c).lower() in ["group", "group_name", "groupname"]), None)
            type_col = next((c for c in df.columns if str(c).lower() in ["type", "elem_type"]), None)
            name_col = next((c for c in df.columns if str(c).lower() in ["name", "object_name", "object"]), None)

            if grp_col and type_col and name_col:
                assign_dict: Dict[str, Dict[str, List[str]]] = {}
                for _, row in df.dropna(subset=[grp_col, type_col, name_col]).iterrows():
                    g = str(row[grp_col]).strip()
                    t = str(row[type_col]).strip().lower()
                    n = str(row[name_col]).strip()
                    if g not in assign_dict:
                        assign_dict[g] = {"frames": [], "shells": [], "points": []}
                    if t in ["frame", "beam", "column", "brace"]:
                        assign_dict[g]["frames"].append(n)
                    elif t in ["shell", "area", "floor", "slab", "wall"]:
                        assign_dict[g]["shells"].append(n)
                    elif t in ["point", "joint"]:
                        assign_dict[g]["points"].append(n)
                payload["assign_groups"] = assign_dict
            break

    # 4. Sheet Sections
    for key in ["sections", "frame_sections"]:
        if key in sheet_names:
            df = excel_obj.parse(sheet_names[key])
            payload["frame_sections"] = df.dropna(how="all").to_dict(orient="records")
            break

    # 5. Sheet AssignSections
    for key in ["assignsections", "assign_sections", "section_assign"]:
        if key in sheet_names:
            df = excel_obj.parse(sheet_names[key])
            payload["assign_sections"] = df.dropna(how="all").to_dict(orient="records")
            break

    return payload


def parse_csv_file(file_input: Any, task_type: str = "rename") -> List[Dict[str, Any]]:
    """
    Read a CSV file or buffer and return its records.
    """
    if isinstance(file_input, (str, Path)):
        path = Path(file_input)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_input}")
        df = pd.read_csv(path)
    else:
        df = pd.read_csv(file_input)

    return df.dropna(how="all").to_dict(orient="records")
