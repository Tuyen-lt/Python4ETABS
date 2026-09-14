# ETABS Core Engine - Design

Date: 2026-09-14
Status: Draft for review

## 1. Goal

Turn the library into a core engine that:

- reads data from a running ETABS (COM) **or** from exported files (ETABS Excel export, engine CSV/JSON snapshots),
- runs named operations (stories, element info, properties, internal forces, beam zone envelopes, selection, model writes),
- returns pandas DataFrames in a consistent SI unit system,
- is callable from Python workflows and from a FastAPI server used by a web UI.

Non-goals: remote agents / cloud deployment, Access `.mdb` and `.e2k` sources, imperial units, multi-user auth.

## 2. Deployment

Engine and server run on the Windows machine that has ETABS. The web UI talks to the server over localhost or LAN.
Operations that do not need COM also work on any machine with only exported files.

## 3. Package layout

```
etabs_python/
  __init__.py      # exports Engine, LiveSource, ExcelSource, FileSource, Units, errors
  connection.py    # COM attach, lock helpers (existing)
  errors.py        # EngineError, SourceError, TableNotFound, ResultNotAvailable, UnitError; re-exports ModelLockedError
  units.py         # unit string parser, Units target system, DataFrame conversion
  schema.json      # table -> fields (key, display name, unit) generated from ETABS 22
  sources.py       # TableSource base, LiveSource, ExcelSource, FileSource
  engine.py        # operation registry, Engine.run/ops/export, Progress
  ops_model.py     # stories, story_at, frames/beams/columns/shells/walls/slabs/points, properties, materials
  ops_forces.py    # beam/column/pier forces, joint reactions, beam_forces_by_zone
  ops_live.py      # select, selected, clear_selection, name_from_label, frame_info, execute_batch
  bridge.py        # EtabsDataBridge write helpers (renamed from data_bridge.py)
  parser.py        # payload parsing (renamed from data_parser.py)
  rename.py        # bulk rename (renamed from Object_rename.py)
  server.py        # FastAPI app + job queue
tools/
  build_schema.py  # regenerates schema.json from a running ETABS
tests/
  fixtures/        # small synthetic Excel export + FileSource snapshot, no project data
  test_units.py, test_sources.py, test_ops_offline.py, test_server.py
  test_live.py     # read-only, skipped when ETABS is not running
docs/
  ENGINE_GUIDE.md  # full usage + extension guide for humans and agents
AGENTS.md          # short pointer to ENGINE_GUIDE.md
```

`etabs_model.py`, `data_bridge.py`, `data_parser.py`, `Object_rename.py`, `test_etabs_model.py`, `test_live_readonly.py`,
`example_pull_inject.py`, `start_server.bat` at repo root are removed or moved into the package. Imports become
package imports (`from etabs_python import Engine`). All code, docstrings, messages and docs are in English.

## 4. Sources

```python
class TableSource:
    name: str                                   # "live" | "excel" | "file"
    def tables(self) -> list[str]
    def table(self, name: str, cases=None, combos=None) -> pd.DataFrame
```

`table()` returns a DataFrame whose column names are ETABS **field keys** (e.g. `AnalysisSect`, `AMod`,
`UniqueName`), values in the source's native units, with `df.attrs["units"] = {column: unit_string}` and
`df.attrs["table"] = name`. Raises `TableNotFound(name, source)` if the table is absent.

### LiveSource(pid=None, sap_model=None)
- Reads with `DatabaseTables.GetTableForDisplayArray`; units come from `GetAllFieldsInTable` (present units).
- `cases` / `combos`: calls `SetLoadCasesSelectedForDisplay` / `SetLoadCombinationsSelectedForDisplay` before reading.
- Exposes `.sap_model` and `.bridge` for live operations.
- Empty result table for results tables with no analysis -> `ResultNotAvailable`.

### ExcelSource(path)
- Each sheet: row 1 `TABLE:  <table name>`, row 2 display headers, row 3 units, data from row 4.
  Table name is taken from row 1 (sheet names are truncated).
- Display headers are mapped to field keys with `schema.json`; unknown headers fall back to the header with
  spaces and `?` removed.
- Identifier columns (`UniqueName`, `Label`, `Story`, names, sections, materials) are cast to `str`.
- `cases` / `combos`: filter on `OutputCase`.
- Workbook is loaded read-only once; sheets are parsed lazily and cached.

