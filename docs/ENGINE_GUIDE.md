# etabs_python Engine Guide

Reference for people and coding agents who use or extend the engine. Everything here matches the code in
`etabs_python/`; when they disagree, the code and its tests win and this guide must be fixed.

## 1. Concepts

| Concept | What it is | Where |
|---|---|---|
| **Source** | Where ETABS tables come from: a running ETABS (`LiveSource`), an ETABS Excel export (`ExcelSource`), or a snapshot folder written by the engine (`FileSource`). | `sources.py` |
| **Table** | A pandas DataFrame named after the ETABS database table (e.g. `Frame Assignments - Summary`). Columns are ETABS **field keys** (`UniqueName`, `AnalysisSect`, `AMod`), `df.attrs["units"]` maps column -> unit. | `sources.py` |
| **Units** | Target SI unit system. The engine converts every table right after reading it. | `units.py` |
| **Operation** | A registered function (`@operation`) that turns tables into a result (DataFrame, list, dict). | `ops_*.py` |
| **Engine** | Holds a source + units, runs operations, reports progress, exports snapshots. | `engine.py` |
| **Server** | FastAPI app exposing sources, operations and background jobs over HTTP. | `server.py` |

Data flow:

```
LiveSource / ExcelSource / FileSource --table()--> raw table (native units)
        --Engine.table(): convert_table(units)--> converted table
        --operation(ctx, **params)--> result --> Python caller | HTTP JSON/CSV/XLSX
```

## 2. Install

Windows with Python 3.8+. ETABS (v22 tested) is only needed for `LiveSource`.

```bash
pip install -e .[test]
```

Dependencies: comtypes, pywin32, pandas, openpyxl, tqdm, fastapi, uvicorn, python-multipart (tests: pytest, httpx).

## 3. Quick start (Python)

```python
from etabs_python import Engine, LiveSource, ExcelSource, FileSource, Units

# Live model (ETABS open)
eng = Engine(LiveSource())                     # LiveSource(pid=1234) to pick a process

# Or an Excel export, no ETABS needed
eng = Engine(ExcelSource("model_export.xlsx"))
eng = Engine(ExcelSource(["model_export.xlsx", "Story.xlsx", "Joint Reaction.xlsx"]))   # several exports

eng.ops()                                      # list operations, params, docs
st = eng.run("stories")                        # DataFrame: Story, Height, Elevation
eng.run("story_at", z=[3.5, 7.0])              # ['FL1', 'FL2']
fp = eng.run("frame_properties")               # 1 row per frame, sections, modifiers, materials
z = eng.run("beam_forces_by_zone", combos=["ULS1", "ULS2"], progress=True)   # tqdm bar
eng.run("beam_forces_by_zone", combos=["ULS1"], envelope=True, out="zones.xlsx")

raw = eng.table("Joint Reactions", combos=["ULS1"])   # any table, converted
eng.export("snapshot/", ["Frame Assignments - Summary", "Element Forces - Beams"])
eng2 = Engine(FileSource("snapshot/"))                # reuse without ETABS or Excel

eng_mm = Engine(ExcelSource("model_export.xlsx"), units=Units(force="N", length="mm"))
```

`progress` accepts `False` (silent), `True` (tqdm bar in the terminal), a callable
`fn(done, total, message, elapsed_seconds)`, or a `Progress` instance. A heartbeat re-emits every second, so a
long ETABS call still shows elapsed time.

`out=` also writes the result: DataFrames to `.xlsx`, `.csv` or `.json`; other results to `.json` only.

## 4. Units

Defaults: `Units(force="kN", length="m", section="mm", stress="MPa")`.

| Group | Default | Allowed | Examples |
|---|---|---|---|
| force | kN | N, kN | P, V2, FZ |
| general length | m | mm, m | Length, Station, X/Y/Z, Height, Elevation, OffsetI |
| section length | mm | mm, m | t3, t2, Thickness, Area (mm²), I33 (mm⁴), S33 (mm³) |
| stress | MPa | kPa, MPa | E1, G12, Fc, Fy, Fu |

Derived units follow force and length: moments `kN-m`, line loads `kN/m`, area loads `kN/m²`, unit weight `kN/m³`.

Rules (`units.py`):
- **Section length** applies to every column of tables whose name starts with `Frame Section Property Definitions`,
  `Area Section Property Definitions`, `Slab Property Definitions`, `Wall Property Definitions`,
  `Deck Property Definitions`, `Reinforcing Bar Sizes`. All other tables use general length (pier/spandrel widths too).
