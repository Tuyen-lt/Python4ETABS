"""
Model operations: stories, element lists, materials and element properties.
All work on every source (needs="tables").
"""
from bisect import bisect_left
from numbers import Real
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .engine import Context, operation

FRAME_MODS = ["AMod", "A2Mod", "A3Mod", "JMod", "I2Mod", "I3Mod", "MMod", "WMod"]
AREA_MODS = ["f11Mod", "f22Mod", "f12Mod", "m11Mod", "m22Mod", "m12Mod", "v13Mod", "v23Mod", "MMod", "WMod"]


def _finish(df: pd.DataFrame, table: str, *parts: pd.DataFrame) -> pd.DataFrame:
    """Set attrs: table name and the union of the parts' units restricted to df's columns."""
    units = {}
    for part in parts:
        units.update(part.attrs.get("units", {}))
    df.attrs = {"table": table, "units": {c: u for c, u in units.items() if c in df.columns}}
    return df


def _by_story(df: pd.DataFrame, story: Optional[str]) -> pd.DataFrame:
    if not story:
        return df
    return _finish(df[df["Story"] == story].reset_index(drop=True), df.attrs.get("table", ""), df)


def _merge(left: pd.DataFrame, right: pd.DataFrame, on: str) -> pd.DataFrame:
    """Left merge dropping right columns that already exist in left."""
    if right.empty or on not in right.columns:
        return left
    right = right[[on] + [c for c in right.columns if c not in left.columns]].drop_duplicates(on)
    return left.merge(right, on=on, how="left")


# ------------------------------------------------------------------ stories
def story_from_elevation(z: float, elevations: List[float], names: List[str], base: float,
                         tol: float = 1e-3, base_name: str = "Base") -> Optional[str]:
    """
    Story containing elevation z. elevations/names sorted bottom to top (top-of-story elevations).
    Story X contains (elevation of story below, elevation of X]. Base elevation -> base_name.
    """
    if abs(z - base) <= tol:
        return base_name
    if z < base or z > elevations[-1] + tol:
        return None
    return names[bisect_left(elevations, z - tol)]


@operation("stories")
def stories(ctx: Context) -> pd.DataFrame:
    """
    Stories from bottom to top: Story, Height, Elevation (top of story, from the base elevation).
    attrs: base_name, base_elevation. Tables: Story Definitions, Tower and Base Story Definitions.
    """
    sd = ctx.table("Story Definitions")
    bs = ctx.table("Tower and Base Story Definitions")
    if "Tower" in sd.columns and sd["Tower"].notna().any():
        tower = sd["Tower"].dropna().iloc[0]  # ponytail: first tower only; add a tower param for multi-tower models
        sd = sd[sd["Tower"] == tower]
        if "Tower" in bs.columns:
            bs = bs[bs["Tower"] == tower]
    base_name = str(bs["BSName"].iloc[0]) if "BSName" in bs.columns and len(bs) else "Base"
    base_elevation = float(bs["BSElev"].iloc[0]) if "BSElev" in bs.columns and len(bs) else 0.0
    df = sd[["Story", "Height"]].iloc[::-1].reset_index(drop=True)
    df["Elevation"] = base_elevation + df["Height"].cumsum()
    unit = sd.attrs["units"].get("Height", "")
    df.attrs = {"table": "Story Definitions", "units": {"Height": unit, "Elevation": unit},
                "base_name": base_name, "base_elevation": base_elevation}
    return df


@operation("story_at")
def story_at(ctx: Context, z, tol: float = 1e-3):
    """
    Story name for an elevation (or a list of elevations) in the general length unit.
    Returns the base story name at the base elevation and None outside the model.
    """
    st = stories(ctx)
    args = (st["Elevation"].tolist(), st["Story"].tolist(), st.attrs["base_elevation"], tol, st.attrs["base_name"])
    if isinstance(z, Real):
        return story_from_elevation(float(z), *args)
    return [story_from_elevation(float(v), *args) for v in z]


