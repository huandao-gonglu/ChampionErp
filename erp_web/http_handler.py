# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import http_routes
from .http_request import safe_json_body

access_logger = logging.getLogger("erp.access")
response_logger = logging.getLogger("erp.http.response")
_REQUEST_LINE_PATTERN = re.compile(
    r"(?P<method>[A-Z]+)\s+(?P<target>\S+)\s+(?P<version>HTTP/\d(?:\.\d)?)"
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|"
    r"password|secret|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:code|token|key|secret|state)=)[^&\s]+"
)


def _without_query_from_request_line(value: Any) -> str:
    """从访问日志中的 HTTP request-line 移除 query，保留可诊断的 path。"""

    text = str(value)

    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        path = urllib.parse.urlsplit(target).path or "/"
        return f"{match.group('method')} {path} {match.group('version')}"

    return _REQUEST_LINE_PATTERN.sub(replace, text)


def _safe_log_text(value: Any, *, limit: int = 500) -> str:
    """压缩并脱敏可诊断文本，禁止把凭据写入普通日志。"""

    text = " ".join(str(value or "").split())
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1=[REDACTED]", text)
    text = _SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", text)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _response_diagnostics(data: Any) -> dict[str, Any]:
    """只投影稳定错误字段，不记录任意响应正文。"""

    if not isinstance(data, dict):
        return {
            "ok": None,
            "error_code": "",
            "error_stage": "",
            "retryable": None,
            "message": "",
        }
    failure = data.get("failure") if isinstance(data.get("failure"), dict) else {}
    return {
        "ok": data.get("ok") if isinstance(data.get("ok"), bool) else None,
        "error_code": _safe_log_text(
            data.get("error_code") or failure.get("code"),
            limit=120,
        ),
        "error_stage": _safe_log_text(failure.get("stage"), limit=80),
        "retryable": (
            failure.get("retryable")
            if isinstance(failure.get("retryable"), bool)
            else None
        ),
        "message": _safe_log_text(
            data.get("error") or data.get("message") or failure.get("message")
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        safe_args = tuple(
            _without_query_from_request_line(arg) if isinstance(arg, str) else arg
            for arg in args
        )
        try:
            message = format % safe_args
        except TypeError:
            message = format
        message = _without_query_from_request_line(message)
        access_logger.info("%s - %s", self.address_string(), message)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """写访问日志，但不记录可能包含 OAuth code/秘密的 query。"""

        path = urllib.parse.urlsplit(getattr(self, "path", "")).path or "/"
        self.log_message(
            '"%s %s %s" %s %s',
            getattr(self, "command", ""),
            path,
            getattr(self, "request_version", ""),
            code,
            size,
        )

    def send_json(self, data: Any, status: int = 200) -> None:
        path = urllib.parse.urlsplit(getattr(self, "path", "")).path or "/"
        diagnostics = _response_diagnostics(data)
        if status >= 400:
            log_method = response_logger.error if status >= 500 else response_logger.warning
            log_method(
                "HTTP JSON failure path=%s status=%s ok=%s error_code=%s "
                "error_stage=%s retryable=%s",
                path,
                status,
                diagnostics["ok"],
                diagnostics["error_code"] or "-",
                diagnostics["error_stage"] or "-",
                diagnostics["retryable"],
            )
            response_logger.debug(
                "HTTP JSON failure detail path=%s status=%s error_code=%s message=%s",
                path,
                status,
                diagnostics["error_code"] or "-",
                diagnostics["message"] or "-",
            )
        else:
            response_logger.debug(
                "HTTP JSON response path=%s status=%s ok=%s",
                path,
                status,
                diagnostics["ok"],
            )
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_ndjson(self, items: list[dict[str, Any]], status: int = 200) -> None:
        raw = "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            for item in items
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self) -> dict[str, Any]:
        return safe_json_body(self)

    def do_GET(self) -> None:
        http_routes.handle_get(self)

    def do_POST(self) -> None:
        http_routes.handle_post(self)