- **Stress** applies when the source unit is `Pa`, `kPa` or `MPa`, or a force/length² column is in a
  `Material Properties ...` table. Other force/length² columns (area loads) stay force/area (`kN/m²`).
- Units not made only of force and length (`kg`, `ton-m²`, `sec`, `deg`, `rad`, `1/C`, `cyc/sec`) are kept unchanged.
- Dimensionless units (`mm/mm`) are kept unchanged.
- Imperial units (`kip`, `lb`, `in`, `ft`, `ksi`, `psi`, ...) raise `UnitError`.
- `df.attrs["units"]` always holds the units of the returned values.

## 5. Sources

### 5.1 ExcelSource

Export from ETABS: *File > Export > ETABS Tables to Excel* (or Display > Show Tables > Export to Excel), any
display units (SI). Layout per sheet: row 1 `TABLE:  <table name>`, row 2 headers, row 3 units, data from row 4.
The table name is read from row 1 (sheet names are truncated by Excel). Headers are mapped to field keys with
`etabs_python/schema.json`.

Workbooks load read-only; a sheet is parsed on first use and cached. Opening a 130 MB export takes about 1 s;
parse only the tables you need.

`cases` / `combos` filter the `OutputCase` column. Results exist only for the cases/combos you exported.

**Several files:** `ExcelSource([path1, path2, ...])` exposes the tables of all files as one source.
- A results table (has `OutputCase`) found in several files is concatenated; later files are converted to the
  units of the first file (`SourceError` if the units are incompatible). Use this to split large force exports by
  combination.
- Any other table found in several files (e.g. `Program Control`) comes from the first file in the list.
- `source.table_files()` shows which files contain each table.

**Precision:** ETABS writes values with the decimals set in its display format. An export shown with 0 decimals in
kN gives reactions rounded to 1 kN (measured: max 0.5 kN difference against `LiveSource`). Increase the displayed
decimals before exporting results you need precisely, or use `LiveSource`, which always reads in N-mm.

**Case and combination names** are matched exactly: repeated spaces, `%`, `~`, `/`, `(`, `)` are significant
(`"1_ULSE1   1.35D+1.5L"` is not `"1_ULSE1 1.35D+1.5L"`). Names passed as numbers are compared as strings.

Tables required per operation:

| Operation | Tables to export |
|---|---|
| `stories`, `story_at` | Story Definitions, Tower and Base Story Definitions |
| `frames`, `beams`, `columns` | Frame Assignments - Summary |
| `shells`, `walls`, `slabs` | Area Assignments - Summary |
| `points` | Point Object Connectivity |
| `materials` | Material Properties - General (+ Basic Mechanical Properties, Concrete Data, Steel/Rebar/Tendon Data) |
| `frame_properties` | Frame Assignments - * (Summary required, Property Modifiers recommended), Frame Section Property Definitions - Summary (+ shape tables for t3/t2), material tables |
| `area_properties` | Area Assignments - * (Summary required, Stiffness Modifiers recommended), Area Section Property Definitions - Summary, Slab/Wall/Deck Property Definitions, material tables |
| `beam_forces`, `beam_forces_by_zone` | Element Forces - Beams (+ Frame Assignments - Summary for zones) |
| `column_forces` | Element Forces - Columns |
| `pier_forces` | Pier Forces |
| `joint_reactions` | Joint Reactions |

Missing tables raise `TableNotFound` naming the table.

Exports contain the ETABS license number (`Program Control` sheet) and project data: never commit them.

### 5.2 LiveSource

- Attaches through `connection.get_active_etabs(pid)`. Without `pid` it uses the active ETABS instance; if ETABS
  is not registered as active (common when a model was opened by double-clicking the .EDB), it tries every
  running `ETABS.exe` process id and attaches to the first that answers. Pass `pid` when several ETABS are open.
- Reads `DatabaseTables.GetTableForDisplayArray` with present units temporarily set to **N-mm** (display
  arrays are rounded to 2 decimals, so small units keep precision) and restores the previous units.
- `cases` / `combos` set the tables' display selection (`SetLoadCasesSelectedForDisplay` /
  `SetLoadCombinationsSelectedForDisplay`); unknown names raise `EngineError`. This changes the display
  selection in the ETABS UI.
- When several cases/combos are requested, `Engine.table` reads them one at a time and steps progress per name.
- Results tables with no rows raise `ResultNotAvailable` (analysis not run, case not finished).
- Exposes `.sap_model` (raw OAPI), `.bridge` (`EtabsDataBridge` write helpers) and
  `present_units(code)` (context manager).

### 5.3 FileSource