### FileSource(folder)
- One file per table: `<table name>.csv` or `.json`, plus `units.json` mapping table -> column -> unit.
- Produced by `Engine.export(folder, tables)`.

## 5. Units

Target system (`Units`), defaults and allowed values:

| Quantity | Default | Allowed |
|---|---|---|
| force | kN | N, kN |
| length (general: member length, coordinates, elevations, stations, offsets) | m | mm, m |
| section length (dimensions, thickness, A, I, J, S, Z) | mm | mm, m |
| stress / strength (E, G, Fc, Fy, Fu) | MPa | kPa, MPa |

Derived units (moment, force/length, force/area, unit weight, area, inertia) follow from force and length.
Moment default is therefore kN-m.

Parsing: a unit string is split on `-`, `/` and superscripts/digits (`²`, `³`, `⁴`, `⁶`, `2`, `3`) into powers of
force (`N`, `kN`) and length (`mm`, `cm`, `m`); `Pa`, `kPa`, `MPa` expand to N/m², kN/m², N/mm².

Conversion rules:
- Section-length columns = columns of tables whose name starts with `Frame Section Property Definitions`,
  `Area Section Property Definitions`, `Slab Property Definitions`, `Wall Property Definitions`,
  `Deck Property Definitions`, `Reinforcing Bar Sizes`. All other length-based columns use general length
  (so pier/spandrel widths and thicknesses are in general length).
- Pure stress (force/length²) columns use the stress unit regardless of table.
- Columns with no unit are unchanged.
- Non force/length units (`kg`, `ton-m²`, `sec`, `1/C`, `cyc/sec`, `deg`, `rad`) are left unchanged and recorded.
- Imperial units (`kip`, `lb`, `in`, `ft`) raise `UnitError(table, column, unit)`.
- Output `df.attrs["units"]` holds the converted unit strings.

Operations always see converted data: `Engine` converts tables right after reading.

## 6. Engine

```python
engine = Engine(source, units=Units())         # Units(force="kN", length="m", section="mm", stress="MPa")
engine.ops() -> list[dict]                      # name, needs, slow, params (name/default/annotation), doc
engine.run(op, progress=False, out=None, **params) -> pd.DataFrame | dict
engine.table(name, cases=None, combos=None) -> pd.DataFrame   # converted raw table
engine.export(folder, tables) -> list[Path]     # FileSource snapshot
```

Operation registration:

```python
@operation("beam_forces_by_zone", needs="tables", slow=True)
def beam_forces_by_zone(ctx, cases=None, combos=None, zones=(0.25, 0.5, 0.25), envelope=False):
    """One line summary. Details..."""
```

- `ctx` gives `ctx.table(name, cases, combos)`, `ctx.source`, `ctx.units`, `ctx.progress`.
- `needs="tables"` works on every source; `needs="live"` raises `SourceError` unless the source is `LiveSource`.
- `slow=True` makes the server run it as a background job. Python callers always run synchronously.
- `run()` validates params against the function signature (unknown / missing -> `EngineError`).
- `out="x.xlsx|.csv|.json"` also writes the result.

Progress:

```python
ctx.progress.start(total, desc)   # optional
ctx.progress.step(n=1, msg="")
```

- `progress=True` -> tqdm bar in terminal; `progress=callable(done, total, msg, elapsed)` -> custom;
  server -> job state.
- A heartbeat thread refreshes elapsed time every second while a long COM call blocks, so a stuck-looking bar
  still shows it is alive.
- `LiveSource` reads results tables one case/combo at a time and steps progress per case/combo.

## 7. Operations

`needs="tables"`:

