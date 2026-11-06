# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from collections.abc import Callable
from http.server import ThreadingHTTPServer

from .context import build_default_context, get_context, set_context
from .http_handler import Handler
from .logging_config import configure_logging
from .services.config_service import load_env
from .stores.credential_layout import assert_credentials_migrated
from .account_runtime import AccountRuntime

logger = logging.getLogger(__name__)


def main(*, on_ready: Callable[[str], None] | None = None,
         stop_event: threading.Event | None = None) -> None:
    # build_default_context() 构造 ErpDatabase：schema 初始化在构造期完成。
    set_context(build_default_context())
    paths = get_context().paths
    assert_credentials_migrated(paths.app_dir)
    # 日志配置也允许写在 config/.env；环境变量仍具有更高优先级。
    load_env(paths.app_dir)
    log_file = configure_logging(paths.app_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    accounts = AccountRuntime(get_context())
    if accounts.store.admin() is None:
        raise RuntimeError("请先运行 python -m erp_web.accounts init-admin 初始化管理员。")
    host = os.environ.get('ERP_BIND_HOST', '127.0.0.1')
    port = paths.web_port
    server = ThreadingHTTPServer((host, port), Handler)
    server.account_runtime = accounts
    accounts.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    logger.info("ERP running at %s", url)
    logger.info("Backend log file: %s", log_file)
    if (
        os.environ.get("ERP_NO_BROWSER") != "1"
        and os.environ.get("ERP_SKIP_OPEN_BROWSER") != "1"
    ):
        try:
            webbrowser.open(url)
        except Exception:
            logger.exception("Failed to open browser for %s", url)
    try:
        if stop_event is not None:
            def stop_server():
                stop_event.wait()
                server.shutdown()

            threading.Thread(target=stop_server, name="erp-server-stop", daemon=True).start()
        if on_ready is not None:
            on_ready(url)
        server.serve_forever()
    finally:
        if stop_event is not None:
            stop_event.set()
        accounts.close()
        server.server_close()


if __name__ == "__main__":
    main()