Folder written by `Engine.export(folder, tables)`: one `<table>.csv` per table (invalid filename characters
replaced by `_`) and `index.json` (`{"units_system": {...}, "tables": {name: {"file", "units"}}}`). Values are
stored already converted, and the stored units are converted again if the reading engine uses other units.

## 6. Operations reference

`needs`: `tables` = any source, `live` = `LiveSource` only. `slow`: runs as a background job on the server.
List parameters accept a single string or a list. All DataFrames carry `attrs["units"]`.

### 6.1 Model (`ops_model.py`)

| Operation | Params | Result |
|---|---|---|
| `stories` | - | DataFrame `Story, Height, Elevation` bottom to top; `Elevation` = top of story = base elevation + cumulative heights. `attrs["base_name"]`, `attrs["base_elevation"]`. First tower only. |
| `story_at` | `z` (number or list, general length), `tol=1e-3` | Story name (or list). Story X contains (elevation of the story below, elevation of X]; base elevation -> base story name; outside -> `None`. |
| `frames` / `beams` / `columns` | `story=None` | Frame Assignments - Summary rows (`Type` = Beam / Column). |
| `shells` / `walls` / `slabs` | `story=None` | Area Assignments - Summary rows (`PropType` = Wall / Slab). |
| `points` | `story=None` | Point Object Connectivity rows (`UniqueName, Story, X, Y, Z`). |
| `materials` | - | 1 row per material: `Material, MatType, Grade, UnitWeight, E1, G12, U12, A1, Fc, Fy, Fu`. |
| `frame_properties` | - | 1 row per frame: every one-row-per-object `Frame Assignments - *` table, section summary (`Material, Shape, Area, I33, I22, J, ...`) and `t3, t2, tf, tw`, modifiers, material columns. |
| `area_properties` | - | 1 row per area: `Area Assignments - *`, `SectType, AnalType, Material, TotalThick`, modifiers, material columns. |

Modifiers (frame: `AMod, A2Mod, A3Mod, JMod, I2Mod, I3Mod, MMod, WMod`; area: `f11Mod, f22Mod, f12Mod, m11Mod,
m22Mod, m12Mod, v13Mod, v23Mod, MMod, WMod`):
- `Sect_<m>`: modifier defined on the section/property (1 when missing),
- `Obj_<m>`: modifier assigned to the object (1 when the object is not in the modifier table),
- `<m>` = `Sect_<m>` x `Obj_<m>` (effective value used by ETABS).

Assignment tables with several rows per object (loads) are skipped; read them with `engine.table(name)`.

### 6.2 Forces (`ops_forces.py`)

| Operation | Params | Slow | Result |
|---|---|---|---|
| `beam_forces` | `names, cases, combos` | yes | Element Forces - Beams: `UniqueName, OutputCase, CaseType, [StepType], Station, P, V2, V3, T, M2, M3, Element, ElemStation` |
| `column_forces` | `names, cases, combos` | yes | Element Forces - Columns |
| `pier_forces` | `piers, stories, cases, combos` | no | Pier Forces: `Story, Pier, OutputCase, Location (Top/Bottom), P..M3` |
| `joint_reactions` | `names, cases, combos` | yes | Joint Reactions: `UniqueName, OutputCase, FX, FY, FZ, MX, MY, MZ` |
| `base_reactions` | `cases, combos` | no | Base Reactions: resultant `FX, FY, FZ, MX, MY, MZ, X, Y, Z` |
| `beam_forces_by_zone` | `names, cases, combos, zones=(0.25, 0.5, 0.25), envelope=False` | yes | per beam x zone (x `OutputCase`, `StepType` unless `envelope`): `UniqueName, Story, Label, Length, AnalysisSect, Zone, ZoneStart, ZoneEnd, P_max, P_min, V2_max, ..., M3_min` |
| `story_drifts` | `stories, cases, combos` | no | Point-based story drift ratios and governing coordinates |
| `story_max_over_average_drifts` | `stories, cases, combos` | no | Story maximum drift, average drift and torsional ratio |
| `diaphragm_max_over_average_drifts` | `stories, cases, combos` | no | Diaphragm maximum/average drift and governing point |
| `story_forces` | `stories, cases, combos` | no | Story `P, VX, VY, T, MX, MY` at top/bottom |
| `story_stiffness` | `stories, cases, combos` | no | Story shear, drift, stiffness and irregularity flags |
| `modal_periods` | `cases, modes` | no | Period, frequency, circular frequency and eigenvalue |
| `modal_mass_participation` | `cases, modes` | no | Per-mode and cumulative `UX..RZ` mass ratios |
| `modal_load_participation` | `cases` | no | Static/dynamic load participation percentages |
| `modal_participation_factors` | `cases, modes` | no | Participation factors, modal mass and stiffness |
| `modal_direction_factors` | `cases, modes` | no | Modal direction factors `UX, UY, UZ, RZ` |
| `centers_of_mass_and_rigidity` | `stories` | no | Story mass, centers of mass and rigidity |
| `tributary_area_llrf` | `stories, names` | no | Tributary area and live-load reduction factor |

