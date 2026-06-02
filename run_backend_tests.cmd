@echo off
setlocal
cd /d D:\champion-Erp
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -m pytest tests -v
) else (
  python -m pytest tests -v
)
pause
