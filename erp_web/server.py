# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import webbrowser
from http.server import ThreadingHTTPServer

from .http_handler import Handler
from .logging_config import configure_logging
from .runtime import OUTPUT_DIR, WEB_PORT, pick_web_port

logger = logging.getLogger(__name__)


def main() -> None:
    log_file = configure_logging()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    port = pick_web_port(WEB_PORT)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    logger.info("ERP running at %s", url)
    logger.info("Backend log file: %s", log_file)
    if os.environ.get("ERP_NO_BROWSER") != "1" and os.environ.get("ERP_SKIP_OPEN_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            logger.exception("Failed to open browser for %s", url)
    server.serve_forever()


if __name__ == "__main__":
    main()
