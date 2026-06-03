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
  wait "$BACKEND_PID" >/dev/null 2>&1 || true
  wait "$FRONTEND_PID" >/dev/null 2>&1 || true
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

echo "[start] Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "[start] Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "[log] Backend log:  $BACKEND_LOG"
echo "[log] Frontend log: $FRONTEND_LOG"

echo "[start] Launching backend..."
"$PY" "$ROOT_DIR/erp_web_app.py" >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

# Give backend a moment to bind before Vite starts proxying.
sleep 1

echo "[start] Launching Vue dev server..."
(cd "$FRONT_DIR" && pnpm exec vite --host 127.0.0.1 --port "$FRONTEND_PORT" --force) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

# Wait until frontend responds.
for _ in {1..60}; do
  if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

URL="http://127.0.0.1:${FRONTEND_PORT}/"
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
