# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import webbrowser
from http.server import ThreadingHTTPServer

from .http_handler import Handler
from .runtime import OUTPUT_DIR, WEB_PORT, pick_web_port


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    port = pick_web_port(WEB_PORT)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ERP running at {url}")
    if os.environ.get("ERP_NO_BROWSER") != "1" and os.environ.get("ERP_SKIP_OPEN_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    server.serve_forever()
