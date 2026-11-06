from __future__ import annotations

import ipaddress
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from erp_web.schemas.requests import RequestValidationError


MAX_JSON_BODY_BYTES = 64 * 1024 * 1024
_JSON_CONTENT_TYPE = "application/json"


def _header(handler: BaseHTTPRequestHandler, name: str) -> str:
    headers = getattr(handler, "headers", None)
    if headers is None:
        return ""
    return str(headers.get(name, "") or "").strip()


def _loopback_hostname(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_host(authority: str) -> str | None:
    if not authority or any(char in authority for char in "/?#"):
        return None
    try:
        parsed = urllib.parse.urlsplit(f"//{authority}")
        # 访问 port 可触发非法端口的 ValueError。
        parsed.port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return parsed.hostname


def validate_request_metadata(handler: BaseHTTPRequestHandler) -> None:
    """限制本机 HTTP 接口只能由回环目标和可信浏览器来源调用。

    没有 ``Origin`` 的桌面客户端、CLI 和 webhook 保持可用；浏览器显式声明的
    跨站来源则在任何路由处理器运行前拒绝。
    """

    host = _header(handler, "Host")
    public = os.environ.get('ERP_PUBLIC_URL', '').strip()
    public_host = urllib.parse.urlsplit(public).hostname if public else None
    allowed_hosts = {item.strip().lower() for item in os.environ.get('ERP_ALLOWED_HOSTS', '').split(',') if item.strip()}
    if public_host:
        allowed_hosts.add(public_host.lower())
    hostname = _parse_host(host)
    if host and not (_loopback_hostname(hostname) or (hostname and hostname.lower() in allowed_hosts)):
        raise RequestValidationError(
            "Host 未在允许的后端地址中",
            status_code=403,
            error_code="UNTRUSTED_HOST",
        )

    # 官方 OAuth 的顶层回跳以服务端保存的一次性 state 校验来源；仅放行此 GET 页面。
    if (getattr(handler, "command", "") == "GET"
            and urllib.parse.urlsplit(getattr(handler, "path", "")).path in {"/auth/alibaba/callback", "/auth/mercadolibre/callback"}
            and _header(handler, "Sec-Fetch-Mode") == "navigate"
            and _header(handler, "Sec-Fetch-Dest") == "document"):
        return

    origin = _header(handler, 'Origin')
    allowed_origins = {item.strip().rstrip('/') for item in os.environ.get('ERP_ALLOWED_ORIGINS', '').split(',') if item.strip()}
    if public:
        allowed_origins.add(public.rstrip('/'))
    fetch_site = _header(handler, "Sec-Fetch-Site").lower()
    if fetch_site == "cross-site" and origin.rstrip('/') not in allowed_origins:
        raise RequestValidationError(
            "不允许浏览器跨站请求",
            status_code=403,
            error_code="CROSS_SITE_REQUEST",
        )

    origin = _header(handler, "Origin")
    if not origin:
        return
    try:
        parsed = urllib.parse.urlsplit(origin)
        parsed.port
    except ValueError as exc:
        raise RequestValidationError(
            "Origin 无效",
            status_code=403,
            error_code="UNTRUSTED_ORIGIN",
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or not (_loopback_hostname(parsed.hostname) or origin.rstrip('/') in allowed_origins)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RequestValidationError(
            "不允许浏览器跨站请求",
            status_code=403,
            error_code="UNTRUSTED_ORIGIN",
        )


def _content_length(handler: BaseHTTPRequestHandler) -> int:
    if _header(handler, "Transfer-Encoding"):
        raise RequestValidationError(
            "不支持 Transfer-Encoding 请求体",
            error_code="UNSUPPORTED_TRANSFER_ENCODING",
        )
    values = getattr(handler.headers, "get_all", lambda _name: None)(
        "Content-Length"
    )
    if values and len(values) > 1:
        raise RequestValidationError(
            "Content-Length 不能重复",
            error_code="INVALID_CONTENT_LENGTH",
        )
    raw_length = _header(handler, "Content-Length") or "0"
    try:
        length = int(raw_length, 10)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(
            "Content-Length 无效",
            error_code="INVALID_CONTENT_LENGTH",
        ) from exc
    if length < 0:
        raise RequestValidationError(
            "Content-Length 无效",
            error_code="INVALID_CONTENT_LENGTH",
        )
    if length > MAX_JSON_BODY_BYTES:
        raise RequestValidationError(
            f"请求体不能超过 {MAX_JSON_BODY_BYTES} 字节",
            status_code=413,
            error_code="REQUEST_BODY_TOO_LARGE",
        )
    return length


def _validate_json_content_type(
    handler: BaseHTTPRequestHandler,
    length: int,
) -> None:
    if length == 0:
        return
    media_type = _header(handler, "Content-Type").split(";", 1)[0].strip().lower()
    if media_type != _JSON_CONTENT_TYPE:
        raise RequestValidationError(
            "POST 请求体必须使用 application/json",
            status_code=415,
            error_code="UNSUPPORTED_CONTENT_TYPE",
        )


def safe_json_body_with_raw(
    handler: BaseHTTPRequestHandler,
) -> tuple[dict[str, Any], bytes]:
    """一次 socket 读取 JSON 请求体，返回解析结果与同一份原始字节。

    Vercel ``build_run_input()`` 需要原始字节，路由契约校验需要解析后的副本；
    两者必须来自同一次读取，不能二次消费 socket。
    """

    validate_request_metadata(handler)
    length = _content_length(handler)
    _validate_json_content_type(handler, length)
    if not length:
        return {}, b""

    raw_bytes = handler.rfile.read(length)
    if len(raw_bytes) != length:
        raise RequestValidationError(
            "请求体不完整",
            error_code="INCOMPLETE_REQUEST_BODY",
        )
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestValidationError(
            "请求体不是合法 JSON",
            error_code="INVALID_JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise RequestValidationError(
            "请求体必须是 JSON 对象",
            error_code="INVALID_JSON_OBJECT",
        )
    return payload, raw_bytes


def safe_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """读取 JSON 对象请求体，并把客户端格式错误稳定映射为 HTTP 输入错误。"""

    payload, _raw = safe_json_body_with_raw(handler)
    return payload


__all__ = [
    "MAX_JSON_BODY_BYTES",
    "safe_json_body",
    "safe_json_body_with_raw",
    "validate_request_metadata",
]
