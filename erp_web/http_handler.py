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
_REQUEST_LINE_PATTERN = re.compile(
    r"(?P<method>[A-Z]+)\s+(?P<target>\S+)\s+(?P<version>HTTP/\d(?:\.\d)?)"
)


def _without_query_from_request_line(value: Any) -> str:
    """从访问日志中的 HTTP request-line 移除 query，保留可诊断的 path。"""

    text = str(value)

    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        path = urllib.parse.urlsplit(target).path or "/"
        return f"{match.group('method')} {path} {match.group('version')}"

    return _REQUEST_LINE_PATTERN.sub(replace, text)


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
