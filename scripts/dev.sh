#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONT_DIR="$ROOT_DIR/front"
BACKEND_PORT="${ERP_PORT:-5050}"
FRONTEND_PORT="${VITE_DEV_PORT:-3000}"
IMAGE_HTTPS_PORT="${ERP_IMAGE_HTTPS_PORT:-8787}"
IMAGE_HTTPS_TUNNEL_MODE="${ERP_IMAGE_HTTPS_TUNNEL:-auto}"
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

wait_for_quick_tunnel_url() {
  local pid="$1"
  local log_file="$2"
  local public_url

  for _ in {1..120}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "[error] 图片 HTTPS Tunnel 在就绪前退出。" >&2
      tail -n 80 "$log_file" >&2 || true
      return 1
    fi
    public_url="$(awk 'match($0, /https:\/\/[a-z0-9-]+\.trycloudflare\.com/) { print substr($0, RSTART, RLENGTH); exit }' "$log_file")"
    # 某些 VPN/代理网络无法从 origin 本机回连 trycloudflare 域名；
    # cloudflared 已注册连接才是 Tunnel 就绪的可靠本地信号。
    if [ -n "$public_url" ] && grep -q "Registered tunnel connection" "$log_file"; then
      echo "$public_url"
      return 0
    fi
    sleep 0.25
  done

  echo "[error] 等待图片 HTTPS Tunnel 地址超时。" >&2
  tail -n 80 "$log_file" >&2 || true
  return 1
}

start_image_https_tunnel() {
  local mode
  local image_root
  local public_url
  mode="$(printf '%s' "$IMAGE_HTTPS_TUNNEL_MODE" | tr '[:upper:]' '[:lower:]')"
  image_root="${ERP_IMAGE_HTTPS_ROOT:-$ROOT_DIR/data/images/public}"

  case "$mode" in
    0|false|off|disabled)
      echo "[skip] 图片 HTTPS Tunnel 已禁用。"
      return 0
      ;;
  esac

  if [ -n "${ERP_IMAGE_HTTPS_BASE_URL:-}" ]; then
    export ERP_IMAGE_HTTPS_PROVIDER="${ERP_IMAGE_HTTPS_PROVIDER:-local_static}"
    export ERP_IMAGE_HTTPS_ROOT="$image_root"
    echo "[ready] 使用已配置的图片 HTTPS 地址：$ERP_IMAGE_HTTPS_BASE_URL"
    return 0
  fi

  if ! command -v cloudflared >/dev/null 2>&1; then
    if [ "$mode" = "required" ]; then
      echo "[error] 图片 HTTPS Tunnel 为 required，但未安装 cloudflared。" >&2
      return 1
    fi
    echo "[warn] 未安装 cloudflared，跳过本地图片 HTTPS Tunnel。" >&2
    echo "[hint] macOS 可运行：brew install cloudflared" >&2
    return 0
  fi

  if port_in_use "$IMAGE_HTTPS_PORT"; then
    echo "[error] 图片静态服务端口 $IMAGE_HTTPS_PORT 已被占用。" >&2
    print_port_owner "$IMAGE_HTTPS_PORT"
    if [ "$mode" = "required" ]; then
      return 1
    fi
    echo "[warn] 跳过本地图片 HTTPS Tunnel；可通过 ERP_IMAGE_HTTPS_PORT 更换端口。" >&2
    return 0
  fi

  IMAGE_TUNNEL_LOG="$ROOT_DIR/data/logs/dev-image-tunnel.log"
  echo "[start] Launching image HTTPS Tunnel..."
  ERP_IMAGE_HTTPS_ROOT="$image_root" \
  ERP_IMAGE_HTTPS_PORT="$IMAGE_HTTPS_PORT" \
  PYTHON_BIN="$PY" \
    "$ROOT_DIR/scripts/image_https_tunnel.sh" >"$IMAGE_TUNNEL_LOG" 2>&1 &
  IMAGE_TUNNEL_PID=$!

  if ! public_url="$(wait_for_quick_tunnel_url "$IMAGE_TUNNEL_PID" "$IMAGE_TUNNEL_LOG")"; then
    kill "$IMAGE_TUNNEL_PID" >/dev/null 2>&1 || true
    wait "$IMAGE_TUNNEL_PID" >/dev/null 2>&1 || true
    unset IMAGE_TUNNEL_PID
    if [ "$mode" = "required" ]; then
      return 1
    fi
    echo "[warn] 图片 HTTPS Tunnel 启动失败，后端继续以 existing_url 模式运行。" >&2
    return 0
  fi

  export ERP_IMAGE_HTTPS_PROVIDER="local_static"
  export ERP_IMAGE_HTTPS_BASE_URL="$public_url"
  export ERP_IMAGE_HTTPS_ROOT="$image_root"
  echo "[ready] Image HTTPS: $ERP_IMAGE_HTTPS_BASE_URL"
  echo "[log] Image Tunnel log: $IMAGE_TUNNEL_LOG"
}

