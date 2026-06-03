#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "[setup] Creating Python virtual environment: .venv"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi

PY="$ROOT_DIR/.venv/bin/python"

# Install dependencies needed by the backend test suite.
if ! "$PY" -c "import pytest, requests, PIL, dotenv" >/dev/null 2>&1; then
  echo "[setup] Installing backend test dependencies"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install pytest requests pillow python-dotenv
fi

# Try runtime requirements too, but do not block local backend tests if an AI SDK
# is unavailable for the current Python version / package index.
if [ "${INSTALL_FULL_REQUIREMENTS:-0}" = "1" ]; then
  "$PY" -m pip install -r requirements.txt
fi

# Do not auto-open browser while tests spawn erp_web_app.py.
export ERP_NO_BROWSER="${ERP_NO_BROWSER:-1}"
export ERP_SKIP_OPEN_BROWSER="${ERP_SKIP_OPEN_BROWSER:-1}"

# macOS often has port 5000 occupied; use 5050 unless caller overrides it.
export ERP_PORT="${ERP_PORT:-5050}"
export ERP_TEST_BASE_URL="${ERP_TEST_BASE_URL:-http://127.0.0.1:${ERP_PORT}}"

# Backend API tests mutate runtime files in the project root. Preserve them so
# local test runs do not dirty product data or the checked-in SQLite snapshot.
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/champion-erp-test-backup.XXXXXX")"
RUNTIME_FILES=("product.json" "erp.sqlite3")
restore_runtime_files() {
  local exit_code=$?
  for file in "${RUNTIME_FILES[@]}"; do
    if [ -f "$BACKUP_DIR/$file" ]; then
      cp "$BACKUP_DIR/$file" "$ROOT_DIR/$file"
    fi
  done
  rm -rf "$BACKUP_DIR"
  exit "$exit_code"
}
trap restore_runtime_files EXIT

for file in "${RUNTIME_FILES[@]}"; do
  if [ -f "$ROOT_DIR/$file" ]; then
    cp "$ROOT_DIR/$file" "$BACKUP_DIR/$file"
  fi
done

echo "[test] Python: $($PY --version)"
echo "[test] ERP_TEST_BASE_URL=$ERP_TEST_BASE_URL"

if [ "$#" -eq 0 ]; then
  set -- tests -v
fi

"$PY" -m pytest "$@"
