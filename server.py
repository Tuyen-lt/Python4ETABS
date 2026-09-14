"""
Dich vu Local REST API (FastAPI) cho phep Ung dung Web, C# .NET va he thong khac
tu dong hoa ETABS qua HTTP localhost.
"""
from typing import Optional, Dict, Any, List
import io
import os
import shutil
import tempfile
import threading
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import pythoncom

from data_bridge import EtabsDataBridge
from data_parser import parse_json_payload, parse_excel_file, parse_csv_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("EtabsServer")

app = FastAPI(
    title="ETABS Automation API",
    description="Dich vu REST API cuc bo dieu khien ETABS OAPI",
    version="1.0.0"
)

# Ho tro CORS de Web App tren localhost / file:// / bat ky port nao deu goi duoc
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bridge_instance: Optional[EtabsDataBridge] = None
_etabs_lock = threading.Lock()


def get_bridge() -> EtabsDataBridge:
    """
    Lay hoac khoi tao ket noi toi ETABS instance dang mo san.
    """
    global _bridge_instance
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except Exception:
        pass

    if _bridge_instance is None:
        _bridge_instance = EtabsDataBridge()
    return _bridge_instance


@app.get("/")
def root():
    return {
        "service": "ETABS Automation API",
        "status": "online",
        "docs_url": "/docs"
    }


@app.get("/api/status")
def get_status():
    """
    Kiem tra trang thai ket noi toi ETABS va mo hinh hien tai.
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            model_file = bridge.sap_model.GetModelFilename()
            locked = bridge.is_locked()
            version_info = bridge.sap_model.GetVersion()
            version = version_info[0] if version_info else "Unknown"

            return {
                "connected": True,
                "model_file": model_file,
                "is_locked": locked,
                "etabs_version": version
            }
        except Exception as e:
            logger.error(f"Loi kiem tra status ETABS: {e}")
            return {
                "connected": False,
                "model_file": None,
                "is_locked": None,
                "etabs_version": None,
                "error": str(e)
            }


@app.post("/api/reconnect")
def reconnect_etabs():
    """
    Buoc ket noi lai toi instance ETABS (dung khi nguoi dung khoi dong lai ETABS).
    """
    global _bridge_instance
    with _etabs_lock:
        try:
            _bridge_instance = None
            bridge = get_bridge()
            model_file = bridge.sap_model.GetModelFilename()
            return {"status": "reconnected", "model_file": model_file}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Khong the ket noi toi ETABS: {e}")


@app.post("/api/execute")
def execute_payload(payload: Dict[str, Any] = Body(...)):
    """
    Nhan chuoi JSON hoac JSON body truc tiep tu ung dung Web / he thong khac de thuc thi:
    - create_groups: ["G1", "G2"]
    - rename: [{"type": "frame", "old_name": "2159", "new_name": "Beam 1"}]
    - assign_groups: {"G1": {"frames": ["Beam 1"], "shells": ["Slab 1"]}}
    - frame_sections: [...]
    - assign_sections: [...]
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            if bridge.is_locked():
                raise HTTPException(status_code=400, detail="Mo hinh ETABS dang bi KHOA (Locked). Vui long mo khoa truoc khi thuc hien.")

            standard_payload = parse_json_payload(payload)
            report = bridge.execute_batch(standard_payload)
            return report
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Loi thuc thi execute: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
def upload_and_run(file: UploadFile = File(...)):
    """
    Nhan tep Excel (.xlsx) hoac CSV tai len tu ung dung Web de thuc thi.
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            if bridge.is_locked():
                raise HTTPException(status_code=400, detail="Mo hinh ETABS dang bi KHOA (Locked). Vui long mo khoa truoc khi thuc hien.")

            file_ext = Path(file.filename or "").suffix.lower()
            if file_ext not in [".xlsx", ".xls", ".csv"]:
                raise HTTPException(status_code=400, detail=f"Dinh dang tep '{file_ext}' khong duoc ho tro. Chi ho tro .xlsx, .xls, .csv")

            content = file.file.read()
            buffer = io.BytesIO(content)

            if file_ext in [".xlsx", ".xls"]:
                standard_payload = parse_excel_file(buffer)
            else:
                csv_records = parse_csv_file(buffer)
                standard_payload = parse_json_payload({"rename": csv_records})

            report = bridge.execute_batch(standard_payload)
            return report

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Loi thuc thi upload_and_run: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tables")
def get_tables():
    """
    Lay danh sach toan bo Database Tables co trong mo hinh ETABS.
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            tables = bridge.get_available_tables()
            return {"count": len(tables), "tables": tables}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pull/{table_name}")
def pull_table_data(table_name: str):
    """
    Trich xuat du lieu mot bang Database Tables tu ETABS sang JSON array.
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            df = bridge.pull_table(table_name)
            return {
                "table": table_name,
                "count": len(df),
                "columns": list(df.columns),
                "data": df.to_dict(orient="records")
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refresh")
def refresh_view():
    """
    Lam moi khung nhin giao dien ETABS.
    """
    with _etabs_lock:
        try:
            bridge = get_bridge()
            bridge.refresh_view()
            return {"status": "refreshed"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
