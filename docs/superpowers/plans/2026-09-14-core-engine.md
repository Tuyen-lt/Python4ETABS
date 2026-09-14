# ETABS Core Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the library as `etabs_python`, a core engine that reads ETABS data from a live model, an ETABS Excel export or an engine CSV snapshot, runs named operations with progress, converts to SI units, and serves them over FastAPI with background jobs.

**Architecture:** Every source exposes `table(name, cases, combos) -> DataFrame` keyed by ETABS field keys with native unit strings in `df.attrs`. `Engine` converts units right after reading and dispatches registered operations (`@operation(name, needs, slow)`), passing a `Context` with `table()` and `progress`. The server runs all engine calls on one worker thread (COM) and wraps `slow` operations as jobs.

**Tech Stack:** Python 3.8+, pandas, openpyxl, comtypes/pywin32, tqdm, FastAPI, uvicorn, pytest, httpx (TestClient).

**Spec:** `docs/superpowers/specs/2026-09-14-core-engine-design.md`

## Global Constraints

- All code, docstrings, log/error messages and docs in English.
- Python `>=3.8` syntax: no `dict | dict`, no `match`, use `typing.List/Dict/Optional`.
- Only new runtime dependency: `tqdm`.
- Units: SI only. Defaults force `kN`, general length `m`, section length `mm`, stress `MPa`. Imperial units raise `UnitError`.
- Column names are ETABS field keys (e.g. `AnalysisSect`, `AMod`, `UniqueName`).
- Identifier columns stay `str` (never numeric).
- `ETABS_EXPORTED*.xlsx` is never committed (license number + project data). Tests use a synthetic fixture.
- Live tests are read-only: they may change output/display selection and object selection but must restore object selection; never modify the model.
- Tests: `python -m pytest` from repo root; live tests skip when ETABS is not running.

## File map

| File | Responsibility |
|---|---|
| `etabs_python/__init__.py` | public exports; imports ops modules to register operations |
| `etabs_python/errors.py` | exception hierarchy |
| `etabs_python/connection.py` | COM attach + lock helpers (moved, translated) |
| `etabs_python/bridge.py` | `EtabsDataBridge` write helpers (moved from `data_bridge.py`, translated) |
| `etabs_python/parser.py` | payload parsing (moved from `data_parser.py`, translated) |
| `etabs_python/rename.py` | bulk rename (moved from `Object_rename.py`, translated) |
| `etabs_python/units.py` | unit parsing, `Units`, `convert_table`, `convert_value`, `format_unit` |
| `etabs_python/schema.json` | `{table: {display name: field key}}` where they differ |
| `etabs_python/sources.py` | `TableSource`, `ExcelSource`, `FileSource`, `LiveSource`, `coerce_types` |
| `etabs_python/engine.py` | `operation`, `Progress`, `Context`, `Engine` |
| `etabs_python/ops_model.py` | stories, story_at, element lists, materials, properties |
| `etabs_python/ops_forces.py` | forces tables, `zone_envelope`, `beam_forces_by_zone` |
| `etabs_python/ops_live.py` | selection, `frame_info`, `execute_batch` |
| `etabs_python/server.py` | FastAPI app, worker thread, jobs |
| `tools/build_schema.py` | regenerate `schema.json` from running ETABS |
| `tests/conftest.py` | synthetic Excel fixture builder, `etabs_live` skip fixture |
| `tests/test_units.py`, `tests/test_sources.py`, `tests/test_engine.py`, `tests/test_ops_offline.py`, `tests/test_server.py`, `tests/test_live.py` | tests |
| `docs/ENGINE_GUIDE.md`, `AGENTS.md`, `README.md` | docs |

Removed at the end: root `__init__.py`, `etabs_model.py`, `test_etabs_model.py`, `test_live_readonly.py`, `example_pull_inject.py`, `server.py`, `start_server.bat`.

---

### Task 1: Package skeleton, errors, moved modules

**Files:**
- Move: `connection.py` -> `etabs_python/connection.py`, `data_bridge.py` -> `etabs_python/bridge.py`, `data_parser.py` -> `etabs_python/parser.py`, `Object_rename.py` -> `etabs_python/rename.py`
- Create: `etabs_python/__init__.py`, `etabs_python/errors.py`, `tests/test_package.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `etabs_python.errors.{EngineError, SourceError, TableNotFound(table, source), ResultNotAvailable, UnitError(table, column, unit)}`, re-exported `EtabsConnectionError`, `ModelLockedError`; `etabs_python.bridge.EtabsDataBridge`; `etabs_python.parser.parse_json_payload`.

- [ ] **Step 1: Failing test** `tests/test_package.py`

```python
from etabs_python.errors import EngineError, TableNotFound, UnitError


def test_errors_hierarchy_and_messages():
    assert issubclass(TableNotFound, EngineError)
    e = TableNotFound("Story Definitions", "excel")
    assert "Story Definitions" in str(e) and "excel" in str(e)
    u = UnitError("T", "Fc", "ksi")
    assert "ksi" in str(u) and "Fc" in str(u)
