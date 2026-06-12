# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import http_routes

access_logger = logging.getLogger("erp.access")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        try:
            message = format % args
        except TypeError:
            message = format
        access_logger.info("%s - %s", self.address_string(), message)

    def send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self) -> dict[str, Any]:
        return http_routes.safe_json_body(self)

    def do_GET(self) -> None:
        http_routes.handle_get(self)

    def do_POST(self) -> None:
        http_routes.handle_post(self)
