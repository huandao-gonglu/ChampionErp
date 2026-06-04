#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONT_DIR="$ROOT_DIR/front"
BACKEND_PORT="${ERP_PORT:-5050}"
FRONTEND_PORT="${VITE_DEV_PORT:-3000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "[setup] Creating Python virtual environment: .venv"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi
PY="$ROOT_DIR/.venv/bin/python"

port_in_use() {
  local port="$1"
  "$PY" - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.25)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

port_owner_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

print_port_owner() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
  fi
}

kill_port_owner() {
  local label="$1"
  local port="$2"
  local pids

  if ! port_in_use "$port"; then
    return 0
  fi

  echo "[cleanup] ${label} port ${port} is already in use; stopping old process(es)..." >&2
  print_port_owner "$port"

  pids="$(port_owner_pids "$port")"
  if [ -z "$pids" ]; then
    echo "[error] ${label} port ${port} is in use, but lsof could not identify the process." >&2
    exit 1
  fi

  kill $pids >/dev/null 2>&1 || true
  for _ in {1..20}; do
    if ! port_in_use "$port"; then
      echo "[cleanup] ${label} port ${port} is free." >&2
      return 0
    fi
    sleep 0.25
  done

  echo "[cleanup] ${label} port ${port} is still in use; force killing old process(es)..." >&2
  pids="$(port_owner_pids "$port")"
  if [ -n "$pids" ]; then
    kill -9 $pids >/dev/null 2>&1 || true
  fi
  for _ in {1..20}; do
    if ! port_in_use "$port"; then
      echo "[cleanup] ${label} port ${port} is free." >&2
      return 0
    fi
    sleep 0.25
  done

  echo "[error] Could not free ${label} port ${port}." >&2
  print_port_owner "$port"
  exit 1
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local pid="$3"
  local log_file="$4"

  for _ in {1..60}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "[error] ${label} exited before it became ready." >&2
      echo "[log] Last lines from ${log_file}:" >&2
      tail -n 80 "$log_file" >&2 || true
      return 1
    fi
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  echo "[error] Timed out waiting for ${label}: ${url}" >&2
  echo "[log] Last lines from ${log_file}:" >&2
  tail -n 80 "$log_file" >&2 || true
  return 1
}

if ! "$PY" -c "import requests, PIL, dotenv" >/dev/null 2>&1; then
  echo "[setup] Installing backend dependencies"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "$ROOT_DIR/requirements.txt"
  "$PY" -m pip install requests pillow python-dotenv
fi

if ! command -v pnpm >/dev/null 2>&1; then
  echo "[error] pnpm not found. Install pnpm first: npm install -g pnpm" >&2
  exit 1
fi

if [ ! -d "$FRONT_DIR/node_modules" ]; then
  echo "[setup] Installing frontend dependencies"
  (cd "$FRONT_DIR" && pnpm install)
fi

cleanup() {
  local code=$?
  echo
  echo "[stop] Stopping dev servers..."
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${BACKEND_PID:-}" ]; then
    wait "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ]; then
    wait "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

export ERP_PORT="$BACKEND_PORT"
export ERP_NO_BROWSER="${ERP_NO_BROWSER:-1}"
export VITE_DEV_PROXY_TARGET="${VITE_DEV_PROXY_TARGET:-http://127.0.0.1:${BACKEND_PORT}}"
export VITE_DEV_PORT="$FRONTEND_PORT"

mkdir -p "$ROOT_DIR/data/logs"
BACKEND_LOG="$ROOT_DIR/data/logs/dev-backend.log"
FRONTEND_LOG="$ROOT_DIR/data/logs/dev-frontend.log"

# Avoid stale Vite alias/cache errors after refactors.
rm -rf "$FRONT_DIR/node_modules/.vite"

kill_port_owner "Backend" "$BACKEND_PORT"
kill_port_owner "Frontend" "$FRONTEND_PORT"

echo "[start] Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "[start] Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "[log] Backend log:  $BACKEND_LOG"
echo "[log] Frontend log: $FRONTEND_LOG"

echo "[start] Launching backend..."
"$PY" "$ROOT_DIR/erp_web_app.py" >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

wait_for_url "backend" "http://127.0.0.1:${BACKEND_PORT}/" "$BACKEND_PID" "$BACKEND_LOG"

echo "[start] Launching Vue dev server..."
(cd "$FRONT_DIR" && exec pnpm exec vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort --force) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

URL="http://127.0.0.1:${FRONTEND_PORT}/"
wait_for_url "frontend" "$URL" "$FRONTEND_PID" "$FRONTEND_LOG"
echo "[ready] Open: $URL"
if [ "${ERP_SKIP_OPEN_BROWSER:-0}" != "1" ]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi

echo "[hint] Press Ctrl+C to stop both servers."
# Keep script alive while either process is running.
wait "$BACKEND_PID" "$FRONTEND_PID"
