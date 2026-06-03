@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%ERP_PORT%"=="" set "ERP_PORT=5050"
if "%VITE_DEV_PORT%"=="" set "VITE_DEV_PORT=3000"
if "%ERP_NO_BROWSER%"=="" set "ERP_NO_BROWSER=1"
if "%VITE_DEV_PROXY_TARGET%"=="" set "VITE_DEV_PROXY_TARGET=http://127.0.0.1:%ERP_PORT%"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [setup] Creating Python virtual environment: .venv
  python -m venv .venv
)

where pnpm >nul 2>nul
if errorlevel 1 (
  echo [error] pnpm not found. Install pnpm first: npm install -g pnpm
  pause
  exit /b 1
)

if not exist "%~dp0front\node_modules" (
  echo [setup] Installing frontend dependencies
  pushd front
  pnpm install
  popd
)

if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"

if exist "%~dp0front\node_modules\.vite" rmdir /s /q "%~dp0front\node_modules\.vite"

echo [start] Backend:  http://127.0.0.1:%ERP_PORT%
echo [start] Frontend: http://127.0.0.1:%VITE_DEV_PORT%
echo.

start "Champion ERP Backend" cmd /k "cd /d "%~dp0" && set ERP_PORT=%ERP_PORT%&& set ERP_NO_BROWSER=%ERP_NO_BROWSER%&& "%PY%" erp_web_app.py"
start "Champion ERP Vue" cmd /k "cd /d "%~dp0front" && set VITE_DEV_PROXY_TARGET=%VITE_DEV_PROXY_TARGET%&& set VITE_DEV_PORT=%VITE_DEV_PORT%&& pnpm exec vite --host 127.0.0.1 --port %VITE_DEV_PORT% --force"

timeout /t 3 >nul
start "" "http://127.0.0.1:%VITE_DEV_PORT%/"