```

- [ ] **Step 2:** `python -m pytest tests/test_package.py -v` -> FAIL (no package).
- [ ] **Step 3:** `git mv` the four modules; sibling imports become relative (`from .connection import ...`); translate every Vietnamese docstring, comment, log and error message to English. Verify with `grep -nE "Khong|khong|Dong |Loi |that bai|Tep |Muc |mo hinh|cau kien" etabs_python/*.py` -> no output.
- [ ] **Step 4:** Create `etabs_python/errors.py`:

```python
"""Exception hierarchy of the engine."""
from .connection import EtabsConnectionError, ModelLockedError  # noqa: F401


class EngineError(Exception):
    """Base class of engine errors."""


class SourceError(EngineError):
    """Source cannot be opened or does not support the operation."""


class TableNotFound(EngineError):
    def __init__(self, table: str, source: str):
        super().__init__(f"Table '{table}' not found in {source} source. "
                         f"Export it from ETABS or check the table name.")
        self.table, self.source = table, source


class ResultNotAvailable(EngineError):
    """Analysis results are missing (model not run, or case/combo not finished)."""


class UnitError(EngineError):
    def __init__(self, table: str, column: str, unit: str):
        super().__init__(f"Unsupported unit '{unit}' in column '{column}' of table '{table}'. "
                         f"Only SI units are supported.")
        self.table, self.column, self.unit = table, column, unit
```

- [ ] **Step 5:** `etabs_python/__init__.py` final form (Tasks 2-7 fill the modules):

```python
"""etabs_python: ETABS core engine."""
from .errors import (EngineError, SourceError, TableNotFound, ResultNotAvailable, UnitError,
                     EtabsConnectionError, ModelLockedError)
from .units import Units
from .sources import TableSource, ExcelSource, FileSource, LiveSource
from .engine import Engine, operation, Progress
from . import ops_model, ops_forces, ops_live  # noqa: F401  registers operations
```
Until Task 7, `__init__.py` imports only what exists.

- [ ] **Step 6:** `pyproject.toml`: wheel `packages = ["etabs_python"]`, add `tqdm`, version `3.0.0`, `[tool.pytest.ini_options] pythonpath = ["."]`, `testpaths = ["tests"]`.
- [ ] **Step 7:** run test -> PASS; commit `refactor: move library into etabs_python package`.

### Task 2: Units

**Files:** Create `etabs_python/units.py`, `tests/test_units.py`

**Interfaces (produces):**
- `parse_unit(unit, table="", column="") -> Optional[Tuple[float, int, int]]`: factor to the N/mm base, force power, length power. `None` for units not built from force/length. Raises `UnitError` for imperial tokens.
- `Units(force="kN", length="m", section="mm", stress="MPa")` (frozen dataclass, validates choices).
- `format_unit(fp, lp, force, length) -> str` (`kN-m`, `kN/m³`, `mm⁴`, `1/m`).
- `convert_table(df, units) -> DataFrame` using `df.attrs["table"]` and `df.attrs["units"]`; returns a copy with converted `attrs["units"]`.
- `convert_value(value, unit, units, section=False, table="") -> float`.

Rules: stress target when the source token is `Pa/kPa/MPa`, or the column is force/length² in a table starting with `Material Properties`. Section length for tables starting with `Frame Section Property Definitions`, `Area Section Property Definitions`, `Slab Property Definitions`, `Wall Property Definitions`, `Deck Property Definitions`, `Reinforcing Bar Sizes`. Dimensionless (fp=lp=0) and unparseable units unchanged.

- [ ] **Step 1: Failing tests** `tests/test_units.py`

```python
import math
import pandas as pd
import pytest
from etabs_python.units import parse_unit, Units, format_unit, convert_table, convert_value
from etabs_python.errors import UnitError


@pytest.mark.parametrize("u,expected", [
    ("kN", (1e3, 1, 0)), ("N-mm", (1.0, 1, 1)), ("kN-m", (1e6, 1, 1)), ("mm", (1.0, 0, 1)),
    ("m", (1e3, 0, 1)), ("cm²", (100.0, 0, 2)), ("cm⁴", (1e4, 0, 4)), ("mm4", (1.0, 0, 4)),
    ("kN/m", (1.0, 1, -1)), ("kN/m³", (1e-6, 1, -3)), ("MPa", (1.0, 1, -2)), ("kPa", (1e-3, 1, -2)),
    ("N/mm2", (1.0, 1, -2)), ("kN-m/m", (1e3, 1, 0)), ("1/m", (1e-3, 0, -1)), ("mm/mm", (1.0, 0, 0)),
])
def test_parse_unit(u, expected):
    f, fp, lp = parse_unit(u)
    assert (fp, lp) == expected[1:] and math.isclose(f, expected[0], rel_tol=1e-12)


@pytest.mark.parametrize("u", ["kg", "ton-m²", "1/C", "cyc/sec", "deg", "kN-m/rad", "mm/sec²"])
def test_non_force_length_units_return_none(u):
    assert parse_unit(u) is None


@pytest.mark.parametrize("u", ["kip", "lb-ft", "in", "ksi", "psi", "kip-ft"])
def test_imperial_raises(u):
    with pytest.raises(UnitError):
        parse_unit(u, table="T", column="C")


def test_format_unit():
    assert format_unit(1, 1, "kN", "m") == "kN-m"
    assert format_unit(1, -3, "kN", "m") == "kN/m³"
    assert format_unit(0, 4, "kN", "mm") == "mm⁴"
    assert format_unit(0, -1, "kN", "m") == "1/m"


def test_convert_section_table():
    df = pd.DataFrame({"Name": ["B"], "Area": [1800.0], "t3": [600.0]})
    df.attrs = {"table": "Frame Section Property Definitions - Summary", "units": {"Area": "cm²", "t3": "mm"}}
    out = convert_table(df, Units())
    assert out["Area"][0] == pytest.approx(180000.0) and out.attrs["units"]["Area"] == "mm²"
    assert out["t3"][0] == 600.0 and out.attrs["units"]["t3"] == "mm"


def test_convert_general_table_keeps_area_load_as_force_per_area():
    g = pd.DataFrame({"Station": [8000.0], "M3": [5e6], "Load": [2.0], "Name": ["x"]})
    g.attrs = {"table": "Element Forces - Beams", "units": {"Station": "mm", "M3": "N-mm", "Load": "kN/m²"}}
    out = convert_table(g, Units())
    assert out["Station"][0] == pytest.approx(8.0) and out["M3"][0] == pytest.approx(5.0)
    assert out["Load"][0] == pytest.approx(2.0) and out.attrs["units"]["Load"] == "kN/m²"


def test_convert_material_table_uses_stress_unit():
    m = pd.DataFrame({"E1": [30000.0], "UnitWeight": [2.5e-5]})
    m.attrs = {"table": "Material Properties - Basic Mechanical Properties",
               "units": {"E1": "N/mm²", "UnitWeight": "N/mm³"}}
    out = convert_table(m, Units())
    assert out["E1"][0] == pytest.approx(30000.0) and out.attrs["units"]["E1"] == "MPa"
    assert out["UnitWeight"][0] == pytest.approx(25.0) and out.attrs["units"]["UnitWeight"] == "kN/m³"


def test_units_validation_and_convert_value():
    with pytest.raises(ValueError):
        Units(force="kip")
    assert convert_value(7000.0, "mm", Units()) == pytest.approx(7.0)
    assert convert_value(7000.0, "mm", Units(length="mm")) == pytest.approx(7000.0)
```

- [ ] **Step 2:** run -> FAIL. **Step 3:** implement. **Step 4:** run -> PASS. **Step 5:** commit `feat(units): SI unit parsing and conversion`.

### Task 3: Schema and sources

**Files:** Create `tools/build_schema.py`, `etabs_python/schema.json`, `etabs_python/sources.py`, `tests/conftest.py`, `tests/test_sources.py`

**Interfaces:**
- Consumes: `errors.TableNotFound`, `errors.SourceError`, `errors.ResultNotAvailable`.
- Produces:
  - `TableSource` with `kind: str`, `tables() -> List[str]`, `table(name, cases=None, combos=None) -> DataFrame` (field-key columns, `attrs["table"]`, `attrs["units"]` native units).
  - `ExcelSource(path)`, `FileSource(folder)`, `LiveSource(pid=None, sap_model=None)` with `.sap_model`, `.bridge`, `present_units(code)` context manager.
  - `coerce_types(df) -> DataFrame`: identifier columns to `str`, other object columns numeric when every non-null value parses.
  - `ID_COLUMNS: set`, `header_to_key(table, header) -> str`, `write_snapshot(folder, frames: Dict[str, DataFrame], units_system: dict)` used by `Engine.export`.
- `tests/conftest.py` produces fixtures `export_xlsx` (path to synthetic workbook) and `etabs_live` (LiveSource or `pytest.skip`).

**Excel layout:** row 1 `TABLE:  <name>`, row 2 display headers, row 3 units (blank cells allowed), data from row 4. Table name from row 1.

**Schema:** `schema.json` = `{table: {display_name: field_key}}` only for pairs that differ; built by `tools/build_schema.py` from `DatabaseTables.GetAllTables` + `GetAllFieldsInTable`. Unknown headers: remove whitespace and `?`.

**LiveSource.table:** inside `present_units(9)` (N-mm; restore afterwards): `GetAllFieldsInTable(name)` (ret != 0 -> `TableNotFound`) gives units; when `cases`/`combos` is not None, validate names against `LoadCases.GetNameList` / `RespCombo.GetNameList` (unknown -> `EngineError`), then `SetLoadCasesSelectedForDisplay(cases or [])` and `SetLoadCombinationsSelectedForDisplay(combos or [])`; read `GetTableForDisplayArray(name, [], "", 0, [], 0, [])`; tables with an `OutputCase` field and zero rows -> `ResultNotAvailable`.

**Fixture workbook (synthetic, units as a user would export in kN-m with mm sections):**
- `Story Definitions`: Tower, Name, Height(m): (T1, FL2, 3.5), (T1, FL1, 4.0)  [top first]
- `Tower and Base Story Definitions`: Tower, BSName, BSElev(m): (T1, Base, 0)
- `Point Object Connectivity`: UniqueName, Is Auto Point, Story, PointBay, IsSpecial, X, Y, Z (m): (1,No,Base,1,No,0,0,0), (2,No,FL1,1,No,0,0,4.0), (3,No,FL2,1,No,0,0,7.5), (4,No,FL1,2,No,8,0,4.0)
- `Frame Assignments - Summary`: Story, Label, UniqueName, Design Type, Length(m), Analysis Section, Design Section, Axis Angle(deg), Modifiers: (FL1,B1,10,Beam,8,B30x60,B30x60,0,Yes), (FL1,B2,11,Beam,6,B30x60,B30x60,0,No), (FL1,C1,12,Column,4,C50x50,C50x50,0,No)
- `Frame Assignments - Property Modifiers`: Story, Label, UniqueName, Area Modifier, As2 Modifier, As3 Modifier, J Modifier, I22 Modifier, I33 Modifier, Mass Modifier, Weight Modifier: (FL1,B1,10,1,1,1,0.1,0.5,0.5,1,1)
- `Frame Assignments - End Length Offsets`: Story, Label, UniqueName, Offset Option, Offset I(mm), Offset J(mm), Rigid Factor: rows for 10, 11, 12 with 250, 250, 0
- `Frame Section Property Definitions - Summary`: Name, Material, Shape, Area(cm²), I33(cm⁴), I22(cm⁴), Area Modifier..Weight Modifier (order Area, As2, As3, J, I33, I22, Mass, Weight): (B30x60, C30, Concrete Rectangular, 1800, 540000, 135000, 1,1,1,1,0.7,1,1,1), (C50x50, C30, Concrete Rectangular, 2500, 520833.3, 520833.3, 1,1,1,1,1,1,1,1)
- `Frame Section Property Definitions - Concrete Rectangular`: Name, Material, Depth(mm), Width(mm): (B30x60, C30, 600, 300), (C50x50, C30, 500, 500)
- `Material Properties - General`: Material, Type, Grade: (C30, Concrete, C30/37)
- `Material Properties - Basic Mechanical Properties`: Material, UnitWeight(kN/m³), E1(MPa), G12(MPa), U12: (C30, 25, 32000, 13333.3, 0.2)
- `Material Properties - Concrete Data`: Material, Fc(MPa): (C30, 30)
- `Area Assignments - Summary`: Story, Label, UniqueName, Section Property, Property Type: (FL1,F1,20,S200,Slab), (FL1,W1,21,W300,Wall)
- `Area Assignments - Stiffness Modifiers`: Story, Label, UniqueName, f11..Weight Modifier (10 cols): (FL1,F1,20,1,1,1,0.25,0.25,0.25,1,1,1,1)
- `Area Section Property Definitions - Summary`: Name, Type, Element Type, Material, Total Thickness(mm): (S200,Slab,Shell-Thin,C30,200), (W300,Wall,Shell-Thin,C30,300)
- `Slab Property Definitions`: Name, Modeling Type, Property Type, Material, Slab Thickness(mm), f11..Weight Modifier: (S200,Shell-Thin,Slab,C30,200,1,1,1,0.5,0.5,0.5,1,1,1,1)
- `Wall Property Definitions - Specified`: Name, Modeling Type, Material, Wall Thickness(mm), f11..Weight Modifier: (W300,Shell-Thin,C30,300, all 1)
- `Element Forces - Beams`: Story, Beam, Unique Name, Output Case, Case Type, Station(m), P,V2,V3(kN), T,M2,M3(kN-m): B1 (10) stations 0..8 step 1 for `ULS1` with M3 = station, V2 = 10 - station, and `ULS2` with M3 = -station, V2 = station - 10; B2 (11) stations 0, 1.5, 3, 4.5, 6 for `ULS1` with M3 = 10 * station, V2 = 1
- `Element Forces - Columns`: Story, Column, Unique Name, Output Case, Case Type, Station(m), P..M3: C1 (12) ULS1 stations 0 and 4 with P -100 and -90
- `Pier Forces`: Story, Pier, Output Case, Case Type, Location, P..M3: (FL1,P1,ULS1,Combination,Top,-500,...), (FL1,P1,ULS1,Combination,Bottom,-600,...)
- `Joint Reactions`: Story, Label, Unique Name, Output Case, Case Type, FX,FY,FZ(kN), MX,MY,MZ(kN-m): (Base,1,1,ULS1,Combination,0,0,700,0,0,0)

- [ ] **Step 1:** run `python tools/build_schema.py` with ETABS open -> writes `etabs_python/schema.json`; check `schema["Frame Assignments - Summary"]["Design Type"] == "Type"`.
- [ ] **Step 2: Failing tests** `tests/test_sources.py`

```python
import pandas as pd
import pytest
from etabs_python.sources import ExcelSource, FileSource, coerce_types, header_to_key, write_snapshot
from etabs_python.errors import TableNotFound


def test_header_mapping():
    assert header_to_key("Frame Assignments - Summary", "Design Type") == "Type"
    assert header_to_key("Frame Section Property Definitions - Summary", "I33 Modifier") == "I3Mod"
    assert header_to_key("Some Unknown Table", "Is Auto Point?") == "IsAutoPoint"


def test_excel_source_reads_keys_units_and_ids(export_xlsx):
    src = ExcelSource(export_xlsx)
    assert "Frame Assignments - Summary" in src.tables()
    df = src.table("Frame Assignments - Summary")
    assert list(df.columns[:6]) == ["Story", "Label", "UniqueName", "Type", "Length", "AnalysisSect"]
    assert df["UniqueName"].tolist() == ["10", "11", "12"]
    assert df["Length"].dtype.kind == "f" and df.attrs["units"]["Length"] == "m"
    assert df.attrs["table"] == "Frame Assignments - Summary"


def test_excel_source_filters_output_case(export_xlsx):
    src = ExcelSource(export_xlsx)
    df = src.table("Element Forces - Beams", combos=["ULS2"])
    assert set(df["OutputCase"]) == {"ULS2"} and len(df) == 9


def test_excel_source_missing_table(export_xlsx):
    with pytest.raises(TableNotFound):
        ExcelSource(export_xlsx).table("Story Drifts")


def test_coerce_types_keeps_ids():
    df = coerce_types(pd.DataFrame({"Label": ["65"], "UniqueName": [83], "X": ["1.5"], "Shape": ["Rect"]}))
    assert df["Label"][0] == "65" and df["UniqueName"][0] == "83"
    assert df["X"][0] == 1.5 and df["Shape"][0] == "Rect"


def test_file_source_roundtrip(tmp_path):
    df = pd.DataFrame({"UniqueName": ["10"], "Length": [8.0]})
    df.attrs = {"table": "Frame Assignments - Summary", "units": {"Length": "m"}}
    write_snapshot(tmp_path, {"Frame Assignments - Summary": df}, {"force": "kN"})
    src = FileSource(tmp_path)
    back = src.table("Frame Assignments - Summary")
    assert back["UniqueName"][0] == "10" and back["Length"][0] == 8.0
    assert back.attrs["units"] == {"Length": "m"}
```

- [ ] **Step 3:** implement `conftest.py` (openpyxl writer from a dict of `{table: (headers, units, rows)}`; `etabs_live` tries `LiveSource()` and skips on any exception) and `sources.py`.
- [ ] **Step 4:** run -> PASS. **Step 5:** commit `feat(sources): excel, file and live table sources`.

### Task 4: Engine, operations registry, progress

**Files:** Create `etabs_python/engine.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: `sources.TableSource`, `sources.write_snapshot`, `units.Units`, `units.convert_table`, errors.
- Produces:
  - `operation(name, needs="tables", slow=False)` decorator; registry `OPERATIONS: Dict[str, Operation]`; `Operation(name, func, needs, slow, doc, params)`.
  - `Progress(callback=None, use_tqdm=False, heartbeat=1.0)`: `start(total=None, desc="")`, `step(n=1, msg="")`, `close()`, properties `done`, `total`, `message`, `elapsed`. Callback signature `callback(done, total, message, elapsed)`. Heartbeat thread calls callback / refreshes tqdm every `heartbeat` seconds while started.
  - `Context(engine, progress)`: `.source`, `.units`, `.progress`, `.table(name, cases=None, combos=None)`, `.optional_table(name) -> DataFrame` (empty on `TableNotFound`).
  - `Engine(source, units=None)`: `.ops() -> List[dict]`, `.table(name, cases=None, combos=None, progress=None)`, `.run(op, progress=False, out=None, **params)`, `.export(folder, tables) -> List[Path]`.
  - Live multi-case reads: when `source.kind == "live"` and `len(cases)+len(combos) > 1`, read one name at a time and `progress.step(msg=f"{table}: {name}")`.
  - `run`: unknown op -> `EngineError`; `needs == "live"` and `source.kind != "live"` -> `SourceError`; bad params -> `EngineError` (from `inspect.signature(func).bind(ctx, **params)`); `progress` may be `False`, `True` (tqdm), a callable, or a `Progress`; `out` suffix `.xlsx/.csv/.json` writes DataFrame results (`.json` also for dict/list results).

- [ ] **Step 1: Failing tests** `tests/test_engine.py`

```python
import pandas as pd
import pytest
from etabs_python import Engine, ExcelSource, FileSource, Progress, operation
from etabs_python.errors import EngineError, SourceError


@operation("_test_echo")
def _echo(ctx, value, times=1):
    """Echo value."""
    ctx.progress.start(total=times, desc="echo")
    for i in range(times):
        ctx.progress.step(msg=f"{i + 1}/{times}")
    return pd.DataFrame({"value": [value] * times})


@operation("_test_live_only", needs="live")
def _live_only(ctx):
    return 1


def test_ops_listing(export_xlsx):
    ops = {o["name"]: o for o in Engine(ExcelSource(export_xlsx)).ops()}
    assert ops["_test_echo"]["params"][0]["name"] == "value"
    assert ops["_test_echo"]["doc"] == "Echo value."
    assert "beam_forces_by_zone" in ops and ops["beam_forces_by_zone"]["slow"] is True


def test_run_validates(export_xlsx):
    eng = Engine(ExcelSource(export_xlsx))
    with pytest.raises(EngineError):
        eng.run("nope")
    with pytest.raises(EngineError):
        eng.run("_test_echo", wrong=1)
    with pytest.raises(SourceError):
        eng.run("_test_live_only")


def test_progress_callback_and_out(export_xlsx, tmp_path):
    calls = []
    eng = Engine(ExcelSource(export_xlsx))
    df = eng.run("_test_echo", value="x", times=3, progress=lambda d, t, m, e: calls.append((d, t, m)),
                 out=tmp_path / "r.csv")
    assert len(df) == 3 and (tmp_path / "r.csv").exists()
    assert (3, 3, "3/3") in calls


def test_progress_tqdm_does_not_crash(export_xlsx):
    Engine(ExcelSource(export_xlsx)).run("_test_echo", value=1, times=2, progress=True)


def test_engine_table_converts_units(export_xlsx):
    eng = Engine(ExcelSource(export_xlsx))
    off = eng.table("Frame Assignments - End Length Offsets")
    assert off["OffsetI"].tolist()[0] == pytest.approx(0.25) and off.attrs["units"]["OffsetI"] == "m"
    mm = Engine(ExcelSource(export_xlsx), units=__import__("etabs_python").Units(length="mm"))
    assert mm.table("Frame Assignments - Summary")["Length"][0] == pytest.approx(8000.0)


def test_export_roundtrip(export_xlsx, tmp_path):
    eng = Engine(ExcelSource(export_xlsx))
    eng.export(tmp_path, ["Frame Assignments - Summary", "Element Forces - Beams"])
    back = Engine(FileSource(tmp_path))
    a = eng.table("Element Forces - Beams")
    b = back.table("Element Forces - Beams")
    assert b["M3"].tolist() == pytest.approx(a["M3"].tolist()) and b["UniqueName"].tolist() == a["UniqueName"].tolist()
```

- [ ] **Step 2:** run -> FAIL. **Step 3:** implement. **Step 4:** run -> PASS (after Task 6 for the `beam_forces_by_zone` listing assertion; run the other tests now). **Step 5:** commit `feat(engine): operation registry, progress, export`.

### Task 5: Model operations

**Files:** Create `etabs_python/ops_model.py`, `tests/test_ops_offline.py` (model part)

**Interfaces:**
- Consumes: `engine.operation`, `Context.table/optional_table`.
- Produces operations (all `needs="tables"`): `stories()`, `story_at(z, tol=1e-3)`, `frames(story=None)`, `beams(story=None)`, `columns(story=None)`, `shells(story=None)`, `walls(story=None)`, `slabs(story=None)`, `points(story=None)`, `materials()`, `frame_properties()`, `area_properties()`. Module-level helpers `story_from_elevation(z, elevations, names, base, tol, base_name)`, `apply_modifiers(df, obj_mods, mod_cols)`, constants `FRAME_MODS`, `AREA_MODS`.
- `stories`: `Story Definitions` is top-first; reverse, `Elevation = BSElev + cumsum(Height)`; `attrs["base_name"]`, `attrs["base_elevation"]`; first tower only when a `Tower` column exists.

- [ ] **Step 1: Failing tests**

```python
import pytest
from etabs_python import Engine, ExcelSource


@pytest.fixture
def eng(export_xlsx):
    return Engine(ExcelSource(export_xlsx))


def test_stories_and_story_at(eng):
    st = eng.run("stories")
    assert st["Story"].tolist() == ["FL1", "FL2"] and st["Elevation"].tolist() == pytest.approx([4.0, 7.5])
    assert st.attrs["base_name"] == "Base"
    assert eng.run("story_at", z=[0.0, 2.0, 4.0, 4.0005, 5.0, 7.5, 9.0]) == ["Base", "FL1", "FL1", "FL1", "FL2", "FL2", None]
    assert eng.run("story_at", z=4.0) == "FL1"


def test_story_at_matches_point_stories(eng):
    pts = eng.run("points")
    assert eng.run("story_at", z=pts["Z"].tolist()) == pts["Story"].tolist()


def test_element_lists(eng):
    assert eng.run("beams")["UniqueName"].tolist() == ["10", "11"]
    assert eng.run("columns", story="FL1")["Label"].tolist() == ["C1"]
    assert eng.run("walls")["Label"].tolist() == ["W1"]
    assert eng.run("slabs")["Label"].tolist() == ["F1"]


def test_frame_properties(eng):
    fp = eng.run("frame_properties").set_index("UniqueName")
    assert fp.loc["10", "Sect_I3Mod"] == 0.7 and fp.loc["10", "Obj_I3Mod"] == 0.5
    assert fp.loc["10", "I3Mod"] == pytest.approx(0.35) and fp.loc["10", "JMod"] == pytest.approx(0.1)
    assert fp.loc["11", "I3Mod"] == pytest.approx(0.7) and fp.loc["12", "I3Mod"] == 1.0
    assert fp.loc["10", "t3"] == 600 and fp.loc["10", "Area"] == pytest.approx(180000.0)
    assert fp.loc["10", "OffsetI"] == pytest.approx(0.25) and fp.loc["10", "Length"] == 8.0
    assert fp.loc["10", "E1"] == 32000 and fp.loc["10", "Fc"] == 30 and fp.loc["10", "MatType"] == "Concrete"
    assert fp.attrs["units"]["I33"] == "mm⁴" and fp.attrs["units"]["Length"] == "m"


def test_area_properties(eng):
    ap = eng.run("area_properties").set_index("UniqueName")
    assert ap.loc["20", "m11Mod"] == pytest.approx(0.125) and ap.loc["20", "f11Mod"] == 1.0
    assert ap.loc["21", "m11Mod"] == 1.0 and ap.loc["20", "TotalThick"] == 200
```

- [ ] **Step 2:** FAIL. **Step 3:** implement (port logic from root `etabs_model.py`: `_assignments`, `apply_modifiers`, `materials`, section dims merge; drop duplicate columns before merges; `MatType` rename; merged `attrs["units"]` = union of the tables' converted units). **Step 4:** PASS. **Step 5:** commit `feat(ops): model operations`.

### Task 6: Force operations

**Files:** Create `etabs_python/ops_forces.py`; extend `tests/test_ops_offline.py`

**Interfaces:**
- Produces operations: `beam_forces(names=None, cases=None, combos=None)` (slow), `column_forces(...)` (slow), `pier_forces(piers=None, stories=None, cases=None, combos=None)`, `joint_reactions(names=None, cases=None, combos=None)` (slow), `beam_forces_by_zone(names=None, cases=None, combos=None, zones=(0.25, 0.5, 0.25), envelope=False)` (slow). Helper `zone_envelope(forces, lengths, zones, envelope, tol=1e-4) -> DataFrame` with columns `UniqueName, [OutputCase, StepType], Zone, ZoneStart, ZoneEnd, P_max, P_min, ..., M3_max, M3_min`; zone names `Start/Middle/End` for 3 zones else `Z1..Zn`.

- [ ] **Step 1: Failing tests**

```python
def test_beam_forces_by_zone(eng):
    z = eng.run("beam_forces_by_zone", combos=["ULS1", "ULS2"])
    b1 = z[(z["UniqueName"] == "10") & (z["OutputCase"] == "ULS1")]
    assert b1["Zone"].tolist() == ["Start", "Middle", "End"]
    assert b1["M3_min"].tolist() == [0, 2, 6] and b1["M3_max"].tolist() == [2, 6, 8]
    assert b1["V2_max"].tolist() == [10, 8, 4]
    b2 = z[(z["UniqueName"] == "11")]
    assert b2["M3_max"].tolist() == [15, 45, 60] and b2["M3_min"].tolist() == [0, 15, 45]
    assert {"Story", "Label", "Length", "AnalysisSect"} <= set(z.columns)


def test_beam_forces_by_zone_envelope_and_names(eng):
    z = eng.run("beam_forces_by_zone", names=["10"], combos=["ULS1", "ULS2"], envelope=True)
    assert len(z) == 3 and z["M3_min"].tolist() == [-2, -6, -8] and z["M3_max"].tolist() == [2, 6, 8]
    assert "OutputCase" not in z.columns


def test_zones_must_sum_to_one(eng):
    import pytest
    from etabs_python.errors import EngineError
    with pytest.raises((ValueError, EngineError)):
        eng.run("beam_forces_by_zone", zones=[0.3, 0.3])


def test_other_force_tables(eng):
    assert eng.run("column_forces")["P"].tolist() == [-100, -90]
    assert eng.run("pier_forces", piers="P1")["Location"].tolist() == ["Top", "Bottom"]
    assert eng.run("joint_reactions")["FZ"].sum() == 700
```

- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS; also rerun `tests/test_engine.py`. **Step 5:** commit `feat(ops): force operations and beam zone envelope`.

### Task 7: Live operations and final package exports

**Files:** Create `etabs_python/ops_live.py`; finalize `etabs_python/__init__.py`; Create `tests/test_live.py` (selection part)

**Interfaces:**
- Consumes: `LiveSource.sap_model`, `LiveSource.bridge`, `LiveSource.present_units`, `units.convert_value`, `parser.parse_json_payload`.
- Produces operations (`needs="live"`): `name_from_label(label, story, kind="frame") -> str`, `select(names=None, kind="frame", labels=None, story=None, clear=True) -> List[str]`, `selected() -> DataFrame[Type, UniqueName]`, `clear_selection() -> None`, `frame_info(name) -> dict` (coordinates and length in general length unit), `execute_batch(payload) -> dict` (slow; raises `ModelLockedError` when locked). `kind` aliases: frame/beam/column/brace, area/shell/wall/slab, point/joint.

- [ ] **Step 1: Failing live tests**

```python
import pytest
from etabs_python import Engine


@pytest.fixture(scope="module")
def eng(etabs_live):
    return Engine(etabs_live)


def test_select_by_label_and_name_restores_selection(eng):
    before = eng.run("selected")
    beams, cols = eng.run("beams"), eng.run("columns")
    b = beams.iloc[0]
    assert eng.run("name_from_label", label=b["Label"], story=b["Story"], kind="beam") == b["UniqueName"]
    got = eng.run("select", labels=[b["Label"]], story=b["Story"], kind="beam")
    got += eng.run("select", names=[cols.iloc[0]["UniqueName"]], kind="frame", clear=False)
    sel = eng.run("selected")
    assert sorted(sel["UniqueName"]) == sorted(got) and set(sel["Type"]) == {"Frame"}
    eng.run("clear_selection")
    assert eng.run("selected").empty
    for r in before.itertuples():
        kind = {"Frame": "frame", "Area": "area", "Point": "point"}.get(r.Type)
        if kind:
            eng.run("select", names=[r.UniqueName], kind=kind, clear=False)


def test_frame_info_matches_table(eng):
    c = eng.run("columns").iloc[0]
    info = eng.run("frame_info", name=c["UniqueName"])
    assert info["story"] == c["Story"] and info["section"] == c["AnalysisSect"] and info["type"] == "Column"
    assert info["length"] == pytest.approx(c["Length"], abs=1e-3)
```

- [ ] **Step 2:** with ETABS open, FAIL. **Step 3:** implement; finalize `__init__.py` as in Task 1 Step 5. **Step 4:** PASS (`python -m pytest tests/test_live.py -k "select or frame_info" -v`). **Step 5:** commit `feat(ops): live selection, frame_info, execute_batch`.

### Task 8: Server

**Files:** Create `etabs_python/server.py`, `tests/test_server.py`; Delete root `server.py`, `start_server.bat`

**Interfaces:**
- Consumes: `Engine`, `ExcelSource`, `LiveSource`, `Units`, `Progress`, `OPERATIONS`, errors.
- Produces: FastAPI `app`; `Worker` (single thread, `submit(fn) -> concurrent.futures.Future`, COM MTA initialized in the thread when `pythoncom` is available); endpoints per spec section 8; `main()` for `python -m etabs_python.server --host 127.0.0.1 --port 8000`.
- Result JSON: `{"columns": [...], "rows": [[...]], "units": {...}}` for DataFrames (NaN -> null), `{"result": value}` otherwise.
- Error mapping: `TableNotFound` 404, `ModelLockedError`/`ResultNotAvailable` 409, other `EngineError` 400, unknown source/job/op 404.

- [ ] **Step 1: Failing tests**

```python
import time
from fastapi.testclient import TestClient
from etabs_python.server import app

client = TestClient(app)


def _excel_source(path):
    with open(path, "rb") as f:
        r = client.post("/sources/excel", files={"file": ("export.xlsx", f)})
    assert r.status_code == 200
    return r.json()["source_id"]


def test_ops_endpoint():
    names = {o["name"] for o in client.get("/ops").json()}
    assert {"stories", "beam_forces_by_zone", "select"} <= names


def test_fast_op_returns_rows(export_xlsx):
    sid = _excel_source(export_xlsx)
    r = client.post("/run/beams", json={"source_id": sid, "params": {}})
    body = r.json()
    assert r.status_code == 200 and body["columns"][:3] == ["Story", "Label", "UniqueName"]
    assert [row[2] for row in body["rows"]] == ["10", "11"] and body["units"]["Length"] == "m"


def test_units_override(export_xlsx):
    sid = _excel_source(export_xlsx)
    r = client.post("/run/beams", json={"source_id": sid, "params": {}, "units": {"length": "mm"}})
    i = r.json()["columns"].index("Length")
    assert r.json()["rows"][0][i] == 8000


def test_slow_op_job_lifecycle(export_xlsx):
    sid = _excel_source(export_xlsx)
    r = client.post("/run/beam_forces_by_zone", json={"source_id": sid, "params": {"combos": ["ULS1"]}})
    assert r.status_code == 202
    job = r.json()["job_id"]
    for _ in range(100):
        s = client.get(f"/jobs/{job}").json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert s["status"] == "done", s
    res = client.get(f"/jobs/{job}/result").json()
    assert "M3_max" in res["columns"]
    x = client.get(f"/jobs/{job}/result", params={"format": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_errors(export_xlsx):
    sid = _excel_source(export_xlsx)
    assert client.post("/run/nope", json={"source_id": sid, "params": {}}).status_code == 404
    assert client.post("/run/select", json={"source_id": sid, "params": {}}).status_code == 400
    assert client.post("/run/beams", json={"source_id": "missing", "params": {}}).status_code == 404
    assert client.post("/run/beams", json={"source_id": sid, "params": {"bad": 1}}).status_code == 400
    assert client.delete(f"/sources/{sid}").status_code == 200
```

- [ ] **Step 2:** FAIL. **Step 3:** implement; `git rm server.py start_server.bat`. **Step 4:** PASS. **Step 5:** commit `feat(server): FastAPI engine server with background jobs`.

### Task 9: Live verification tests

**Files:** extend `tests/test_live.py`

- [ ] **Step 1: Tests** (all read-only; skip when ETABS not running):
  - `stories` elevations equal `Story.GetStories_2` elevations converted from N-mm to m (set present units with `LiveSource.present_units(9)`), base name equals `Tower and Base Story Definitions`.
  - `story_at(points.Z) == points.Story` for all points.
  - `frame_properties`: for up to 50 frames with any `Sect_*` or `Obj_*` != 1, `Obj_*` equals `FrameObj.GetModifiers`, `Sect_*` equals `PropFrame.GetModifiers(AnalysisSect)`, effective = product; `area_properties` same with `AreaObj`/`PropArea`.
  - `beam_forces(names=[b], cases=[case])` equals `Results.FrameForce(b, 0)` per station: station (m) vs `ObjSta/1000`, `M3` (kN-m) vs `M3/1e6` within 1e-3 relative + 1e-3 absolute, with first finished case from `Analyze.GetCaseStatus` (status 4); skip if none.
  - `joint_reactions(cases=[case])["FZ"].sum()` equals `Results.BaseReact()` FZ / 1000 within 1e-3 relative.
  - `beam_forces_by_zone(names=[b], cases=[case])` equals a manual pandas computation from `beam_forces`.
  - Excel vs live when env `ETABS_EXPORT_XLSX` points to a workbook of the same model: for `Frame Assignments - Summary` (`Length`), `Frame Section Property Definitions - Summary` (`Area`, `I33`) and `Element Forces - Beams` for one combo present in the workbook (`Station`, `M3` of one beam), converted values match within 1e-3 relative + 1e-6 absolute.
- [ ] **Step 2:** run `python -m pytest tests/test_live.py -v` with ETABS open (set `ETABS_EXPORT_XLSX`) -> PASS. **Step 3:** commit `test: live verification against OAPI and Excel export`.

### Task 10: Docs and cleanup

**Files:** Create `docs/ENGINE_GUIDE.md`; rewrite `AGENTS.md`, `README.md`; delete root `__init__.py`, `etabs_model.py`, `test_etabs_model.py`, `test_live_readonly.py`, `example_pull_inject.py`.

- [ ] **Step 1:** `ENGINE_GUIDE.md` sections: Overview & concepts; Install; Quick start (Python: live, Excel, File; progress; out); Units (table of defaults, section vs general rule, stress rule, unsupported units); Sources (Excel export instructions and required tables per operation; FileSource layout; LiveSource behaviour incl. display selection and N-mm reading); Operations reference (every op: params, needs, slow, output columns, example); HTTP API (each endpoint with curl/JSON example, job polling loop in JS and Python); Errors; Extending (template `@operation`, checklist, test pattern with the synthetic fixture, where to register); Schema regeneration; Testing (offline/live, env var); Conventions (English, field keys, ids as str, py3.8).
- [ ] **Step 2:** `AGENTS.md` short conventions + pointer; `README.md` quick start + link to guide.
- [ ] **Step 3:** delete obsolete root files; `python -m pytest -v` (offline all pass, live pass with ETABS) ; `grep -rn "etabs_model\|data_bridge\|data_parser\|Object_rename" --include=*.py --include=*.md .` only matches historical specs/plans.
- [ ] **Step 4:** commit `docs: engine guide; remove legacy root modules`; push.