if ! "$PY" -c "import requests, PIL, dotenv, openai, pydantic_ai, opentelemetry.sdk; from importlib.metadata import version; assert version('pydantic-ai-slim') == '2.22.0'; assert version('opentelemetry-sdk') == '1.44.0'" >/dev/null 2>&1; then
  echo "[setup] Installing backend dependencies"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "$ROOT_DIR/requirements.txt"
  "$PY" -m pip install requests pillow python-dotenv
fi

dotenv_value() {
  local key="$1"
  "$PY" - "$ROOT_DIR/config/.env" "$key" <<'PY'
import sys
from pathlib import Path
from dotenv import dotenv_values

path = Path(sys.argv[1])
key = sys.argv[2]
value = dotenv_values(path).get(key) if path.is_file() else ""
print(str(value or "").strip())
PY
}

if [ -f "$ROOT_DIR/config/.env" ]; then
  if [ -z "${ERP_IMAGE_HTTPS_TUNNEL+x}" ]; then
    IMAGE_HTTPS_TUNNEL_MODE="$(dotenv_value ERP_IMAGE_HTTPS_TUNNEL)"
    IMAGE_HTTPS_TUNNEL_MODE="${IMAGE_HTTPS_TUNNEL_MODE:-auto}"
  fi
  if [ -z "${ERP_IMAGE_HTTPS_PORT+x}" ]; then
    IMAGE_HTTPS_PORT="$(dotenv_value ERP_IMAGE_HTTPS_PORT)"
    IMAGE_HTTPS_PORT="${IMAGE_HTTPS_PORT:-8787}"
  fi
  for key in ERP_IMAGE_HTTPS_PROVIDER ERP_IMAGE_HTTPS_BASE_URL ERP_IMAGE_HTTPS_ROOT; do
    if ! printenv "$key" >/dev/null 2>&1; then
      value="$(dotenv_value "$key")"
      if [ -n "$value" ]; then
        export "$key=$value"
      fi
    fi
  done
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
  if [ -n "${IMAGE_TUNNEL_PID:-}" ] && kill -0 "$IMAGE_TUNNEL_PID" >/dev/null 2>&1; then
    kill "$IMAGE_TUNNEL_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${BACKEND_PID:-}" ]; then
    wait "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ]; then
    wait "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${IMAGE_TUNNEL_PID:-}" ]; then
    wait "$IMAGE_TUNNEL_PID" >/dev/null 2>&1 || true
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

start_image_https_tunnel

echo "[start] Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "[start] Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "[log] Backend log:  $BACKEND_LOG"
echo "[log] Frontend log: $FRONTEND_LOG"

echo "[start] Launching backend..."
"$PY" -m erp_web.server >"$BACKEND_LOG" 2>&1 &
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

echo "[hint] Press Ctrl+C to stop all development services."
# Keep script alive while either process is running.
wait "$BACKEND_PID" "$FRONTEND_PID"
