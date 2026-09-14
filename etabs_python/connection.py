"""
COM connection to a running ETABS instance.

Attaches from a dedicated MTA thread bound to the "Default" desktop (avoids desktop isolation
issues on Windows) and provides model lock helpers and the connection exceptions.
"""
from typing import Optional, Any, List
import csv
import ctypes
import io
import subprocess
import threading
import logging
import pythoncom
import comtypes.client

logger = logging.getLogger("EtabsConnection")


class EtabsConnectionError(Exception):
    """Raised when no running ETABS instance can be attached."""
    pass


class ModelLockedError(Exception):
    """Raised when a modifying operation is attempted on a locked model."""
    pass


def etabs_process_ids() -> List[int]:
    """Process ids of running ETABS.exe instances (Windows tasklist)."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ETABS.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as e:
        logger.warning(f"Cannot list ETABS processes: {e}")
        return []
    return [int(row[1]) for row in csv.reader(io.StringIO(out)) if len(row) > 1 and row[1].isdigit()]


def get_active_etabs(pid: Optional[int] = None) -> Any:
    """
    Attach to a running ETABS instance and return its SapModel.
    If pid is given, attach to that process. Otherwise use the active instance; when ETABS is not registered as
    active (e.g. opened by double-clicking a model), try every running ETABS.exe process id.
    """
    if pid is not None:
        return _attach(pid)
    try:
        return _attach(None)
    except EtabsConnectionError as first_error:
        for process_id in etabs_process_ids():
            try:
                return _attach(process_id)
            except EtabsConnectionError:
                continue
        raise first_error


def _attach(pid: Optional[int]) -> Any:
    holder = [None, None]
    user32 = ctypes.windll.user32
    hDesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)

    def worker():
        try:
            if hDesk:
                user32.SetThreadDesktop(hDesk)
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            helper = comtypes.client.CreateObject("ETABSv1.Helper")

            obj = None
            if pid is not None and hasattr(helper, "GetObjectProcess"):
                obj = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", int(pid))

            if obj is None:
                obj = helper.GetObject("CSI.ETABS.API.ETABSObject")

            if obj and hasattr(obj, "SapModel") and obj.SapModel:
                holder[0] = obj.SapModel
            else:
                holder[1] = "GetObject returned None or no valid SapModel"
        except Exception as e:
            holder[1] = str(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    if holder[0] is not None:
        return holder[0]

    err_detail = f": {holder[1]}" if holder[1] else ""
    raise EtabsConnectionError(f"Cannot attach to a running ETABS instance{err_detail}")


def is_model_locked(sap_model: Any) -> bool:
    """
    Return True if the ETABS model is locked.
    """
    if sap_model is None:
        return False
    try:
        return bool(sap_model.GetModelIsLocked())
    except Exception as e:
        logger.warning(f"Cannot read model lock state: {e}")
        return False


def ensure_unlocked(sap_model: Any, operation_name: str = "Operation"):
    """
    Raise ModelLockedError if the model is locked.
    """
    if is_model_locked(sap_model):
        raise ModelLockedError(
            f"{operation_name} failed: the ETABS model is locked. Unlock the model first."
        )
