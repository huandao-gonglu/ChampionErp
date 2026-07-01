@echo off
setlocal
set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"
set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -m pytest tests -v
) else (
  python -m pytest tests -v
)
pause
