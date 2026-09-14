"""
Engine: operation registry, progress reporting and unit-converted table access.

    engine = Engine(ExcelSource("export.xlsx"))          # or LiveSource(), FileSource(folder)
    df = engine.run("beam_forces_by_zone", combos=["ULS1"], progress=True)

Operations are plain functions registered with @operation; the first argument is a Context.
"""
import inspect
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .errors import EngineError, SourceError, TableNotFound
from .sources import TableSource, write_snapshot
from .units import Units, convert_table


@dataclass
class Operation:
    name: str
    func: Callable
    needs: str
    slow: bool
    doc: str
    params: List[Dict[str, Any]]


OPERATIONS: Dict[str, Operation] = {}


def operation(name: str, needs: str = "tables", slow: bool = False):
    """
    Register an operation.
    needs="tables": works on every source; needs="live": requires LiveSource.
    slow=True: the server runs it as a background job.
    """
    if needs not in ("tables", "live"):
        raise ValueError("needs must be 'tables' or 'live'")

    def decorator(func):
        parameters = list(inspect.signature(func).parameters.values())[1:]
        params = [{"name": p.name,
                   "default": None if p.default is inspect.Parameter.empty else p.default,
                   "required": p.default is inspect.Parameter.empty} for p in parameters]
        OPERATIONS[name] = Operation(name, func, needs, slow, inspect.getdoc(func) or "", params)
        return func

    return decorator


def to_list(value) -> List[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class Progress:
    """
    Progress state with optional tqdm bar and callback(done, total, message, elapsed).
    A heartbeat thread re-emits every `heartbeat` seconds so long blocking calls still show activity.
    """

    def __init__(self, callback: Optional[Callable] = None, use_tqdm: bool = False, heartbeat: float = 1.0):
        self.callback = callback
        self.use_tqdm = use_tqdm
        self.heartbeat = heartbeat
        self.done = 0
        self.total: Optional[int] = None
        self.message = ""
        self.desc = ""
        self._t0 = time.monotonic()
        self._bar = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def start(self, total: Optional[int] = None, desc: str = ""):
        self.total, self.done, self.desc = total, 0, desc
        if self.use_tqdm:
            from tqdm import tqdm
            if self._bar is not None:
                self._bar.close()
            self._bar = tqdm(total=total, desc=desc, unit="step", dynamic_ncols=True)
        if (self.callback or self._bar is not None) and self._thread is None:
            self._thread = threading.Thread(target=self._beat, daemon=True)
            self._thread.start()
        self._emit()

    def step(self, n: int = 1, msg: str = ""):
        self.done += n
        self.message = msg
        if self._bar is not None:
            self._bar.set_postfix_str(msg, refresh=False)
            self._bar.update(n)
        self._emit()

    def close(self):
        self._stop.set()
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def _emit(self):
        if self.callback:
            self.callback(self.done, self.total, self.message, self.elapsed)

    def _beat(self):
        while not self._stop.wait(self.heartbeat):
            if self._bar is not None:
                self._bar.refresh()
            self._emit()


class Context:
    """Passed to operations: unit-converted table access, source, units and progress."""

    def __init__(self, engine: "Engine", progress: Progress):
        self.engine = engine
        self.progress = progress

    @property
    def source(self) -> TableSource:
        return self.engine.source

    @property
    def units(self) -> Units:
        return self.engine.units

    def table(self, name: str, cases=None, combos=None) -> pd.DataFrame:
        return self.engine.table(name, cases, combos, progress=self.progress)

    def optional_table(self, name: str) -> pd.DataFrame:
        """Like table() but returns an empty DataFrame when the table is missing."""
        try:
            return self.table(name)
        except TableNotFound:
            df = pd.DataFrame()
            df.attrs = {"table": name, "units": {}}
            return df


def _make_progress(progress) -> Progress:
    if isinstance(progress, Progress):
        return progress
    if progress is True:
        return Progress(use_tqdm=True)
    if callable(progress):
        return Progress(callback=progress)
    return Progress()


def write_result(result, out) -> Path:
    """Write a result to .xlsx, .csv or .json."""
    path = Path(out)
    suffix = path.suffix.lower()
    if isinstance(result, pd.DataFrame):
        if suffix == ".xlsx":
            result.to_excel(path, index=False)
        elif suffix == ".csv":
            result.to_csv(path, index=False, encoding="utf-8")
        elif suffix == ".json":
            result.to_json(path, orient="records", force_ascii=False, indent=1)
        else:
            raise EngineError(f"Unsupported output format: {suffix}")
    elif suffix == ".json":
        path.write_text(json.dumps(result, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        raise EngineError(f"Only .json output is supported for non-table results, got {suffix}")
    return path


class Engine:
    def __init__(self, source: TableSource, units: Optional[Units] = None):
        self.source = source
        self.units = units or Units()

    def ops(self) -> List[Dict[str, Any]]:
        """List registered operations with needs, slow flag, parameters and docstring."""
        return [{"name": o.name, "needs": o.needs, "slow": o.slow, "doc": o.doc, "params": o.params}
                for o in sorted(OPERATIONS.values(), key=lambda o: o.name)]

    def table(self, name: str, cases=None, combos=None, progress: Optional[Progress] = None) -> pd.DataFrame:
        """Read a table from the source and convert it to self.units."""
        progress = progress or Progress()
        case_list, combo_list = to_list(cases), to_list(combos)
        if self.source.kind == "live" and len(case_list) + len(combo_list) > 1:
            progress.start(total=len(case_list) + len(combo_list), desc=name)
            parts = []
            for case_names, combo_names, label in ([([c], [], c) for c in case_list] +
                                                   [([], [c], c) for c in combo_list]):
                parts.append(self.source.table(name, cases=case_names, combos=combo_names))
                progress.step(msg=f"{name}: {label}")
            raw = pd.concat(parts, ignore_index=True)
            raw.attrs = parts[0].attrs
        else:
            raw = self.source.table(name,
                                    cases=case_list if cases is not None else None,
                                    combos=combo_list if combos is not None else None)
        return convert_table(raw, self.units)

    def run(self, op: str, progress=False, out=None, **params):
        """
        Run an operation. progress: False | True (tqdm) | callable(done, total, message, elapsed) | Progress.
        out: optional .xlsx/.csv/.json path to also write the result.
        """
        if op not in OPERATIONS:
            raise EngineError(f"Unknown operation '{op}'. Available: {sorted(OPERATIONS)}")
        spec = OPERATIONS[op]
        if spec.needs == "live" and self.source.kind != "live":
            raise SourceError(f"Operation '{op}' needs a live ETABS source, current source is '{self.source.kind}'")
        prog = _make_progress(progress)
        ctx = Context(self, prog)
        try:
            bound = inspect.signature(spec.func).bind(ctx, **params)
        except TypeError as e:
            prog.close()
            raise EngineError(f"Invalid parameters for '{op}': {e}")
        try:
            result = spec.func(*bound.args, **bound.kwargs)
        finally:
            prog.close()
        if out is not None:
            write_result(result, out)
        return result

    def export(self, folder, tables: List[str]) -> List[Path]:
        """Write converted tables to a folder readable by FileSource."""
        frames = {name: self.table(name) for name in tables}
        return write_snapshot(folder, frames, self.units.to_dict())