# ----------------------------------------------------------------- elements
@operation("frames")
def frames(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """All frame objects (Frame Assignments - Summary): Story, Label, UniqueName, Type, Length, AnalysisSect..."""
    return _by_story(ctx.table("Frame Assignments - Summary"), story)


def _by_type(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    return _finish(df[df[column] == value].reset_index(drop=True), df.attrs.get("table", ""), df)


@operation("beams")
def beams(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """Frame objects with Type == Beam."""
    return _by_type(frames(ctx, story), "Type", "Beam")


@operation("columns")
def columns(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """Frame objects with Type == Column."""
    return _by_type(frames(ctx, story), "Type", "Column")


@operation("shells")
def shells(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """All area objects (Area Assignments - Summary): Story, Label, UniqueName, SectProp, PropType..."""
    return _by_story(ctx.table("Area Assignments - Summary"), story)


@operation("walls")
def walls(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """Area objects with PropType == Wall."""
    return _by_type(shells(ctx, story), "PropType", "Wall")


@operation("slabs")
def slabs(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """Area objects with PropType == Slab."""
    return _by_type(shells(ctx, story), "PropType", "Slab")


@operation("points")
def points(ctx: Context, story: Optional[str] = None) -> pd.DataFrame:
    """All point objects (Point Object Connectivity): UniqueName, Story, X, Y, Z..."""
    return _by_story(ctx.table("Point Object Connectivity"), story)


# ---------------------------------------------------------------- properties
@operation("materials")
def materials(ctx: Context) -> pd.DataFrame:
    """
    One row per material: MatType, Grade, UnitWeight, E1, G12, U12, A1, Fc (concrete), Fy/Fu (steel, rebar, tendon).
    """
    general = ctx.table("Material Properties - General")
    df = general[[c for c in ("Material", "Type", "Grade") if c in general.columns]].rename(columns={"Type": "MatType"})
    parts = [general]
    mech = ctx.optional_table("Material Properties - Basic Mechanical Properties")
    df = _merge(df, mech, "Material")
    concrete = ctx.optional_table("Material Properties - Concrete Data")
    if "Fc" in concrete.columns:
        df = _merge(df, concrete[["Material", "Fc"]], "Material")
    steel_parts = [t for t in (ctx.optional_table("Material Properties - Steel Data"),
                               ctx.optional_table("Material Properties - Rebar Data"),
                               ctx.optional_table("Material Properties - Tendon Data")) if "Fy" in t.columns]
    if steel_parts:
        steel = pd.concat([t[["Material", "Fy", "Fu"]] for t in steel_parts], ignore_index=True)
        df = _merge(df, steel, "Material")
    return _finish(df, "materials", *(parts + [mech, concrete] + steel_parts))


def _assignments(ctx: Context, prefix: str, skip: Iterable[str] = ()) -> List[pd.DataFrame]:
    """Summary table merged with every '<prefix>*' table that has one row per object."""
    base = ctx.table(prefix + "Summary")
    parts = [base]
    for name in ctx.source.tables():
        if not name.startswith(prefix) or name == prefix + "Summary" or name in skip:
            continue
        table = ctx.table(name)
        if table.empty or "UniqueName" not in table.columns or table["UniqueName"].duplicated().any():
            continue  # ponytail: multi-row tables (loads...) are skipped; read them with engine.table()
        base = _merge(base, table, "UniqueName")
        parts.append(table)
    return [base] + parts


def apply_modifiers(df: pd.DataFrame, obj_mods: pd.DataFrame, mod_cols: List[str]) -> pd.DataFrame:
    """
    Obj_<m>: object modifier (1 when the object is not in the modifier table).
    Sect_<m>: section modifier (1 when missing). <m> = Sect_<m> x Obj_<m>.
    """
    present = [m for m in mod_cols if m in obj_mods.columns] if "UniqueName" in obj_mods.columns else []
    if present:
        renamed = obj_mods[["UniqueName"] + present].rename(columns={m: f"Obj_{m}" for m in present})
        df = df.merge(renamed.drop_duplicates("UniqueName"), on="UniqueName", how="left")
    for m in mod_cols:
        obj = pd.to_numeric(df[f"Obj_{m}"], errors="coerce").fillna(1.0) if f"Obj_{m}" in df.columns else 1.0
        sect = pd.to_numeric(df[f"Sect_{m}"], errors="coerce").fillna(1.0) if f"Sect_{m}" in df.columns else 1.0
        df[f"Obj_{m}"] = obj
        df[f"Sect_{m}"] = sect
        df[m] = df[f"Sect_{m}"] * df[f"Obj_{m}"]
    return df


@operation("frame_properties")
def frame_properties(ctx: Context) -> pd.DataFrame:
    """
    One row per frame: all one-row-per-object Frame Assignments tables, section properties (Material, Shape,
    Area, I33, I22, J, t3, t2...), modifiers Sect_* x Obj_* = AMod, I3Mod..., and material data.
    """
    modifiers_table = "Frame Assignments - Property Modifiers"
    assigned = _assignments(ctx, "Frame Assignments - ", skip=[modifiers_table])
    df, parts = assigned[0], assigned[1:]
    obj_mods = ctx.optional_table(modifiers_table)

    summary_name = "Frame Section Property Definitions - Summary"
    sect = ctx.table(summary_name)
    parts.append(sect)
    dims = []
    for name in ctx.source.tables():
        if name.startswith("Frame Section Property Definitions - ") and name != summary_name:
            table = ctx.table(name)
            if "Name" in table.columns and "t3" in table.columns:
                dims.append(table[["Name"] + [c for c in ("t3", "t2", "tf", "tw") if c in table.columns]])
                parts.append(table)
    if dims:
        sect = _merge(sect, pd.concat(dims, ignore_index=True), "Name")
    sect = sect.drop(columns=[c for c in ("Color", "GUID", "Notes") if c in sect.columns])
    renames = {m: f"Sect_{m}" for m in FRAME_MODS}
    renames["Name"] = "AnalysisSect"
    df = _merge(df, sect.rename(columns=renames), "AnalysisSect")

    df = apply_modifiers(df, obj_mods, FRAME_MODS)
    mats = materials(ctx)
    if "Material" in df.columns:
        df = _merge(df, mats, "Material")
    return _finish(df, "frame_properties", *(parts + [mats]))


@operation("area_properties")
def area_properties(ctx: Context) -> pd.DataFrame:
    """
    One row per area: all one-row-per-object Area Assignments tables, section (SectType, AnalType, Material,
    TotalThick), modifiers Sect_* x Obj_* = f11Mod, m11Mod..., and material data.
    """
    modifiers_table = "Area Assignments - Stiffness Modifiers"
    assigned = _assignments(ctx, "Area Assignments - ", skip=[modifiers_table])
    df, parts = assigned[0], assigned[1:]
    obj_mods = ctx.optional_table(modifiers_table)

    sect = ctx.table("Area Section Property Definitions - Summary").rename(columns={"Name": "SectProp", "Type": "SectType"})
    parts.append(sect)
    props = [t for t in (ctx.optional_table(name) for name in
                         ("Slab Property Definitions", "Wall Property Definitions - Specified",
                          "Deck Property Definitions")) if "Name" in t.columns]
    if props:
        renames = {m: f"Sect_{m}" for m in AREA_MODS}
        renames["Name"] = "SectProp"
        mods = pd.concat([t[["Name"] + [m for m in AREA_MODS if m in t.columns]] for t in props], ignore_index=True)
        sect = _merge(sect, mods.rename(columns=renames), "SectProp")
    df = _merge(df, sect, "SectProp")

    df = apply_modifiers(df, obj_mods, AREA_MODS)
    mats = materials(ctx)
    if "Material" in df.columns:
        df = _merge(df, mats, "Material")
    return _finish(df, "area_properties", *(parts + [mats]))


@operation("loadset_plan_data", needs="tables", slow=True)
def loadset_plan_data(ctx: Context, story: Optional[str] = None) -> Dict[str, Any]:
    """
    Consolidated plan geometry and loadset definitions for web canvas visualization.
    Returns stories, points, slabs with loadset assignments, columns, beams, walls, and loadset definitions.
    Section dimensions and wall thicknesses are scaled to the general length unit (e.g. meters) for drawing.
    """
    ctx.progress.start(total=10, desc="Extracting ETABS model data")

    # 1. Stories
    ctx.progress.step(msg="Reading story definitions...")
    try:
        st_df = stories(ctx)
        story_list = st_df[["Story", "Height", "Elevation"]].to_dict(orient="records")
    except Exception:
        story_list = []

    # 2. Points lookup
    ctx.progress.step(msg="Reading point coordinates...")
    pts_df = ctx.optional_table("Point Object Connectivity")
    points: Dict[str, Dict[str, float]] = {}
    if not pts_df.empty and "UniqueName" in pts_df.columns:
        for _, r in pts_df.iterrows():
            try:
                uname = str(r["UniqueName"]).strip()
                points[uname] = {
                    "x": float(r.get("X", 0.0)),
                    "y": float(r.get("Y", 0.0)),
                    "z": float(r.get("Z", 0.0))
                }
            except (ValueError, TypeError):
                continue

    # Scale factor for section length (e.g. mm) to general length (e.g. m)
    sec_scale = 0.001 if ctx.units.section == "mm" and ctx.units.length == "m" else 1.0

    # 3. Section dimensions for frame members
    ctx.progress.step(msg="Reading frame section dimensions...")
    section_dims: Dict[str, Dict[str, Any]] = {}
    rect_df = ctx.optional_table("Frame Section Property Definitions - Concrete Rectangular")
    if not rect_df.empty and "Name" in rect_df.columns:
        for _, r in rect_df.iterrows():
            try:
                section_dims[str(r["Name"]).strip()] = {
                    "w": float(r.get("Width", 0.3)) * sec_scale,
                    "d": float(r.get("Depth", 0.3)) * sec_scale,
                    "circle": False
                }
            except (ValueError, TypeError):
                pass

    circ_df = ctx.optional_table("Frame Section Property Definitions - Concrete Circle")
    if not circ_df.empty and "Name" in circ_df.columns:
        for _, r in circ_df.iterrows():
            try:
                dia = float(r.get("Diameter", 0.4)) * sec_scale
                section_dims[str(r["Name"]).strip()] = {
                    "w": dia,
                    "d": dia,
                    "circle": True
                }
            except (ValueError, TypeError):
                pass

    steel_df = ctx.optional_table("Frame Section Property Definitions - Steel I/Wide Flange")
    if steel_df.empty:
        steel_df = ctx.optional_table("Frame Section Property Definitions - Steel I Wide Flange")
    if not steel_df.empty and "Name" in steel_df.columns:
        for _, r in steel_df.iterrows():
            try:
                w = float(r.get("TopFlangeWidth", r.get("Width", 0.2))) * sec_scale
                d = float(r.get("TotalDepth", r.get("Depth", 0.3))) * sec_scale
                section_dims[str(r["Name"]).strip()] = {
                    "w": w,
                    "d": d,
                    "circle": False
                }
            except (ValueError, TypeError):
                pass

    # 4. Frame assignments and rotation angles
    ctx.progress.step(msg="Reading frame assignments and orientations...")
    frame_angles: Dict[str, float] = {}
    frame_sections: Dict[str, str] = {}
    fa_df = ctx.optional_table("Frame Assignments - Summary")
    if not fa_df.empty and "UniqueName" in fa_df.columns:
        for _, r in fa_df.iterrows():
            uname = str(r["UniqueName"]).strip()
            sec = r.get("AnalysisSect") or r.get("DesignSection") or r.get("SectionProperty")
            if pd.notna(sec):
                frame_sections[uname] = str(sec).strip()
            ang = r.get("AxisAngle")
            if pd.notna(ang):
                try:
                    frame_angles[uname] = float(ang)
                except (ValueError, TypeError):
                    pass

    fa_axes = ctx.optional_table("Frame Assignments - Local Axes")
    if not fa_axes.empty and "UniqueName" in fa_axes.columns:
        for _, r in fa_axes.iterrows():
            uname = str(r["UniqueName"]).strip()
            ang = r.get("Angle")
            if pd.notna(ang):
                try:
                    frame_angles[uname] = float(ang)
                except (ValueError, TypeError):
                    pass

    # 5. Columns
    ctx.progress.step(msg="Reading column connectivity...")
    columns = []
    col_df = ctx.optional_table("Column Object Connectivity")
    if not col_df.empty and "UniqueName" in col_df.columns:
        for _, r in col_df.iterrows():
            st_name = str(r.get("Story", "")).strip()
            if story and st_name != story:
                continue
            uname = str(r["UniqueName"]).strip()
            pI = points.get(str(r.get("UniquePtI", "")).strip())
            pJ = points.get(str(r.get("UniquePtJ", "")).strip())
            sec = frame_sections.get(uname)
            dims = section_dims.get(sec or "")
            columns.append({
                "uniqueName": uname,
                "kind": "Column",
                "story": st_name,
                "section": sec,
                "width": dims["w"] if dims else None,
                "depth": dims["d"] if dims else None,
                "circle": dims["circle"] if dims else False,
                "angle": frame_angles.get(uname, 0.0),
                "points": [p for p in (pI, pJ) if p]
            })

    # 6. Beams
    ctx.progress.step(msg="Reading beam connectivity...")
    beams = []
    beam_df = ctx.optional_table("Beam Object Connectivity")
    if not beam_df.empty and "UniqueName" in beam_df.columns:
        for _, r in beam_df.iterrows():
            st_name = str(r.get("Story", "")).strip()
            if story and st_name != story:
                continue
            uname = str(r["UniqueName"]).strip()
            pI = points.get(str(r.get("UniquePtI", "")).strip())
            pJ = points.get(str(r.get("UniquePtJ", "")).strip())
            sec = frame_sections.get(uname)
            dims = section_dims.get(sec or "")
            beams.append({
                "uniqueName": uname,
                "kind": "Beam",
                "story": st_name,
                "section": sec,
                "width": dims["w"] if dims else None,
                "points": [p for p in (pI, pJ) if p]
            })

    # 7. Walls
    ctx.progress.step(msg="Reading wall connectivity and thickness...")
    wall_props: Dict[str, float] = {}
    wp_df = ctx.optional_table("Wall Property Definitions - Specified")
    if not wp_df.empty and "Name" in wp_df.columns:
        for _, r in wp_df.iterrows():
            try:
                thk = r.get("WallThickness") or r.get("Thickness")
                if pd.notna(thk):
                    wall_props[str(r["Name"]).strip()] = float(thk) * sec_scale
            except (ValueError, TypeError):
                pass

    area_sections: Dict[str, str] = {}
    aa_df = ctx.optional_table("Area Assignments - Summary")
    if not aa_df.empty and "UniqueName" in aa_df.columns:
        for _, r in aa_df.iterrows():
            uname = str(r["UniqueName"]).strip()
            sec = r.get("SectProp") or r.get("SectionProperty")
            if pd.notna(sec):
                area_sections[uname] = str(sec).strip()

    walls = []
    wall_df = ctx.optional_table("Wall Object Connectivity")
    if not wall_df.empty and "UniqueName" in wall_df.columns:
        for _, r in wall_df.iterrows():
            st_name = str(r.get("Story", "")).strip()
            if story and st_name != story:
                continue
            uname = str(r["UniqueName"]).strip()
            p1 = points.get(str(r.get("UniquePt1", "")).strip())
            p2 = points.get(str(r.get("UniquePt2", "")).strip())
            p3 = points.get(str(r.get("UniquePt3", "")).strip())
            p4 = points.get(str(r.get("UniquePt4", "")).strip())
            sec = area_sections.get(uname)
            thk = wall_props.get(sec or "")

            plan_pts = []
            for p in (p1, p2, p3, p4):
                if p and not any((p["x"] - q["x"]) ** 2 + (p["y"] - q["y"]) ** 2 < 0.0004 for q in plan_pts):
                    plan_pts.append(p)
            inclined = bool(p1 and p2 and (len(plan_pts) >= 3 or not p3 or not p4 or (
                (p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2 < 0.0004
            )))

            walls.append({
                "uniqueName": uname,
                "kind": "Wall",
                "story": st_name,
                "section": sec,
                "thickness": thk,
                "inclinedShell": inclined,
                "points": [p for p in (p1, p2) if p]
            })

    # 8. Slabs (Floors) and Loadset assignments
    ctx.progress.step(msg="Processing floor slabs and loadset assignments...")
    loadset_assign_df = ctx.optional_table("Area Load Assignments - Uniform Load Sets")
    loadset_by_name: Dict[str, str] = {}
    if not loadset_assign_df.empty and "UniqueName" in loadset_assign_df.columns:
        for _, r in loadset_assign_df.iterrows():
            uname = str(r["UniqueName"]).strip()
            ls = r.get("LoadSet") or r.get("LoadSetName") or r.get("Name")
            if pd.notna(ls) and str(ls).strip():
                loadset_by_name[uname] = str(ls).strip()

    floor_df = ctx.optional_table("Floor Object Connectivity")
    slabs_dict: Dict[str, Dict[str, Any]] = {}
    current_name = None
    point_keys = ["UniquePt1", "UniquePt2", "UniquePt3", "UniquePt4"]
    if not floor_df.empty:
        for _, r in floor_df.iterrows():
            raw_name = r.get("UniqueName")
            if pd.notna(raw_name) and str(raw_name).strip():
                current_name = str(raw_name).strip()
                if current_name not in slabs_dict:
                    slabs_dict[current_name] = {
                        "uniqueName": current_name,
                        "story": str(r.get("Story", "")).strip(),
                        "vertexIds": []
                    }
            if current_name is None:
                continue
            for k in point_keys:
                pid = r.get(k)
                if pd.notna(pid) and str(pid).strip():
                    slabs_dict[current_name]["vertexIds"].append(str(pid).strip())

    slabs = []
    for uname, slab in slabs_dict.items():
        if story and slab["story"] != story:
            continue
        pts = [points[pid] for pid in slab["vertexIds"] if pid in points]
        slabs.append({
            "uniqueName": uname,
            "kind": "Slab",
            "story": slab["story"],
            "loadset": loadset_by_name.get(uname),
            "vertexIds": slab["vertexIds"],
            "points": pts
        })

    # 9. Loadset definitions
    ctx.progress.step(msg="Reading shell uniform load sets...")
    loadset_defs: Dict[str, List[Dict[str, Any]]] = {}
    ls_df = ctx.optional_table("Shell Uniform Load Sets")
    if not ls_df.empty:
        for _, r in ls_df.iterrows():
            name = str(r.get("Name") or r.get("LoadSet") or "").strip()
            if not name:
                continue
            pat = str(r.get("LoadPattern") or r.get("Pattern") or "").strip()
            val = r.get("LoadValue") or r.get("Value")
            try:
                val_num = float(val) if pd.notna(val) else None
            except (ValueError, TypeError):
                val_num = None
            loadset_defs.setdefault(name, []).append({"pattern": pat, "value": val_num})

    # 10. Existing groups in ETABS
    ctx.progress.step(msg="Checking existing ETABS groups...")
    existing_groups: Dict[str, Optional[int]] = {}
    grp_df = ctx.optional_table("Group Definitions")
    if not grp_df.empty:
        for _, r in grp_df.iterrows():
            gname = str(r.get("Name") or r.get("GroupName") or "").strip()
            if gname:
                gcol = r.get("Color")
                try:
                    existing_groups[gname] = int(gcol) if pd.notna(gcol) else None
                except (ValueError, TypeError):
                    existing_groups[gname] = None

    return {
        "stories": story_list,
        "points": points,
        "slabs": slabs,
        "columns": columns,
        "beams": beams,
        "walls": walls,
        "loadsets": loadset_defs,
        "existing_groups": existing_groups
    }


@operation("column_layout_tables", needs="tables", slow=True)
def column_layout_tables(ctx: Context) -> Dict[str, Any]:
    """
    Extract all raw tables required by the Column & Wall Layout HTML viewer.
    Returns tables as { "tables": { table_name: { "columns": [...], "values": [...] } } }.
    """
    table_candidates = [
        "Point Object Connectivity",
        "Frame Assignments - Summary",
        "Column Object Connectivity",
        "Beam Object Connectivity",
        "Frame Section Property Definitions - Summary",
        "Wall Object Connectivity",
        "Wall Property Definitions - Specified",
        "Area Assignments - Summary",
        "Area Assignments - Pier Labels",
        "Group Assignments",
        "Group Definitions",
        "Story Definitions",
    ]
    design_prefixes = [
        "Concrete Frame Design Load Combination Data",
        "Concrete Frame Design Preferences",
        "Concrete Beam Overwrites",
        "Concrete Column Design Summary",
        "Concrete Column PMM Shear Envelope",
        "Concrete Column PMM Envelope",
        "Concrete Column Shear Envelope",
        "Concrete Column Overwrites",
        "Concrete Beam Design Summary",
        "Concrete Beam Flexure Envelope",
        "Concrete Beam Shear Envelope",
        "Concrete Joint Design Summary",
        "Concrete Joint Envelope",
        "Shear Wall Design Load Combination Data",
        "Shear Wall Design Preferences",
        "Shear Wall Pier Design Overwrites",
        "Shear Wall Pier Design Summary",
        "Shear Wall Spandrel Design Overwrites",
        "Shear Wall Spandrel Design Summary",
        "Steel Frame Design Preferences",
        "Steel Frame Design Overwrites",
        "Steel Frame Design Summary",
        "Steel Column Envelope",
        "Steel Beam Envelope",
    ]

    available_tables: List[str] = []
    if hasattr(ctx.source, "tables"):
        try:
            available_tables = list(ctx.source.tables())
        except Exception:
            available_tables = []

    # Resolve design tables by their code-independent prefix. ETABS appends the
    # active design code (ACI, Eurocode, AISC, etc.) to most table names.
    resolved_design_tables: List[str] = []
    for prefix in design_prefixes:
        matched = [t for t in available_tables if t.lower().startswith(prefix.lower())]
        if matched:
            resolved_design_tables.extend(matched)
        elif not available_tables:
            # A custom TableSource may not implement table discovery. Retain the
            # old best-effort behaviour for that case only.
            resolved_design_tables.append(prefix)

    all_targets = list(dict.fromkeys(table_candidates + resolved_design_tables))
    ctx.progress.start(total=len(all_targets), desc="Extracting column and wall layout tables")

    KNOWN_FIELD_KEY_TO_NAME = {
        "Frame Assignments - Summary": {
            "AnalysisSect": "Analysis Section",
            "DesignSect": "Design Section",
            "AxisAngle": "Axis Angle",
            "AutoSelect": "Auto Select",
            "Type": "Design Type",
            "MaxStaSpcg": "Max Station Spacing",
            "MinNumSta": "Min Number Stations",
            "UserOffsets": "User Offsets",
            "AddedMass": "Added Mass",
        },
        "Wall Property Definitions - Specified": {
            "Thickness": "Wall Thickness",
            "ModelType": "Modeling Type",
            "RigidZone": "Include Auto Rigid Zone?",
            "f11Mod": "f11 Modifier",
            "f22Mod": "f22 Modifier",
            "f12Mod": "f12 Modifier",
            "m11Mod": "m11 Modifier",
            "m22Mod": "m22 Modifier",
            "m12Mod": "m12 Modifier",
            "v13Mod": "v13 Modifier",
            "v23Mod": "v23 Modifier",
            "MMod": "Mass Modifier",
            "WMod": "Weight Modifier",
        },
        "Area Assignments - Summary": {
            "SectProp": "Section Property",
            "PropType": "Property Type",
            "AxisAngle": "Axis Angle",
            "AddedMass": "Added Mass",
        },
        "Group Assignments": {
            "GroupName": "Group Name",
            "ObjectType": "Object Type",
            "UniqueName": "Object Unique Name",
        },
        "Column Object Connectivity": {
            "UniqueName": "Unique Name",
        },
        "Beam Object Connectivity": {
            "UniqueName": "Unique Name",
        },
        "Area Assignments - Pier Labels": {
            "PierName": "Pier Name",
        },
    }
    # Common design-result keys whose ETABS display names are inconsistent
    # across code/version combinations. These canonical names are also used
    # when a non-live source cannot provide GetAllFieldsInTable metadata.
    COMMON_DESIGN_FIELD_KEY_TO_NAME = {
        "DesignSect": "Design Section",
        "WarnMsg": "Warnings",
        "ErrMsg": "Errors",
        "RatioRebar": "PMM Ratio or Rebar %",
        "Pier": "Pier Label",
        "ReinfPcent": "Required Reinf. Percentage",
        "ShearAv": "Shear Rebar",
        "MMajRatio": "M Major Ratio",
        "MMinRatio": "M Minor Ratio",
    }

    out_tables: Dict[str, Dict[str, Any]] = {}
    read_warnings: List[Dict[str, str]] = []

    for name in all_targets:
        ctx.progress.step(msg=f"Reading {name}...")
        try:
            df = ctx.optional_table(name)
            if not df.empty:
                orig_units = dict(df.attrs.get("units", {}))
                field_map = dict(COMMON_DESIGN_FIELD_KEY_TO_NAME)
                field_map.update(KNOWN_FIELD_KEY_TO_NAME.get(name, {}))
                unit_map = {}
                if hasattr(ctx.source, "sap_model") and ctx.source.sap_model is not None:
                    try:
                        ret = ctx.source.sap_model.DatabaseTables.GetAllFieldsInTable(name)
                        if ret[-1] == 0:
                            field_map.update(dict(zip(ret[2], ret[3])))
                            unit_map = dict(zip(ret[3], ret[5]))
                    except Exception:
                        pass
                if field_map:
                    df = df.rename(columns=field_map)
                clean_df = df.astype(object).where(pd.notna(df), None)
                cols = list(clean_df.columns)
                renamed_orig_units = {field_map.get(k, k): v for k, v in orig_units.items()}
                col_units = [renamed_orig_units.get(c, unit_map.get(c, "")) for c in cols]
                out_tables[name] = {
                    "columns": cols,
                    "units": col_units,
                    "values": clean_df.values.tolist(),
                }
        except Exception as exc:
            read_warnings.append({
                "table": name,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })

    return {"tables": out_tables, "warnings": read_warnings}
