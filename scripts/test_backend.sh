#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "[setup] Creating Python virtual environment: .venv"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi

PY="$ROOT_DIR/.venv/bin/python"

# 同时检查测试依赖及其引用的后端依赖，版本只维护在 requirements 中。
if ! "$PY" -m pip install --dry-run --no-index --disable-pip-version-check --quiet -r "$ROOT_DIR/requirements-dev.txt" >/dev/null 2>&1; then
  echo "[setup] 后端测试依赖缺失或版本不符，正在安装…"
  "$PY" -m pip install --disable-pip-version-check --quiet -r "$ROOT_DIR/requirements-dev.txt"
fi

# Do not auto-open browser while tests spawn erp_web.server.
export ERP_NO_BROWSER="${ERP_NO_BROWSER:-1}"
export ERP_SKIP_OPEN_BROWSER="${ERP_SKIP_OPEN_BROWSER:-1}"

# macOS often has port 5000 occupied; use 5050 unless caller overrides it.
export ERP_PORT="${ERP_PORT:-5050}"
export ERP_TEST_BASE_URL="${ERP_TEST_BASE_URL:-http://127.0.0.1:${ERP_PORT}}"

# Backend API tests mutate runtime files in the project root. Preserve them so
# local test runs do not dirty product data, local config, or the checked-in
# SQLite snapshot.
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/champion-erp-test-backup.XXXXXX")"
RUNTIME_FILES=("erp.sqlite3" "config/store_config.json" "config/app_config.json")
restore_runtime_files() {
  local exit_code=$?
  for file in "${RUNTIME_FILES[@]}"; do
    mkdir -p "$ROOT_DIR/$(dirname "$file")"
    if [ -f "$BACKUP_DIR/$file" ]; then
      cp "$BACKUP_DIR/$file" "$ROOT_DIR/$file"
    elif [ -f "$BACKUP_DIR/$file.__missing__" ]; then
      rm -f "$ROOT_DIR/$file"
    fi
  done
  rm -rf "$BACKUP_DIR"
  exit "$exit_code"
}
trap restore_runtime_files EXIT

for file in "${RUNTIME_FILES[@]}"; do
  mkdir -p "$BACKUP_DIR/$(dirname "$file")"
  if [ -f "$ROOT_DIR/$file" ]; then
    cp "$ROOT_DIR/$file" "$BACKUP_DIR/$file"
  else
    : > "$BACKUP_DIR/$file.__missing__"
  fi
done

echo "[test] Python: $($PY --version)"
echo "[test] ERP_TEST_BASE_URL=$ERP_TEST_BASE_URL"

if [ "$#" -eq 0 ]; then
  set -- tests -v
fi

"$PY" -m pytest "$@"
