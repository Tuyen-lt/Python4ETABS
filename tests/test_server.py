import time

import pytest
from fastapi.testclient import TestClient

from etabs_python.server import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _excel_source(client, path):
    with open(path, "rb") as f:
        r = client.post("/sources/excel", files={"file": ("export.xlsx", f)})
    assert r.status_code == 200, r.text
    return r.json()["source_id"]


def _wait(client, job):
    for _ in range(200):
        s = client.get(f"/jobs/{job}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {s}")


def test_ops_endpoint(client):
    ops = {o["name"]: o for o in client.get("/ops").json()}
    assert {"stories", "beam_forces_by_zone", "select"} <= set(ops)
    assert ops["beam_forces_by_zone"]["params"][3] == {"name": "zones", "default": [0.25, 0.5, 0.25], "required": False}


def test_fast_op_returns_rows(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    r = client.post("/run/beams", json={"source_id": sid, "params": {}})
    body = r.json()
    assert r.status_code == 200 and body["columns"][:3] == ["Story", "Label", "UniqueName"]
    assert [row[2] for row in body["rows"]] == ["10", "11"] and body["units"]["Length"] == "m"


def test_non_table_result(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    r = client.post("/run/story_at", json={"source_id": sid, "params": {"z": [2.0, 5.0]}})
    assert r.status_code == 200 and r.json() == {"result": ["FL1", "FL2"]}


def test_units_override(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    r = client.post("/run/beams", json={"source_id": sid, "params": {}, "units": {"length": "mm"}})
    body = r.json()
    assert body["rows"][0][body["columns"].index("Length")] == 8000 and body["units"]["Length"] == "mm"


def test_slow_op_job_lifecycle(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    r = client.post("/run/beam_forces_by_zone", json={"source_id": sid, "params": {"combos": ["ULS1"]}})
    assert r.status_code == 202, r.text
    job = r.json()["job_id"]
    s = _wait(client, job)
    assert s["status"] == "done", s
    assert {"done", "total", "message", "elapsed"} <= set(s)
    res = client.get(f"/jobs/{job}/result").json()
    assert "M3_max" in res["columns"] and len(res["rows"]) == 6
    x = client.get(f"/jobs/{job}/result", params={"format": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    c = client.get(f"/jobs/{job}/result", params={"format": "csv"})
    assert c.status_code == 200 and c.text.startswith("UniqueName")


def test_job_error_is_reported(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    r = client.post("/run/beam_forces_by_zone", json={"source_id": sid, "params": {"zones": [0.3, 0.3]}})
    s = _wait(client, r.json()["job_id"])
    assert s["status"] == "error" and "zones" in s["error"]
    assert client.get(f"/jobs/{r.json()['job_id']}/result").status_code == 409


def test_upload_several_excel_files(client, tmp_path):
    from conftest import TABLES, write_export
    stories = {k: TABLES[k] for k in ("Story Definitions", "Tower and Base Story Definitions")}
    a = write_export(tmp_path / "Story.xlsx", stories)
    b = write_export(tmp_path / "Rest.xlsx", {k: v for k, v in TABLES.items() if k not in stories})
    with open(a, "rb") as fa, open(b, "rb") as fb:
        r = client.post("/sources/excel", files=[("file", ("Story.xlsx", fa)), ("file", ("Rest.xlsx", fb))])
    assert r.status_code == 200, r.text
    sid = r.json()["source_id"]
    assert set(r.json()["tables"]) == set(TABLES)
    body = client.post("/run/story_at", json={"source_id": sid, "params": {"z": [2.0, 5.0]}}).json()
    assert body == {"result": ["FL1", "FL2"]}
    combo = "1_ULSE1   1.35D+1.5L (LongTerm) 80%~"
    headers, units, rows = TABLES["Joint Reactions"]
    c = write_export(tmp_path / "JR.xlsx", {"Joint Reactions": (headers, units, [r[:3] + [combo] + r[4:] for r in rows])})
    with open(c, "rb") as fc:
        sid2 = client.post("/sources/excel", files={"file": ("JR.xlsx", fc)}).json()["source_id"]
    job = client.post("/run/joint_reactions", json={"source_id": sid2, "params": {"combos": [combo]}}).json()["job_id"]
    assert _wait(client, job)["status"] == "done"
    res = client.get(f"/jobs/{job}/result").json()
    assert [row[res["columns"].index("OutputCase")] for row in res["rows"]] == [combo]


def test_errors(client, export_xlsx):
    sid = _excel_source(client, export_xlsx)
    assert client.post("/run/nope", json={"source_id": sid, "params": {}}).status_code == 404
    assert client.post("/run/select", json={"source_id": sid, "params": {}}).status_code == 400
    assert client.post("/run/beams", json={"source_id": "missing", "params": {}}).status_code == 404
    assert client.post("/run/beams", json={"source_id": sid, "params": {"bad": 1}}).status_code == 400
    assert client.post("/run/beams", json={"source_id": sid, "params": {}, "units": {"force": "kip"}}).status_code == 400
    assert client.get("/jobs/missing").status_code == 404
    assert client.delete(f"/sources/{sid}").status_code == 200
    assert client.post("/run/beams", json={"source_id": sid, "params": {}}).status_code == 404