| Operation | Tables | Output |
|---|---|---|
| `stories` | Story Definitions, Tower and Base Story Definitions | Story, Height, Elevation (cumulative from base), attrs base_name/base_elevation |
| `story_at(z)` | same | story name(s) for elevation(s); base name at base; None outside |
| `frames/beams/columns(story=None)` | Frame Assignments - Summary | assignment summary rows |
| `shells/walls/slabs(story=None)` | Area Assignments - Summary | |
| `points(story=None)` | Point Object Connectivity | |
| `materials` | Material Properties - General / Basic Mechanical / Concrete / Steel / Rebar / Tendon | one row per material |
| `frame_properties` | Frame Assignments - *, Frame Section Property Definitions - *, materials | one row per frame; Sect_*, Obj_*, effective modifiers |
| `area_properties` | Area Assignments - *, Area Section Property Definitions - Summary, Slab/Wall/Deck Property Definitions, materials | one row per area |
| `beam_forces(cases, combos)` | Element Forces - Beams | stations with P, V2, V3, T, M2, M3 |
| `column_forces(cases, combos)` | Element Forces - Columns | |
| `pier_forces(piers, stories, cases, combos)` | Pier Forces | |
| `joint_reactions(cases, combos)` | Joint Reactions | |
| `beam_forces_by_zone(cases, combos, zones, envelope)` | Element Forces - Beams, Frame Assignments - Summary | per beam x zone (x case): max/min of 6 components |

Rules carried over from the current `etabs_model.py` (already verified live):
- effective modifier = section modifier x object modifier; objects absent from the modifier table use 1;
- a station on a zone boundary counts for both zones; `zones` must sum to 1;
- `story_at`: story X contains (elevation of story below, elevation of X]; base story name from
  `Tower and Base Story Definitions.BSName`.

`needs="live"`: `select(names|labels+story, kind, clear)`, `selected`, `clear_selection`,
`name_from_label(label, story, kind)`, `frame_info(name)`, `execute_batch(payload)` (writes; raises
`ModelLockedError` if locked).

Multi-row-per-object assignment tables (loads) are skipped by `*_properties`; read them with `engine.table()`.

## 8. Server

Single FastAPI app, one worker thread owns all COM calls (COM initialized in that thread).

| Method | Path | Behaviour |
|---|---|---|
| GET | `/ops` | `engine.ops()` |
| POST | `/sources/live` | body `{pid?}` -> `{source_id}` |
| POST | `/sources/excel` | multipart upload -> `{source_id}` (file kept in a temp dir until the source is deleted) |
| DELETE | `/sources/{id}` | drop source |
| POST | `/run/{op}` | body `{source_id, params, units?}`; fast op -> `{columns, rows, units}`; slow op -> `{job_id}` (HTTP 202) |
| GET | `/jobs/{id}` | `{status: queued/running/done/error, done, total, message, elapsed, error?}` |
| GET | `/jobs/{id}/result?format=json\|xlsx\|csv` | result |

Errors: `EngineError` subclasses -> 400, `ModelLockedError` -> 409, `TableNotFound` -> 404, `ResultNotAvailable` -> 409,
unexpected -> 500 with message. Jobs and sources live in memory; finished jobs are kept for 1 hour.
The old endpoints (`/api/execute`, `/api/upload`, `/api/pull/...`) are removed; `execute_batch` replaces them.
Start with `python -m etabs_python.server` (host/port args).

## 9. Testing

- Offline (no ETABS, run in CI-like conditions): units parser and conversion; ExcelSource on a synthetic fixture
  generated by `tests/fixtures/make_fixture.py` in the exact ETABS export layout; FileSource round-trip via
  `export`; all `needs="tables"` operations on the fixture with hand-checked expected values; engine param
  validation and progress callback; server endpoints with FastAPI `TestClient` including a slow job lifecycle.
- Live (read-only, skipped if ETABS not running): LiveSource tables vs `Results` API (`FrameForce`, `BaseReact`),
  modifiers vs `GetModifiers`, `story_at` vs point stories, and **ExcelSource vs LiveSource** equality on tables
  present in both, using a user-exported workbook passed by env var `ETABS_EXPORT_XLSX` (never committed).
- The real export `ETABS_EXPORTED.xlsx` contains the ETABS license number and project data: it is git-ignored.

## 10. Documentation

`docs/ENGINE_GUIDE.md` (English) covers: concepts (source, engine, operation, units), install, Python quick start,
every operation with params and output columns, units rules, which tables to export from ETABS for file mode,
HTTP API with request/response examples, progress and jobs, error types, how to add an operation (template +
checklist: register, needs/slow, progress, docstring, offline test, guide entry), how to regenerate `schema.json`,
how to run tests. `AGENTS.md` summarizes conventions and points to the guide.

## 11. Open points

- Required tables for file mode must be exported by the user; the guide lists them per operation.
- `schema.json` is generated from ETABS 22.7; other versions fall back to header normalization for unknown fields.
