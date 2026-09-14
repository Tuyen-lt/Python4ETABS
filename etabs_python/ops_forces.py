"""
Force operations read from ETABS results tables (needs="tables"):
Element Forces, reactions, story response, modal results and mass/stiffness summaries.
"""
from typing import List, Optional

import pandas as pd

from .engine import Context, operation, to_list
from .errors import EngineError
from .ops_model import _finish, beams

FORCE_COMPONENTS = ["P", "V2", "V3", "T", "M2", "M3"]


def _filter(df: pd.DataFrame, column: str, values) -> pd.DataFrame:
    values = [str(v) for v in to_list(values)]
    if not values:
        return df
    return _finish(df[df[column].astype(str).isin(values)].reset_index(drop=True), df.attrs.get("table", ""), df)


@operation("beam_forces", slow=True)
def beam_forces(ctx: Context, names=None, cases=None, combos=None) -> pd.DataFrame:
    """Beam forces per station (Element Forces - Beams): UniqueName, OutputCase, Station, P, V2, V3, T, M2, M3."""
    return _filter(ctx.table("Element Forces - Beams", cases, combos), "UniqueName", names)


@operation("column_forces", slow=True)
def column_forces(ctx: Context, names=None, cases=None, combos=None) -> pd.DataFrame:
    """Column forces per station (Element Forces - Columns)."""
    return _filter(ctx.table("Element Forces - Columns", cases, combos), "UniqueName", names)


