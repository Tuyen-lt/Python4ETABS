"""
FastAPI server for the engine.

All engine calls run on one worker thread (ETABS COM is not thread-safe). Operations flagged slow=True
run as background jobs with progress; other operations return their result directly.

Start:  python -m etabs_python.server --host 127.0.0.1 --port 8000   (docs at /docs)
"""
import argparse
import inspect
import io
import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import OPERATIONS
from .engine import Engine, Progress
from .errors import (EngineError, EtabsConnectionError, ModelLockedError, ResultNotAvailable, SourceError,
                     TableNotFound)
from .sources import ExcelSource, LiveSource, TableSource
from .units import Units

JOB_TTL_SECONDS = 3600

app = FastAPI(title="ETABS Core Engine", version="3.0.0",
              description="Run etabs_python operations on a live ETABS model or an ETABS Excel export.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Worker:
    """Single thread that executes all engine calls (COM initialized in that thread)."""

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="etabs-worker", daemon=True)
        self._thread.start()

    def _loop(self):
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            pass
        while True:
            fn, future = self._queue.get()
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn())
                except BaseException as e:  # delivered to the caller
                    future.set_exception(e)

    def submit(self, fn: Callable[[], Any]) -> Future:
        future: Future = Future()
        self._queue.put((fn, future))
        return future

    def call(self, fn: Callable[[], Any]) -> Any:
        return self.submit(fn).result()


@dataclass
class SourceEntry:
    id: str
    source: TableSource
    tmpdir: Optional[str] = None


@dataclass
class Job:
    id: str
    op: str
    status: str = "queued"
    done: int = 0
    total: Optional[int] = None
    message: str = ""
    error: Optional[str] = None
    result: Any = None
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None

    def state(self) -> Dict[str, Any]:
        start = self.started or self.created
        end = self.finished or time.time()
        return {"job_id": self.id, "op": self.op, "status": self.status, "done": self.done, "total": self.total,
                "message": self.message, "elapsed": round(end - start, 3), "error": self.error}


worker = Worker()
sources: Dict[str, SourceEntry] = {}
jobs: Dict[str, Job] = {}


class RunRequest(BaseModel):
    source_id: str
    params: Dict[str, Any] = {}
    units: Optional[Dict[str, str]] = None


class LiveRequest(BaseModel):
    pid: Optional[int] = None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


@app.exception_handler(TableNotFound)
def _table_not_found(request: Request, exc: TableNotFound):
    return _error(404, str(exc))


@app.exception_handler(ModelLockedError)
@app.exception_handler(ResultNotAvailable)
def _conflict(request: Request, exc: Exception):
    return _error(409, str(exc))


@app.exception_handler(EtabsConnectionError)
def _no_etabs(request: Request, exc: EtabsConnectionError):
    return _error(503, str(exc))


@app.exception_handler(EngineError)
@app.exception_handler(KeyError)
@app.exception_handler(ValueError)
def _bad_request(request: Request, exc: Exception):
    return _error(400, str(exc))


def serialize(result: Any) -> Dict[str, Any]:
    """DataFrame -> {columns, rows, units}; anything else -> {result}."""
    if isinstance(result, pd.DataFrame):
        split = json.loads(result.to_json(orient="split", index=False, force_ascii=False))
        return {"columns": split["columns"], "rows": split["data"], "units": result.attrs.get("units", {})}
    return {"result": jsonable_encoder(result)}


def _prune_jobs():
    now = time.time()
    for job_id in [j.id for j in jobs.values() if j.finished and now - j.finished > JOB_TTL_SECONDS]:
        jobs.pop(job_id, None)


@app.get("/ops")
def list_ops():
    """Registered operations with parameters, needs (tables/live), slow flag and description."""
    return jsonable_encoder(Engine(TableSource()).ops())


