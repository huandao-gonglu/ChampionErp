#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_ROOT="${ERP_IMAGE_HTTPS_ROOT:-$PROJECT_DIR/data/images/public}"
IMAGE_PORT="${ERP_IMAGE_HTTPS_PORT:-8787}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[错误] 未找到 cloudflared，请先安装后重试。" >&2
  exit 1
fi

mkdir -p "$IMAGE_ROOT"

cleanup() {
  local code=$?
  if [ -n "${CLOUDFLARED_PID:-}" ] && kill -0 "$CLOUDFLARED_PID" >/dev/null 2>&1; then
    kill "$CLOUDFLARED_PID" >/dev/null 2>&1 || true
    wait "$CLOUDFLARED_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${STATIC_SERVER_PID:-}" ] && kill -0 "$STATIC_SERVER_PID" >/dev/null 2>&1; then
    kill "$STATIC_SERVER_PID" >/dev/null 2>&1 || true
    wait "$STATIC_SERVER_PID" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

echo "[启动] 图片静态目录：$IMAGE_ROOT"
echo "[启动] 本地文件服务：http://127.0.0.1:$IMAGE_PORT"
"$PYTHON_BIN" -m http.server "$IMAGE_PORT" --bind 127.0.0.1 --directory "$IMAGE_ROOT" &
STATIC_SERVER_PID=$!

echo "[提示] 将 cloudflared 输出的 https://*.trycloudflare.com 地址写入："
echo "       ERP_IMAGE_HTTPS_PROVIDER=local_static"
echo "       ERP_IMAGE_HTTPS_BASE_URL=https://*.trycloudflare.com"
echo "[提示] 修改 config/.env 后重启 ERP 后端，再执行 Ozon 发布预检。"

cloudflared tunnel --url "http://127.0.0.1:$IMAGE_PORT" &
CLOUDFLARED_PID=$!
wait "$CLOUDFLARED_PID"
