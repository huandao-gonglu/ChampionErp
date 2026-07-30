# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import webbrowser
from http.server import ThreadingHTTPServer

from .context import build_default_context, get_context, set_context
from .http_handler import Handler
from .logging_config import configure_logging
from .services.browser_debug_service import pick_web_port
from .runtime_units.publish_adapter import resume_pending_publish_jobs

logger = logging.getLogger(__name__)


def main() -> None:
    # build_default_context() 构造 ErpDatabase：schema 初始化在构造期完成。
    set_context(build_default_context())
    paths = get_context().paths
    log_file = configure_logging(paths.app_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resume_pending_publish_jobs()
    port = pick_web_port(paths.web_port)
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
