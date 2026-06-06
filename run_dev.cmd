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

"%PY%" -c "import requests, PIL, dotenv, openai" >nul 2>nul
if errorlevel 1 (
  echo [setup] Installing backend dependencies
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r "%~dp0requirements.txt"
  "%PY%" -m pip install requests pillow python-dotenv
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
set "BACKEND_LOG=%~dp0data\logs\dev-backend.log"
set "FRONTEND_LOG=%~dp0data\logs\dev-frontend.log"

if exist "%~dp0front\node_modules\.vite" rmdir /s /q "%~dp0front\node_modules\.vite"

call :KillPortOwner "Backend" "%ERP_PORT%"
if errorlevel 1 exit /b 1
call :KillPortOwner "Frontend" "%VITE_DEV_PORT%"
if errorlevel 1 exit /b 1

echo [start] Backend:  http://127.0.0.1:%ERP_PORT%
echo [start] Frontend: http://127.0.0.1:%VITE_DEV_PORT%
echo [log] Backend log:  %BACKEND_LOG%
echo [log] Frontend log: %FRONTEND_LOG%
echo.

echo [start] Launching backend...
start "Champion ERP Backend" /D "%~dp0" cmd /k ""%PY%" "%~dp0erp_web_app.py" ^>"%BACKEND_LOG%" 2^>^&1"

call :WaitForUrl "backend" "http://127.0.0.1:%ERP_PORT%/" "%BACKEND_LOG%"
if errorlevel 1 exit /b 1

echo [start] Launching Vue dev server...
start "Champion ERP Vue" /D "%~dp0front" cmd /k "pnpm exec vite --host 127.0.0.1 --port %VITE_DEV_PORT% --strictPort --force ^>"%FRONTEND_LOG%" 2^>^&1"

call :WaitForUrl "frontend" "http://127.0.0.1:%VITE_DEV_PORT%/" "%FRONTEND_LOG%"
if errorlevel 1 exit /b 1

echo [ready] Open: http://127.0.0.1:%VITE_DEV_PORT%/
if not "%ERP_SKIP_OPEN_BROWSER%"=="1" start "" "http://127.0.0.1:%VITE_DEV_PORT%/"

echo [hint] Close the Backend and Vue command windows to stop dev servers.
exit /b 0

:CollectPortPids
set "PORT_PIDS="
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%~1 .*LISTENING"') do (
  if not "%%P"=="0" set "PORT_PIDS=!PORT_PIDS! %%P"
)
exit /b 0

:KillPortOwner
set "LABEL=%~1"
set "PORT=%~2"
call :CollectPortPids "%PORT%"
if "!PORT_PIDS!"=="" exit /b 0

echo [cleanup] %LABEL% port %PORT% is already in use; stopping old process(es)...
for %%P in (!PORT_PIDS!) do tasklist /fi "PID eq %%P"

for %%P in (!PORT_PIDS!) do taskkill /PID %%P /T >nul 2>nul
for /l %%I in (1,1,20) do (
  call :CollectPortPids "%PORT%"
  if "!PORT_PIDS!"=="" (
    echo [cleanup] %LABEL% port %PORT% is free.
    exit /b 0
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Milliseconds 250" >nul 2>nul
)

echo [cleanup] %LABEL% port %PORT% is still in use; force killing old process(es)...
call :CollectPortPids "%PORT%"
for %%P in (!PORT_PIDS!) do taskkill /F /PID %%P /T >nul 2>nul
for /l %%I in (1,1,20) do (
  call :CollectPortPids "%PORT%"
  if "!PORT_PIDS!"=="" (
    echo [cleanup] %LABEL% port %PORT% is free.
    exit /b 0
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Milliseconds 250" >nul 2>nul
)

echo [error] Could not free %LABEL% port %PORT%.
call :CollectPortPids "%PORT%"
for %%P in (!PORT_PIDS!) do tasklist /fi "PID eq %%P"
exit /b 1

:WaitForUrl
set "LABEL=%~1"
set "URL=%~2"
set "LOG_FILE=%~3"
for /l %%I in (1,1,60) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri $args[0] -TimeoutSec 2 ^| Out-Null; exit 0 } catch { exit 1 }" "%URL%" >nul 2>nul
  if not errorlevel 1 exit /b 0
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Milliseconds 500" >nul 2>nul
)

echo [error] Timed out waiting for %LABEL%: %URL%
echo [log] Last lines from %LOG_FILE%:
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath $args[0]) { Get-Content -LiteralPath $args[0] -Tail 80 }" "%LOG_FILE%"
exit /b 1
