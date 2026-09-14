@echo off
title ETABS Automation API Service
cd /d "%~dp0"
echo ===================================================
echo     KHOI CHAY ETABS LOCAL REST API SERVICE
echo     Dia chi: http://127.0.0.1:8000
echo     Tai lieu API: http://127.0.0.1:8000/docs
echo ===================================================
python -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
pause
