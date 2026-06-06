from __future__ import annotations

import html
import urllib.parse
from pathlib import Path
from typing import Any


FRONTEND_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

FILE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def send_raw(handler: Any, raw: bytes, content_type: str, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def serve_frontend_asset(handler: Any, parsed: urllib.parse.ParseResult, app: Any) -> None:
    rel_path = urllib.parse.unquote(parsed.path.lstrip("/"))
    try:
        path = (app.FRONT_DIST_DIR / rel_path).resolve()
        root = app.FRONT_DIST_DIR.resolve()
        if not str(path).startswith(str(root)) or not path.is_file():
            raise FileNotFoundError
        send_raw(handler, path.read_bytes(), FRONTEND_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
    except Exception:
        handler.send_response(404)
        handler.end_headers()


def serve_file(handler: Any, parsed: urllib.parse.ParseResult, app: Any) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    path = Path(urllib.parse.unquote(params.get("path", [""])[0]))
    try:
        path = path.resolve()
        output_roots = [
            app.DATA_DIR.resolve(),
            app.IMAGES_DIR.resolve(),
            app.CACHE_DIR.resolve(),
            app.LOGS_DIR.resolve(),
            app.EXPORTS_DIR.resolve(),
            app.OUTPUT_DIR.resolve(),
            (app.DIST_DIR / "output").resolve(),
        ]
        if not any(str(path).startswith(str(root)) for root in output_roots) or not path.exists():
            raise FileNotFoundError
        send_raw(handler, path.read_bytes(), FILE_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
    except Exception:
        handler.send_response(404)
        handler.end_headers()


def serve_ml_auth_page(handler: Any) -> None:
    raw = (
        "<html><body style=\"font-family:Arial;padding:24px\">"
        "<h2>Mercado Libre 授权说明</h2>"
        "<p>ERP 默认使用 https://example.com/callback 作为 Redirect URI，与现有本地软件保持一致。</p>"
        "<p>点击“生成授权链接”后，用当前登录店铺的浏览器打开链接；授权完成后即使 example.com 页面打不开也没关系，请复制地址栏中包含 code= 的完整回调地址。</p>"
        "<p>Access Token 和 Refresh Token 会由 ERP 用 code 自动换取并保存在本机私有配置中。</p>"
        "</body></html>"
    ).encode("utf-8")
    send_raw(handler, raw, "text/html; charset=utf-8")


def serve_store_help_page(handler: Any, platform: str) -> None:
    if platform == "wildberries":
        title = "Wildberries 授权说明"
        body = (
            "<p>Wildberries 当前使用卖家后台生成的 API token，不走 OAuth 回调。</p>"
            "<p>请在 WB 卖家后台生成 Content API Token，复制到 ERP 的 content_token 字段。</p>"
            "<p>subject_id 是商品主体/类目 ID；如果暂时不知道，可以先保存 token，后续通过类目读取功能补齐。</p>"
        )
    else:
        title = "Ozon 授权说明"
        body = (
            "<p>Ozon 当前使用 Seller API 的 Client ID 和 API Key。</p>"
            "<p>请在 Ozon Seller 后台的 API 设置中创建或查看 API Key，然后填入 ERP。</p>"
            "<p>保存后点击“测试授权”，ERP 会尝试读取店铺或仓库信息来验证授权。</p>"
        )
    raw = f"<html><body style=\"font-family:Arial;padding:24px\"><h2>{title}</h2>{body}</body></html>".encode("utf-8")
    send_raw(handler, raw, "text/html; charset=utf-8")


def handle_ml_callback(handler: Any, parsed: urllib.parse.ParseResult, app: Any) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [""])[0] or ""
    masked_code = app.mask_secret(code)
    status = "received"
    message = "授权 code 已接收。"
    try:
        if code:
            app.exchange_mercadolibre_code_from_body(
                {
                    "code_or_url": code,
                    "app_id": params.get("app_id", [""])[0],
                    "app_secret": params.get("app_secret", [""])[0],
                    "client_secret": params.get("client_secret", [""])[0],
                    "redirect_uri": params.get("redirect_uri", [""])[0],
                    "code_verifier": params.get("code_verifier", [""])[0],
                    "site_id": params.get("site_id", [""])[0],
                }
            )
            status = "exchanged"
            message = "授权 code 已接收，并已尝试自动换取 token。"
        else:
            status = "missing_code"
            message = "没有在回跳 URL 中找到 code 参数。"
    except Exception as exc:
        status = "manual_required"
        message = f"ERP 已收到 code，但自动换 token 未完成：{exc}"

    safe_code = html.escape(code)
    safe_masked_code = html.escape(masked_code or "未收到")
    safe_message = html.escape(message)
    erp_url = "/auth"
    raw = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Mercado Libre 授权 code 已收到</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f5f7fa; color: #1f2937; padding: 32px; }}
    .card {{ max-width: 760px; margin: 0 auto; background: white; border-radius: 14px; padding: 28px; box-shadow: 0 12px 28px rgba(15,23,42,.08); }}
    .badge {{ display: inline-block; background: #dbeafe; color: #1d4ed8; border-radius: 999px; padding: 6px 10px; font-size: 12px; font-weight: 700; }}
    .code {{ margin-top: 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; font-family: Consolas, monospace; }}
    button, a.button {{ display: inline-block; border: 0; border-radius: 10px; padding: 10px 14px; margin-right: 8px; background: #1d4ed8; color: white; text-decoration: none; cursor: pointer; }}
    .secondary {{ background: #e2e8f0; color: #0f172a; }}
    .note {{ color: #64748b; font-size: 14px; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <span class="badge">{html.escape(status)}</span>
    <h1>Mercado Libre 授权 code 已收到</h1>
    <p>{safe_message}</p>
    <p class="note">如果 ERP 没有自动换 token，请复制下面的 code 回 ERP 授权页手动粘贴。code 是一次性的，有效时间短，使用后会失效。</p>
    <div class="code">脱敏显示：{safe_masked_code}</div>
    <textarea id="mlCode" style="position:absolute;left:-9999px">{safe_code}</textarea>
    <div style="margin-top:18px">
      <button onclick="navigator.clipboard && navigator.clipboard.writeText(document.getElementById('mlCode').value)">复制 code</button>
      <a class="button secondary" href="{erp_url}">返回 ERP 授权页</a>
    </div>
  </div>
</body>
</html>
""".encode("utf-8")
    send_raw(handler, raw, "text/html; charset=utf-8")