@app.post("/sources/live")
def add_live_source(req: LiveRequest = LiveRequest()):
    """Attach to a running ETABS (optionally by process id)."""
    entry = SourceEntry(uuid.uuid4().hex, worker.call(lambda: LiveSource(pid=req.pid)))
    sources[entry.id] = entry
    return {"source_id": entry.id, "kind": "live"}


@app.post("/sources/excel")
def add_excel_source(file: UploadFile = File(...)):
    """Upload an ETABS Excel export (.xlsx)."""
    tmpdir = tempfile.mkdtemp(prefix="etabs_engine_")
    path = Path(tmpdir) / (Path(file.filename or "export.xlsx").name or "export.xlsx")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        source = worker.call(lambda: ExcelSource(path))
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    entry = SourceEntry(uuid.uuid4().hex, source, tmpdir)
    sources[entry.id] = entry
    return {"source_id": entry.id, "kind": "excel", "tables": source.tables()}


@app.delete("/sources/{source_id}")
def delete_source(source_id: str):
    entry = sources.pop(source_id, None)
    if entry is None:
        return _error(404, f"Unknown source '{source_id}'")
    worker.call(entry.source.close)
    if entry.tmpdir:
        shutil.rmtree(entry.tmpdir, ignore_errors=True)
    return {"deleted": source_id}


@app.post("/run/{op}")
def run_op(op: str, req: RunRequest):
    """
    Run an operation. Fast operations return {columns, rows, units} or {result};
    slow operations return HTTP 202 {job_id} (poll /jobs/{job_id}).
    """
    spec = OPERATIONS.get(op)
    if spec is None:
        return _error(404, f"Unknown operation '{op}'")
    entry = sources.get(req.source_id)
    if entry is None:
        return _error(404, f"Unknown source '{req.source_id}'")
    try:
        units = Units(**(req.units or {}))
    except TypeError as e:
        return _error(400, f"Invalid units: {e}")
    if spec.needs == "live" and entry.source.kind != "live":
        raise SourceError(f"Operation '{op}' needs a live ETABS source, current source is '{entry.source.kind}'")
    try:
        inspect.signature(spec.func).bind(None, **req.params)
    except TypeError as e:
        return _error(400, f"Invalid parameters for '{op}': {e}")
    engine = Engine(entry.source, units)

    if not spec.slow:
        return serialize(worker.call(lambda: engine.run(op, **req.params)))

    _prune_jobs()
    job = Job(uuid.uuid4().hex, op)
    jobs[job.id] = job

    def update(done, total, message, elapsed):
        job.done, job.total, job.message = done, total, message

    def execute():
        job.status, job.started = "running", time.time()
        try:
            job.result = engine.run(op, progress=Progress(callback=update), **req.params)
            job.status = "done"
        except Exception as e:
            job.error, job.status = f"{type(e).__name__}: {e}", "error"
        finally:
            job.finished = time.time()

    worker.submit(execute)
    return JSONResponse(status_code=202, content={"job_id": job.id})


@app.get("/jobs/{job_id}")
def job_state(job_id: str):
    _prune_jobs()
    job = jobs.get(job_id)
    if job is None:
        return _error(404, f"Unknown job '{job_id}'")
    return job.state()


@app.get("/jobs/{job_id}/result")
def job_result(job_id: str, format: str = "json"):
    job = jobs.get(job_id)
    if job is None:
        return _error(404, f"Unknown job '{job_id}'")
    if job.status != "done":
        return _error(409, f"Job is {job.status}" + (f": {job.error}" if job.error else ""))
    if format == "json":
        return serialize(job.result)
    if not isinstance(job.result, pd.DataFrame):
        return _error(400, "Only json format is available for non-table results")
    if format == "csv":
        return Response(job.result.to_csv(index=False), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{job.op}.csv"'})
    if format == "xlsx":
        buffer = io.BytesIO()
        job.result.to_excel(buffer, index=False)
        return Response(buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{job.op}.xlsx"'})
    return _error(400, f"Unsupported format '{format}' (json, csv, xlsx)")


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="ETABS core engine server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
