"""
Module connection.py
Chuyen trach quan ly ket noi Windows COM toi phien lam viec ETABS.
Ho tro dinh tuyen dung Windows Desktop (Default), khoi tao COM MTA da luong,
va cung cap cac lop ngoai le tieu chuan (EtabsConnectionError, ModelLockedError).
"""
from typing import Optional, Any
import ctypes
import threading
import logging
import pythoncom
import comtypes.client

logger = logging.getLogger("EtabsConnection")


class EtabsConnectionError(Exception):
    """Ngoai le khi khong the ket noi toi phien lam viec ETABS."""
    pass


class ModelLockedError(Exception):
    """Ngoai le khi co thao tac sua doi tren mo hinh dang bi khoa."""
    pass


def get_active_etabs(pid: Optional[int] = None) -> Any:
    """
    Ket noi toi instance ETABS dang mo tren he thong.
    Tu dong chuyen sang Default Desktop de tranh loi cach ly desktop tren Windows.
    Neu chi dinh pid, ket noi toi dung process ID do.
    Tra ve con tro SapModel.
    """
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
                holder[1] = "GetObject tra ve None hoac khong co SapModel hop le"
        except Exception as e:
            holder[1] = str(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    if holder[0] is not None:
        return holder[0]

    err_detail = f": {holder[1]}" if holder[1] else ""
    raise EtabsConnectionError(f"Khong the ket noi toi instance ETABS dang chay{err_detail}")


def is_model_locked(sap_model: Any) -> bool:
    """
    Kiem tra trang thai khoa cua mo hinh ETABS.
    """
    if sap_model is None:
        return False
    try:
        return bool(sap_model.GetModelIsLocked())
    except Exception as e:
        logger.warning(f"Khong the kiem tra trang thai khoa: {e}")
        return False


def ensure_unlocked(sap_model: Any, operation_name: str = "Thao tac"):
    """
    Kiem tra va nem ngoai le ModelLockedError neu mo hinh bi khoa.
    """
    if is_model_locked(sap_model):
        raise ModelLockedError(
            f"{operation_name} that bai: Mo hinh ETABS dang bi KHOA (Locked). "
            f"Vui long mo khoa mo hinh truoc khi thuc hien."
        )