@operation("pier_forces")
def pier_forces(ctx: Context, piers=None, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Pier forces at Top/Bottom of each story (Pier Forces)."""
    df = _filter(ctx.table("Pier Forces", cases, combos), "Pier", piers)
    return _filter(df, "Story", stories)


@operation("joint_reactions", slow=True)
def joint_reactions(ctx: Context, names=None, cases=None, combos=None) -> pd.DataFrame:
    """Support reactions FX, FY, FZ, MX, MY, MZ (Joint Reactions)."""
    return _filter(ctx.table("Joint Reactions", cases, combos), "UniqueName", names)


@operation("base_reactions")
def base_reactions(ctx: Context, cases=None, combos=None) -> pd.DataFrame:
    """Resultant base reactions FX, FY, FZ, MX, MY, MZ and resultant location."""
    return ctx.table("Base Reactions", cases, combos)


@operation("story_drifts")
def story_drifts(ctx: Context, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Point-based story drift ratios and governing point/location by direction."""
    return _filter(ctx.table("Story Drifts", cases, combos), "Story", stories)


@operation("story_max_over_average_drifts")
def story_max_over_average_drifts(ctx: Context, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Maximum drift, average drift and max/average ratio by story and direction."""
    return _filter(ctx.table("Story Max Over Avg Drifts", cases, combos), "Story", stories)


@operation("diaphragm_max_over_average_drifts")
def diaphragm_max_over_average_drifts(ctx: Context, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Diaphragm maximum/average drift ratios and governing coordinates."""
    return _filter(ctx.table("Diaphragm Max Over Avg Drifts", cases, combos), "Story", stories)


def _modal(ctx: Context, table: str, cases=None, modes=None) -> pd.DataFrame:
    df = _filter(ctx.table(table), "Case", cases)
    return _filter(df, "Mode", modes) if "Mode" in df.columns else df


@operation("modal_periods")
def modal_periods(ctx: Context, cases=None, modes=None) -> pd.DataFrame:
    """Modal periods, frequencies, circular frequencies and eigenvalues."""
    return _modal(ctx, "Modal Periods And Frequencies", cases, modes)


@operation("modal_mass_participation")
def modal_mass_participation(ctx: Context, cases=None, modes=None) -> pd.DataFrame:
    """Per-mode and cumulative translational/rotational participating mass ratios."""
    return _modal(ctx, "Modal Participating Mass Ratios", cases, modes)


@operation("modal_load_participation")
def modal_load_participation(ctx: Context, cases=None) -> pd.DataFrame:
    """Static and dynamic modal load participation percentages."""
    return _filter(ctx.table("Modal Load Participation Ratios"), "Case", cases)


@operation("modal_participation_factors")
def modal_participation_factors(ctx: Context, cases=None, modes=None) -> pd.DataFrame:
    """Modal participation factors, modal mass and modal stiffness."""
    return _modal(ctx, "Modal Participation Factors", cases, modes)


@operation("modal_direction_factors")
def modal_direction_factors(ctx: Context, cases=None, modes=None) -> pd.DataFrame:
    """Direction factors UX, UY, UZ and RZ for each mode."""
    return _modal(ctx, "Modal Direction Factors", cases, modes)


@operation("story_forces")
def story_forces(ctx: Context, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Story forces P, VX, VY, T, MX and MY at top/bottom locations."""
    return _filter(ctx.table("Story Forces", cases, combos), "Story", stories)


@operation("story_stiffness")
def story_stiffness(ctx: Context, stories=None, cases=None, combos=None) -> pd.DataFrame:
    """Story shear, drift, lateral stiffness, irregularity and modifier."""
    return _filter(ctx.table("Story Stiffness", cases, combos), "Story", stories)


@operation("centers_of_mass_and_rigidity")
def centers_of_mass_and_rigidity(ctx: Context, stories=None) -> pd.DataFrame:
    """Story/diaphragm mass, center of mass and center of rigidity coordinates."""
    return _filter(ctx.table("Centers Of Mass And Rigidity"), "Story", stories)


@operation("tributary_area_llrf")
def tributary_area_llrf(ctx: Context, stories=None, names=None) -> pd.DataFrame:
    """Tributary area and live-load reduction factor for frame objects."""
    df = _filter(ctx.table("Tributary Area and LLRF"), "Story", stories)
    return _filter(df, "UniqueName", names)


def zone_names(zones) -> List[str]:
    return ["Start", "Middle", "End"] if len(zones) == 3 else [f"Z{i + 1}" for i in range(len(zones))]


def zone_envelope(forces: pd.DataFrame, lengths: pd.Series, zones=(0.25, 0.5, 0.25),
                  envelope: bool = False, tol: float = 1e-4) -> pd.DataFrame:
    """
    Max/min of P, V2, V3, T, M2, M3 per object and length zone.
    forces: Element Forces table (UniqueName, Station, OutputCase[, StepType], components).
    lengths: Series UniqueName -> object length (same length unit as Station).
    A station on a zone boundary counts for both zones. envelope=True ignores OutputCase/StepType.
    """
    zones = [float(z) for z in zones]
    if abs(sum(zones) - 1.0) > 1e-6:
        raise EngineError(f"zones must sum to 1, got {sum(zones)}")
    keys = ["UniqueName"] if envelope else ["UniqueName"] + [c for c in ("OutputCase", "StepType") if c in forces.columns]
    out_columns = keys + ["Zone", "ZoneStart", "ZoneEnd"] + [f"{c}_{s}" for c in FORCE_COMPONENTS for s in ("max", "min")]
    if forces.empty:
        return pd.DataFrame(columns=out_columns)

    rel = forces["Station"] / forces["UniqueName"].map(lengths).astype(float)
    parts, start = [], 0.0
    for name, ratio in zip(zone_names(zones), zones):
        end = start + ratio
        part = forces[(rel >= start - tol) & (rel <= end + tol)]
        parts.append(part.assign(Zone=name, ZoneStart=round(start, 10), ZoneEnd=round(end, 10)))
        start = end
    data = pd.concat(parts, ignore_index=True)
    if "StepType" in keys:
        data["StepType"] = data["StepType"].fillna("")
    components = [c for c in FORCE_COMPONENTS if c in data.columns]
    out = data.groupby(keys + ["Zone", "ZoneStart", "ZoneEnd"], sort=False)[components].agg(["max", "min"])
    out.columns = [f"{c}_{s}" for c, s in out.columns]
    return out.reset_index().sort_values(keys + ["ZoneStart"], kind="stable", ignore_index=True)


@operation("beam_forces_by_zone", slow=True)
def beam_forces_by_zone(ctx: Context, names=None, cases=None, combos=None,
                        zones=(0.25, 0.5, 0.25), envelope: bool = False) -> pd.DataFrame:
    """
    Beam forces grouped by length zones (default 0.25L / 0.5L / 0.25L = Start / Middle / End):
    max/min of P, V2, V3, T, M2, M3 per beam, zone and output case (envelope=True: over all cases).
    Station is measured from the I-end of the object. Tables: Element Forces - Beams, Frame Assignments - Summary.
    """
    if abs(sum(float(z) for z in zones) - 1.0) > 1e-6:
        raise EngineError(f"zones must sum to 1, got {sum(float(z) for z in zones)}")
    beam_list = beams(ctx)
    forces = beam_forces(ctx, names, cases, combos)
    forces = forces[forces["UniqueName"].isin(beam_list["UniqueName"])]
    out = zone_envelope(forces, beam_list.set_index("UniqueName")["Length"], zones, envelope)
    info = beam_list[[c for c in ("UniqueName", "Story", "Label", "Length", "AnalysisSect") if c in beam_list.columns]]
    out = info.merge(out, on="UniqueName", how="right")
    force_units = forces.attrs.get("units", {})
    units = {f"{c}_{s}": force_units[c] for c in FORCE_COMPONENTS for s in ("max", "min") if c in force_units}
    units.update({k: v for k, v in beam_list.attrs.get("units", {}).items() if k == "Length"})
    out.attrs = {"table": "beam_forces_by_zone", "units": units}
    return out
