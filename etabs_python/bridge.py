"""
EtabsDataBridge: in-memory pull and inject of ETABS data.

1. PULL: DatabaseTables -> pandas DataFrame.
   - pull_table: read any table.
   - pull_frames / pull_shells / pull_points: frame, area and point tables.

2. INJECT: direct OAPI calls.
   - inject_names: rename frames, areas, points.
   - inject_group_definition: define groups (GroupDef.SetGroup).
   - inject_group_assignment: assign objects to a group (SetGroupAssign).
   - inject_frame_sections: define frame sections (Rectangular, Circle, I, Tube, Pipe).
   - inject_section_assignments: assign sections to frames and areas.
"""
from typing import Optional, Dict, Any, List, Union
import logging
import pandas as pd
import comtypes.client

from .connection import get_active_etabs, is_model_locked, ensure_unlocked

logger = logging.getLogger("EtabsDataBridge")


class EtabsDataBridge:
    def __init__(self, sap_model=None, pid: Optional[int] = None):
        """
        Create the bridge. Attaches to the running ETABS instance when sap_model is None.
        """
        if sap_model is not None:
            self.sap_model = sap_model
        else:
            self.sap_model = get_active_etabs(pid=pid)

    def is_locked(self) -> bool:
        """
        Return True if the model is locked.
        """
        return is_model_locked(self.sap_model)

    def _ensure_unlocked(self, operation_name: str = "Operation"):
        """
        Raise ModelLockedError if the model is locked before writing.
        """
        ensure_unlocked(self.sap_model, operation_name)

    # =========================================================================
    # PULL: read data into pandas DataFrames
    # =========================================================================

    def pull_table(self, table_key: str) -> pd.DataFrame:
        """
        Read an ETABS database table into a DataFrame.
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
        Return the names of the database tables available in the model.
        """
        ret = self.sap_model.DatabaseTables.GetAvailableTables(0, [])
        return list(ret[1]) if ret[1] else []

    def pull_frames(self) -> pd.DataFrame:
        """
        Pull frame data, trying table names used by different ETABS versions.
        """
        for tbl in ["Frame Assignments - Section Properties", "Frame Assignments - Summary", "Frame Section Assignments"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    def pull_shells(self) -> pd.DataFrame:
        """
        Pull area (shell) data.
        """
        for tbl in ["Area Assignments - Section Properties", "Area Assignments - Summary", "Area Section Assignments"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    def pull_points(self) -> pd.DataFrame:
        """
        Pull point coordinates (UniqueName, Story, X, Y, Z).
        """
        for tbl in ["Point Object Connectivity", "Joint Coordinates", "Objects and Elements - Joints"]:
            df = self.pull_table(tbl)
            if not df.empty:
                return df
        return pd.DataFrame()

    # =========================================================================
    # INJECT: write data through OAPI
    # =========================================================================

    def inject_names(self, rename_records: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Rename frames, areas and points.
        rename_records: List[{'type': 'frame'|'shell'|'point', 'old_name': '...', 'new_name': '...'}]
        """
        self._ensure_unlocked("Rename objects")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, row in enumerate(rename_records, start=1):
            elem_type = row.get("type", "").lower()
            old_name = row.get("old_name", "")
            new_name = row.get("new_name", "")

            if not elem_type or not old_name or not new_name:
                results["failed"] += 1
                results["errors"].append(f"Row {idx}: missing type, old_name or new_name")
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
                results["errors"].append(f"Row {idx}: invalid object type '{elem_type}'")
                continue

            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Row {idx}: rename failed (ret={ret}) for {elem_type} '{old_name}' -> '{new_name}'")

        self.refresh_view()
        return results

    def inject_group_definition(self, group_names: Union[str, List[str]]) -> Dict[str, Any]:
        """
        Define one or more groups.
        """
        self._ensure_unlocked("Define groups")
        if isinstance(group_names, str):
            group_names = [group_names]

        results = {"success": 0, "failed": 0, "errors": []}
        for name in group_names:
            ret = self.sap_model.GroupDef.SetGroup(name)
            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Cannot create group '{name}' (ret={ret})")

        return results

    def inject_group_assignment(self, group_name: str, elements: Dict[str, List[str]], remove: bool = False) -> Dict[str, Any]:
        """
        Assign objects to a group.
        elements: {'frames': [...], 'shells': [...], 'points': [...]}
        """
        self._ensure_unlocked("Assign objects to group")
        # Make sure the group exists
        self.sap_model.GroupDef.SetGroup(group_name)

        results = {"assigned": 0, "failed": 0, "errors": []}

        # Frames
        for frame in elements.get("frames", []):
            ret = self.sap_model.FrameObj.SetGroupAssign(frame, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Cannot assign frame '{frame}' to group '{group_name}' (ret={ret})")

        # Shells
        for shell in elements.get("shells", []):
            ret = self.sap_model.AreaObj.SetGroupAssign(shell, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Cannot assign shell '{shell}' to group '{group_name}' (ret={ret})")

        # Points
        for point in elements.get("points", []):
            ret = self.sap_model.PointObj.SetGroupAssign(point, group_name, remove, 0)
            if ret == 0:
                results["assigned"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Cannot assign point '{point}' to group '{group_name}' (ret={ret})")

        return results

    def inject_frame_sections(self, section_definitions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Define frame sections in bulk (Rectangle, Circle, I-Section, Tube, Pipe).

        Item format:
        - Rectangle: {'type': 'rect', 'name': 'B300x600', 'mat': 'C30', 'depth': 0.6, 'width': 0.3}
        - Circle:   {'type': 'circle', 'name': 'C500', 'mat': 'C30', 'dia': 0.5}
        - Steel I:  {'type': 'i', 'name': 'H400x200', 'mat': 'SS400', 'depth': 0.4, 'width': 0.2, 'tf': 0.013, 'tw': 0.008}
        - Tube:     {'type': 'tube', 'name': 'BOX200', 'mat': 'SS400', 'depth': 0.2, 'width': 0.2, 'tf': 0.008, 'tw': 0.008}
        - Pipe:     {'type': 'pipe', 'name': 'PIPE219', 'mat': 'SS400', 'dia': 0.219, 'tw': 0.006}
        """
        self._ensure_unlocked("Define sections")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, sec in enumerate(section_definitions, start=1):
            sec_type = sec.get("type", "").lower()
            name = sec.get("name", "")
            mat = sec.get("mat", "")

            if not name or not mat:
                results["failed"] += 1
                results["errors"].append(f"Item {idx}: missing section name ('name') or material ('mat')")
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
                    results["errors"].append(f"Item {idx} ('{name}'): unsupported section type '{sec_type}'")
                    continue

                if ret == 0:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Item {idx} ('{name}'): section definition failed (ret={ret})")

            except Exception as ex:
                results["failed"] += 1
                results["errors"].append(f"Item {idx} ('{name}'): exception while defining section: {ex}")

        return results

    def inject_section_assignments(self, assignments: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Assign sections to frames or areas.
        assignments: List[{'name': '1', 'section': 'B300x600', 'type': 'frame'|'shell'}]
        """
        self._ensure_unlocked("Assign sections")
        results = {"success": 0, "failed": 0, "errors": []}

        for idx, item in enumerate(assignments, start=1):
            name = item.get("name", "")
            section = item.get("section", "")
            elem_type = item.get("type", "frame").lower()

            if not name or not section:
                results["failed"] += 1
                results["errors"].append(f"Item {idx}: missing 'name' or 'section'")
                continue

            ret = -1
            if elem_type in ["frame", "beam", "column", "brace"]:
                ret = self.sap_model.FrameObj.SetSection(name, section)
            elif elem_type in ["shell", "area", "floor", "slab", "wall"]:
                ret = self.sap_model.AreaObj.SetProperty(name, section)
            else:
                results["failed"] += 1
                results["errors"].append(f"Item {idx}: invalid object type '{elem_type}' for section assignment")
                continue

            if ret == 0:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Item {idx}: assigning section '{section}' to {elem_type} '{name}' failed (ret={ret})")

        self.refresh_view()
        return results

    def refresh_view(self):
        """
        Refresh the ETABS views.
        """
        try:
            self.sap_model.View.RefreshView(0, False)
        except Exception:
            pass

    def execute_batch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run a batch: create groups, define sections, rename, assign groups, assign sections.
        Expects a payload normalized by parser.parse_json_payload.
        """
        self._ensure_unlocked("Execute batch")

        report = {
            "model_file": self.sap_model.GetModelFilename(),
            "status": "success",
            "summary": {},
            "errors": []
        }

        # 1. Create groups
        groups = payload.get("create_groups", [])
        if groups:
            res_grp = self.inject_group_definition(groups)
            report["summary"]["create_groups"] = res_grp
            if res_grp["errors"]:
                report["errors"].extend(res_grp["errors"])

        # 2. Define frame sections
        sections = payload.get("frame_sections", [])
        if sections:
            res_sec = self.inject_frame_sections(sections)
            report["summary"]["frame_sections"] = res_sec
            if res_sec["errors"]:
                report["errors"].extend(res_sec["errors"])

        # 3. Rename objects
        renames = payload.get("rename", [])
        if renames:
            res_ren = self.inject_names(renames)
            report["summary"]["rename"] = res_ren
            if res_ren["errors"]:
                report["errors"].extend(res_ren["errors"])

        # 4. Assign objects to groups
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

        # 5. Assign sections
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
