# Python4ETABS (`etabs_python`)

Core engine for CSI ETABS data. Read a running ETABS model (COM) or an ETABS Excel export, run operations
(stories, element properties with modifiers, internal forces, beam zone envelopes, selection, model writes),
get pandas DataFrames in SI units, from Python or over a FastAPI server with background jobs and progress.

```python
from etabs_python import Engine, LiveSource, ExcelSource

eng = Engine(LiveSource())                     # or Engine(ExcelSource("export.xlsx"))
eng.run("stories")
eng.run("frame_properties")                    # sections, Sect_* x Obj_* modifiers, materials
eng.run("beam_forces_by_zone", combos=["ULS1", "ULS2"], progress=True)   # 0.25L / 0.5L / 0.25L max/min
eng.run("select", labels=["B1", "B2"], story="FL1", kind="beam")
```

```bash
pip install -e .[test]
python -m etabs_python.server --port 8000      # http://127.0.0.1:8000/docs
python -m pytest
```

Units default to kN, m (member lengths, coordinates), mm (section properties) and MPa (material strength).

Documentation: [docs/ENGINE_GUIDE.md](docs/ENGINE_GUIDE.md) (usage, operations, HTTP API, units, extending).

## License

Apache-2.0, see [LICENSE](LICENSE).