`beam_forces_by_zone` rules: relative position = `Station / Length` (Station measured from the I-end of the
object, so rigid end offsets are included in the length); zones are consecutive ratios that must sum to 1
(`EngineError` otherwise); zone names are `Start, Middle, End` for 3 zones, else `Z1..Zn`; a station on a
boundary belongs to both zones. `cases=None, combos=None` uses whatever the source provides (all exported
cases for Excel, the current display selection for live).

### 6.3 Live (`ops_live.py`)

| Operation | Params | Result |
|---|---|---|
| `name_from_label` | `label, story, kind="frame"` | UniqueName |
| `select` | `names=None, kind="frame", labels=None, story=None, clear=True` | list of selected UniqueNames (by names + kind, or labels + story; `story` one name or list matching labels) |
| `selected` | - | DataFrame `Type (Point/Frame/Area/...), UniqueName` |
| `clear_selection` | - | `None` |
| `frame_info` | `name` | dict: `name, label, story, section, type, point_i, point_j, coord_i, coord_j, length, length_unit` |
| `execute_batch` (slow) | `payload` | report dict. Writes to the model: `create_groups, frame_sections, rename, assign_groups, assign_sections` (see `parser.parse_json_payload`). Raises `ModelLockedError` if locked. |

`kind` aliases: `frame, beam, column, brace` -> frame; `area, shell, wall, slab, floor` -> area; `point, joint` -> point.

## 7. HTTP API (`server.py`)

Start: `python -m etabs_python.server --host 127.0.0.1 --port 8000`. Interactive docs: `/docs`.
All engine calls run on one worker thread; requests touching ETABS are serialized.

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/ops` | - | `[{name, needs, slow, doc, params: [{name, default, required}]}]` |
| POST | `/sources/live` | `{"pid": null}` | `{"source_id", "kind": "live"}` |
| POST | `/sources/excel` | multipart `file` (.xlsx), repeat the field for several files (order = priority) | `{"source_id", "kind": "excel", "tables": [...]}` |
| DELETE | `/sources/{source_id}` | - | `{"deleted": id}` |
| POST | `/run/{op}` | `{"source_id", "params": {...}, "units": {"force": "kN", ...}}` | fast op: 200 `{"columns", "rows", "units"}` or `{"result"}`; slow op: 202 `{"job_id"}` |
| GET | `/jobs/{job_id}` | - | `{"job_id", "op", "status": "queued/running/done/error", "done", "total", "message", "elapsed", "error"}` |
| GET | `/jobs/{job_id}/result` | `format=json|csv|xlsx` | result (409 if not done) |

Status codes: 404 unknown op/source/job or `TableNotFound`; 400 `EngineError`, bad params or units, live op on a
file source; 409 `ModelLockedError`, `ResultNotAvailable`, result of an unfinished job; 503 cannot attach to ETABS.
Finished jobs are kept for 1 hour. Sources and jobs live in memory (lost on restart).

Example (Python client):

```python
import time, requests
base = "http://127.0.0.1:8000"
with open("model_export.xlsx", "rb") as f:
    sid = requests.post(f"{base}/sources/excel", files={"file": f}).json()["source_id"]
beams = requests.post(f"{base}/run/beams", json={"source_id": sid, "params": {"story": "FL1"}}).json()
job = requests.post(f"{base}/run/beam_forces_by_zone",
                    json={"source_id": sid, "params": {"combos": ["ULS1"]}}).json()["job_id"]
while (s := requests.get(f"{base}/jobs/{job}").json())["status"] in ("queued", "running"):
    print(f"{s['done']}/{s['total']} {s['message']} {s['elapsed']:.0f}s")
    time.sleep(1)
rows = requests.get(f"{base}/jobs/{job}/result").json()
xlsx = requests.get(f"{base}/jobs/{job}/result", params={"format": "xlsx"}).content
```

Example (browser):

```js
const base = "http://127.0.0.1:8000";
const { source_id } = await (await fetch(`${base}/sources/live`, { method: "POST",
  headers: { "Content-Type": "application/json" }, body: "{}" })).json();
