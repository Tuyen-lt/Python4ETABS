"""
Model operations: stories, element lists, materials and element properties.
All work on every source (needs="tables").
"""
from bisect import bisect_left
from numbers import Real
from typing import Iterable, List, Optional

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
