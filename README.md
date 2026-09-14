# Python4ETABS (`etabs_python`)

High-performance data bridge and local REST API for CSI ETABS over the Windows COM Open API.

## Quick start

```python
from data_bridge import EtabsDataBridge

bridge = EtabsDataBridge()        # attach to running ETABS
frames = bridge.pull_frames()     # pandas DataFrame
shells = bridge.pull_shells()
points = bridge.pull_points()

bridge.inject_group_definition(["G1", "G2"])   # raises ModelLockedError if locked
bridge.refresh_view()
```

## Layout

- `connection.py` - COM attach (`get_active_etabs`), lock helpers, exceptions
- `data_bridge.py` - `EtabsDataBridge`: bulk pull/inject via DatabaseTables, batch execution
- `data_parser.py` - JSON / Excel / CSV payload parsing
- `Object_rename.py` - bulk rename of frames, shells, points
- `server.py` + `start_server.bat` - local FastAPI REST service (`http://127.0.0.1:8000/docs`)
- `example_pull_inject.py` - usage example
- `test_live_readonly.py` - read-only smoke test, run with ETABS open: `python test_live_readonly.py`

## License

AGPL-3.0, see [LICENSE](LICENSE).