const { job_id } = await (await fetch(`${base}/run/beam_forces_by_zone`, { method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ source_id, params: { combos: ["ULS1", "ULS2"] } }) })).json();
let s;
do {
  await new Promise(r => setTimeout(r, 1000));
  s = await (await fetch(`${base}/jobs/${job_id}`)).json();
  progressBar.value = s.total ? s.done / s.total : 0;          // your UI
  progressText.textContent = `${s.message} (${s.elapsed.toFixed(0)} s)`;
} while (s.status === "queued" || s.status === "running");
const result = await (await fetch(`${base}/jobs/${job_id}/result`)).json();   // {columns, rows, units}
```

## 8. Errors (`errors.py`)

| Exception | When |
|---|---|
| `EngineError` | base class; unknown operation, invalid params, unknown case/combo, invalid zones |
| `SourceError(EngineError)` | file cannot be opened, live operation on a non-live source |
| `TableNotFound(EngineError)` | table absent from the source (`.table`, `.source`) |
| `ResultNotAvailable(EngineError)` | results table empty / analysis not available |
| `UnitError(EngineError)` | unsupported (imperial) unit (`.table`, `.column`, `.unit`) |
| `EtabsConnectionError` | cannot attach to ETABS |
| `ModelLockedError` | write on a locked model |

Live operations may also raise `KeyError` (object not found) and `ValueError` (bad `kind` or arguments).

## 9. Adding an operation

1. Pick the module: model data -> `ops_model.py`, results -> `ops_forces.py`, needs COM -> `ops_live.py`
   (new domain -> new `ops_<domain>.py` imported in `etabs_python/__init__.py`).
2. Write the function:

```python
from .engine import Context, operation, to_list
from .ops_model import _finish


@operation("story_drifts", slow=True)            # needs="tables" by default
def story_drifts(ctx: Context, stories=None, cases=None, combos=None):
    """Story drifts from the Story Drifts table, filtered by story. One line summary first."""
    df = ctx.table("Story Drifts", cases, combos)      # converted to ctx.units
    wanted = to_list(stories)
    if wanted:
        df = _finish(df[df["Story"].isin(wanted)].reset_index(drop=True), "Story Drifts", df)
    return df
```

3. Rules:
   - First parameter is `ctx`; other parameters must be JSON-friendly (str, number, bool, list, dict) with defaults
     where sensible - they become the HTTP params and `/ops` documentation.
   - Read data only through `ctx.table()` / `ctx.optional_table()` so units and all sources work.
   - Use field keys (check `etabs_python/schema.json` or `DatabaseTables.GetAllFieldsInTable`), never display headers.
   - Keep `attrs["units"]` on returned DataFrames (`_finish(df, name, *source_tables)`).
   - Mark `slow=True` when it reads results tables or loops over many objects; call `ctx.progress.start(total, desc)`
     and `ctx.progress.step(msg=...)` inside long loops.
   - `needs="live"` only when COM calls are unavoidable; access `ctx.source.sap_model` and wrap numeric OAPI
     reads in `ctx.source.present_units(N_MM)` then `convert_value(value, "mm", ctx.units)`.
   - Raise `EngineError` subclasses with actionable messages.
4. Test offline: add the table to `TABLES` in `tests/conftest.py` (display headers, units row, rows) and assert
   hand-computed values in `tests/test_ops_offline.py`. For live-only behaviour add a read-only test in
   `tests/test_live.py` that compares with an OAPI getter.
5. Document it in section 6 of this guide.

## 10. Schema regeneration

`etabs_python/schema.json` maps Excel display headers to field keys for headers that are not simply the key with
spaces/`?` removed. Regenerate after an ETABS upgrade (ETABS open with any model):

```bash
python tools/build_schema.py
```

## 11. Testing

```bash
python -m pytest                       # offline tests always; live tests skip without ETABS
set ETABS_EXPORT_XLSX=D:\path\export.xlsx && python -m pytest tests/test_live.py   # also compare Excel vs live
```

- `tests/conftest.py` builds a synthetic export in the real ETABS layout (no project data).
- Live tests are read-only: they change object selection (restored) and case display/output selection only.
- Live tests need a model with at least one finished analysis case for the force checks.

## 12. Conventions

- English for code, docstrings, messages and docs.
- Python 3.8 compatible syntax (`typing.List/Dict/Optional`, no `dict | dict`).
- Identifier columns (`UniqueName`, `Label`, `Story`, names, sections, materials, `OutputCase`) are always `str`.
- The library does not configure logging; callers do.
- Never commit ETABS exports or models (`.gitignore` covers `ETABS_EXPORTED*.xlsx`, `*.EDB`).
