"""
Operations that need a running ETABS (needs="live"): object selection, frame details, model writes.
"""
from typing import Any, Dict, List, Optional

import pandas as pd

from .engine import Context, operation, to_list
from .parser import parse_json_payload
from .sources import N_MM
from .units import convert_value

KIND_ALIASES = {
    "frame": "frame", "beam": "frame", "column": "frame", "brace": "frame",
    "area": "area", "shell": "area", "wall": "area", "slab": "area", "floor": "area",
    "point": "point", "joint": "point",
}
SELECT_TYPES = {1: "Point", 2: "Frame", 3: "Cable", 4: "Tendon", 5: "Area", 6: "Solid", 7: "Link"}
FRAME_TYPES = {1: "Column", 2: "Beam", 3: "Brace", 4: "Null", 5: "Other"}


def _object_api(ctx: Context, kind: str):
    resolved = KIND_ALIASES.get(str(kind).lower())
    if resolved is None:
        raise ValueError(f"Invalid kind '{kind}'. Use one of {sorted(KIND_ALIASES)}")
    sap = ctx.source.sap_model
    return {"frame": sap.FrameObj, "area": sap.AreaObj, "point": sap.PointObj}[resolved]


@operation("name_from_label", needs="live")
def name_from_label(ctx: Context, label: str, story: str, kind: str = "frame") -> str:
    """UniqueName of an object from its label and story. kind: frame/beam/column/brace, area/wall/slab, point."""
    name, ret = _object_api(ctx, kind).GetNameFromLabel(str(label), str(story))
    if ret != 0 or not name:
        raise KeyError(f"No {kind} with label '{label}' on story '{story}'")
    return name


@operation("select", needs="live")
def select(ctx: Context, names=None, kind: str = "frame", labels=None, story=None, clear: bool = True) -> List[str]:
    """
    Select objects in the ETABS UI by UniqueName + kind, or by labels + story (story: one name or a list
    matching labels). clear=True clears the previous selection. Returns the selected UniqueNames.
    """
    api = _object_api(ctx, kind)
    selected = [str(n) for n in to_list(names)]
    label_list = to_list(labels)
    if label_list:
        stories = to_list(story)
        if len(stories) == 1:
            stories = stories * len(label_list)
        if len(stories) != len(label_list):
            raise ValueError("story must be one story name or a list with the same length as labels")
        selected += [name_from_label(ctx, lab, st, kind) for lab, st in zip(label_list, stories)]
    if not selected:
        raise ValueError("Pass names, or labels with story")
    if clear:
        clear_selection(ctx)
    for name in selected:
        if api.SetSelected(name, True) != 0:
            raise KeyError(f"Cannot select {kind} '{name}'")
    ctx.source.sap_model.View.RefreshView(0, False)
    return selected


@operation("selected", needs="live")
def selected(ctx: Context) -> pd.DataFrame:
    """Currently selected objects: Type (Point/Frame/Area/...), UniqueName."""
    count, types, names, _ = ctx.source.sap_model.SelectObj.GetSelected()
    df = pd.DataFrame({"Type": [SELECT_TYPES.get(t, str(t)) for t in list(types or [])[:count]],
                       "UniqueName": [str(n) for n in list(names or [])[:count]]})
    df.attrs = {"table": "selected", "units": {}}
    return df


@operation("clear_selection", needs="live")
def clear_selection(ctx: Context) -> None:
    """Clear the selection in the ETABS UI."""
    ctx.source.sap_model.SelectObj.ClearSelection()


@operation("frame_info", needs="live")
def frame_info(ctx: Context, name: str) -> Dict[str, Any]:
    """Frame details: label, story, section, type, end points, coordinates and length (general length unit)."""
    sap = ctx.source.sap_model
    frame, point = sap.FrameObj, sap.PointObj
    name = str(name)
    label, story, ret = frame.GetLabelFromName(name)
    if ret != 0:
        raise KeyError(f"Frame '{name}' not found")
    with ctx.source.present_units(N_MM):
        pi, pj, _ = frame.GetPoints(name)
        ci = [convert_value(v, "mm", ctx.units) for v in point.GetCoordCartesian(pi)[:3]]
        cj = [convert_value(v, "mm", ctx.units) for v in point.GetCoordCartesian(pj)[:3]]
    return {
        "name": name, "label": label, "story": story,
        "section": frame.GetSection(name)[0],
        "type": FRAME_TYPES.get(frame.GetDesignOrientation(name)[0], "Other"),
        "point_i": pi, "point_j": pj, "coord_i": ci, "coord_j": cj,
        "length": sum((a - b) ** 2 for a, b in zip(ci, cj)) ** 0.5,
        "length_unit": ctx.units.length,
    }


@operation("execute_batch", needs="live", slow=True)
def execute_batch(ctx: Context, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Write to the model: create_groups, frame_sections, rename, assign_groups, assign_sections
    (see parser.parse_json_payload). Raises ModelLockedError when the model is locked.
    """
    return ctx.source.bridge.execute_batch(parse_json_payload(payload or {}))
