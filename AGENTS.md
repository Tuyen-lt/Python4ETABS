# AGENTS.md

`etabs_python` is a core engine for CSI ETABS data (Windows). Full reference: [docs/ENGINE_GUIDE.md](docs/ENGINE_GUIDE.md).

## Architecture in one breath

Sources (`LiveSource` COM, `ExcelSource` ETABS export, `FileSource` snapshot) return ETABS tables as DataFrames keyed by
field keys with native units in `df.attrs["units"]` -> `Engine.table()` converts to `Units` (kN, m, section mm, MPa)
-> operations registered with `@operation(name, needs, slow)` in `ops_model.py`, `ops_forces.py`, `ops_live.py`
-> results for Python callers or `server.py` (FastAPI, single worker thread, background jobs for `slow` ops).

## Rules

- Read data only through `ctx.table()` / `ctx.optional_table()` inside operations; never call OAPI for data that exists
  in a database table. COM calls belong in `needs="live"` operations only.
- Column names are field keys (`AnalysisSect`, `AMod`, `UniqueName`), not Excel headers; see `etabs_python/schema.json`.
- Identifier columns are `str`. Keep `attrs["units"]` on returned DataFrames.
- English everywhere, Python 3.8 syntax, no new dependencies without a reason.
- Live reads happen in N-mm (`LiveSource.present_units`) to avoid display rounding; convert with `units.convert_value`.
- Never commit ETABS exports (license number, project data) or models.
- Tests are read-only against ETABS. Every new operation gets an offline test using the synthetic export in
  `tests/conftest.py`.

## Commands

```bash
python -m pytest                          # offline + live (live skips without ETABS)
python -m etabs_python.server --port 8000 # HTTP API, docs at /docs
python tools/build_schema.py              # regenerate schema.json (ETABS open)
```
