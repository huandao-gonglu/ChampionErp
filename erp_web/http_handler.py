# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from erp_web.release_guard import release_guard
from . import http_routes
from .http_request import safe_json_body
from .services.external_request_context import capture_request_rejections, current_operation_id
from .services.desktop_version_policy import minimum_update
from .services.account_request import CALLBACK_GET_PATHS, authenticated_request

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


# 已接任务所需的桌面回执、进度读取和用户停止操作，在等待期间仍可用。
_DRAIN_ALLOWED_POST_PATHS = frozenset({
    "/api/desktop/connect", "/api/desktop/poll", "/api/desktop/asset", "/api/desktop/result",
    "/api/desktop/disconnect", "/api/v1/ai-chat/cancel", "/api/wechat/verify",
    "/api/draft-images/state", "/api/load-draft", "/api/load-product",
    "/api/account/login", "/api/account/logout", "/api/internal/newapi/session", "/api/internal/newapi/user",
})


# 查询使用原有持久化确认计划；发布可中断一次回读，不影响已经受理的写入。
_CONFIRMATION_READS = frozenset({"/api/tasks/check", "/api/publish-bus/reconcile", "/api/online-products/reconcile"})


class Handler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        cookie = getattr(self, "account_set_cookie", "")
        if cookie:
            self.send_header("Set-Cookie", cookie)
            self.account_set_cookie = ""
        if not urllib.parse.urlsplit(self.path).path.startswith("/assets/"):
            self._headers_buffer = [line for line in getattr(self, "_headers_buffer", []) if not line.lower().startswith(b"cache-control:")]
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

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

    def send_response(self, code: int, message: str | None = None) -> None:
        """记录本次响应状态：HTTP 公共边界按它裁定 presentation 请求成败。"""

        self.response_status = int(code)
        super().send_response(code, message)

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
        if getattr(self, "external_rejections", None):
            self.send_header("X-External-Request-Blocked", "1")
        if getattr(self, "command", "") == "POST":
            operation_ids = [current_operation_id()]
            if isinstance(data, dict):
                operation_ids.extend(data.get("operation_ids", []) if isinstance(data.get("operation_ids"), list) else [])
                operation_ids.append(data.get("job_id", ""))
                run = data.get("run")
                if isinstance(run, dict):
                    operation_ids.append(run.get("run_id", ""))
                job = data.get("job")
                if isinstance(job, dict):
                    operation_ids.append(job.get("id", ""))
            values = [v for v in operation_ids if isinstance(v, str) and re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", v)]
            if values:
                self.send_header("X-External-Operation-Since", str(getattr(self, "request_started", time.time())))
                self.send_header("X-External-Operation-Ids", ",".join(dict.fromkeys(values[:20])))
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

    def send_sse_headers(
        self,
        headers: dict[str, str],
        status: int = 200,
    ) -> None:
        """开始一个 Server-Sent Events 响应；头部由调用方提供，不设置 Content-Length。"""

        self.send_response(status)
        for keyword, value in headers.items():
            self.send_header(keyword, value)
        self.end_headers()

    def write_sse_chunk(self, chunk: bytes) -> None:
        """逐块写 SSE 并立即 flush，保证增量实时送达。"""

        session = getattr(self, "account_session", None)
        if session is not None and not self.server.account_runtime.store.session_valid(session.token_hash):
            raise BrokenPipeError("登录会话已失效，结束事件流。")
        self.wfile.write(chunk)
        self.wfile.flush()

    def read_body(self) -> dict[str, Any]:
        return safe_json_body(self)

    def _dispatch_business_request(self, dispatch, *, activity="业务请求") -> None:
        with release_guard.admit(activity) as allowed:
            if allowed:
                dispatch(self)
            else:
                self.send_json({"ok": False, "error_code": "RELEASE_DRAINING",
                                "error": "系统正在升级，请稍后再提交。"}, 503)

    def do_GET(self) -> None:
        with authenticated_request(self, public_pages=http_routes.FRONTEND_PAGE_ROUTES) as allowed:
            if allowed:
                with capture_request_rejections() as self.external_rejections:
                    if urllib.parse.urlsplit(self.path).path in CALLBACK_GET_PATHS:
                        self._dispatch_business_request(http_routes.handle_get)
                    else:
                        http_routes.handle_get(self)

    def do_POST(self) -> None:
        self.request_started = time.time()
        with authenticated_request(self, public_pages=http_routes.FRONTEND_PAGE_ROUTES) as allowed:
            if allowed:
                with capture_request_rejections() as self.external_rejections:
                    path = urllib.parse.urlsplit(self.path).path
                    client = self.headers.get("X-Champion-Client")
                    if client and (path == "/api/desktop/connect" or
                                   path not in _DRAIN_ALLOWED_POST_PATHS | _CONFIRMATION_READS):
                        try:
                            floor = minimum_update(client)
                        except ValueError:
                            self.send_json({"ok": False, "error": "桌面版本标识无效。"}, 400)
                            return
                        except (OSError, KeyError, RuntimeError, TypeError):
                            self.send_json({"ok": False, "error": "桌面版本策略暂时不可用。"}, 503)
                            return
                        if floor:
                            self.send_json({"ok": False, "error_code": "DESKTOP_UPDATE_REQUIRED",
                                            "error": f"请升级到 {floor} 或更高版本后继续。"}, 426)
                            return
                    if path in _DRAIN_ALLOWED_POST_PATHS:
                        http_routes.handle_post(self)
                    else:
                        activity = None if path in _CONFIRMATION_READS else "业务请求"
                        self._dispatch_business_request(http_routes.handle_post, activity=activity)
