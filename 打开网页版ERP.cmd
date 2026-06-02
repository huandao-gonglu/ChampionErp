@echo off
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
echo Starting Champion ERP Web...
echo Default URL: http://127.0.0.1:5000/
echo.

if exist "%PYW%" (
  start "" "%PYW%" "%~dp0erp_web_app.py"
) else (
  start "" pythonw "%~dp0erp_web_app.py"
)
exit /b 0
