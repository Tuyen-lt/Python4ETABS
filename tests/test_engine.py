import pandas as pd
import pytest

from etabs_python.engine import Engine, Progress, operation
from etabs_python.errors import EngineError, SourceError
from etabs_python.sources import ExcelSource, FileSource
from etabs_python.units import Units


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
    assert ops["_test_echo"]["params"][0] == {"name": "value", "default": None, "required": True}
    assert ops["_test_echo"]["params"][1] == {"name": "times", "default": 1, "required": False}
    assert ops["_test_echo"]["doc"] == "Echo value."
    assert ops["_test_live_only"]["needs"] == "live" and ops["_test_echo"]["slow"] is False


def test_run_validates(export_xlsx):
    eng = Engine(ExcelSource(export_xlsx))
    with pytest.raises(EngineError):
        eng.run("nope")
    with pytest.raises(EngineError):
        eng.run("_test_echo", wrong=1)
    with pytest.raises(EngineError):
        eng.run("_test_echo")
    with pytest.raises(SourceError):
        eng.run("_test_live_only")


def test_progress_callback_and_out(export_xlsx, tmp_path):
    calls = []
    eng = Engine(ExcelSource(export_xlsx))
    df = eng.run("_test_echo", value="x", times=3, progress=lambda d, t, m, e: calls.append((d, t, m)),
                 out=tmp_path / "r.csv")
    assert len(df) == 3 and (tmp_path / "r.csv").exists()
    assert (3, 3, "3/3") in calls


def test_progress_object_state():
    p = Progress(heartbeat=0.01)
    p.start(total=2, desc="x")
    p.step(msg="a")
    assert (p.done, p.total, p.message) == (1, 2, "a") and p.elapsed >= 0
    p.close()


def test_progress_tqdm_does_not_crash(export_xlsx):
    Engine(ExcelSource(export_xlsx)).run("_test_echo", value=1, times=2, progress=True)


def test_engine_table_converts_units(export_xlsx):
    eng = Engine(ExcelSource(export_xlsx))
    off = eng.table("Frame Assignments - End Length Offsets")
    assert off["OffsetI"].tolist()[0] == pytest.approx(0.25) and off.attrs["units"]["OffsetI"] == "m"
    mm = Engine(ExcelSource(export_xlsx), units=Units(length="mm"))
    assert mm.table("Frame Assignments - Summary")["Length"][0] == pytest.approx(8000.0)


def test_export_roundtrip(export_xlsx, tmp_path):
    eng = Engine(ExcelSource(export_xlsx))
    paths = eng.export(tmp_path, ["Frame Assignments - Summary", "Element Forces - Beams"])
    assert len(paths) == 2
    back = Engine(FileSource(tmp_path))
    a = eng.table("Element Forces - Beams")
    b = back.table("Element Forces - Beams")
    assert b["M3"].tolist() == pytest.approx(a["M3"].tolist())
    assert b["UniqueName"].tolist() == a["UniqueName"].tolist()
    assert b.attrs["units"] == a.attrs["units"]
