@echo off
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
echo Starting ERP Web server...
echo The browser will open automatically.
echo.

if exist "%PYW%" (
  start "" "%PYW%" "%~dp0erp_web_app.py"
) else (
  start "" pythonw "%~dp0erp_web_app.py"
)
exit /b 0
