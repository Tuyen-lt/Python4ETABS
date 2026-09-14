# Python4ETABS (`etabs_python`)

High-performance data bridge and local REST API for CSI ETABS over the Windows COM Open API.

## Quick start

```python
from etabs_model import EtabsModel

m = EtabsModel()                  # attach to running ETABS
m.set_units("kN", "m")

m.stories()                       # Story, Elevation, Height (bottom -> top)
m.story_at(12.5)                  # story containing elevation z
m.beams("Story5"), m.columns(), m.walls(), m.slabs(), m.points()
m.frame_info("15218")             # label, story, section, type, nodes, coords, length

m.select(labels=["B1", "B2"], story="Story5", kind="beam")   # select by label + story
m.select(names=["83", "84"], kind="frame")                  # select by UniqueName + type
m.selected(), m.clear_selection()

m.frame_properties()   # assignments + section (dims, A, I...) + modifiers + material, 1 row/frame
m.area_properties()    # assignments + section + modifiers + material, 1 row/area
# modifiers: Sect_* (section) x Obj_* (object, default 1) = effective AMod, I3Mod, f11Mod...

m.frame_forces("15218", combos="ULS1")        # P, V2, V3, T, M2, M3 per station
m.beam_forces_by_zone(combos="ULS1")          # max/min per beam in zones 0.25L / 0.5L / 0.25L
m.pier_forces(piers="P1", cases=["SW", "LL"])
m.joint_reactions(combos="ULS1")

m.bridge.pull_table("Story Definitions")      # any database table
m.bridge.inject_group_definition(["G1"])      # writes (raises ModelLockedError if locked)
```

## Layout

- `etabs_model.py` - `EtabsModel`: stories, element info, selection, properties, internal forces
- `connection.py` - COM attach (`get_active_etabs`), lock helpers, exceptions
- `data_bridge.py` - `EtabsDataBridge`: bulk pull/inject via DatabaseTables, batch execution
- `data_parser.py` - JSON / Excel / CSV payload parsing
- `Object_rename.py` - bulk rename of frames, shells, points
- `server.py` + `start_server.bat` - local FastAPI REST service (`http://127.0.0.1:8000/docs`)
- `example_pull_inject.py` - usage example
- `test_etabs_model.py` - offline logic checks + live read-only checks: `python test_etabs_model.py`
- `test_live_readonly.py` - read-only smoke test, run with ETABS open: `python test_live_readonly.py`

## License

AGPL-3.0, see [LICENSE](LICENSE).
