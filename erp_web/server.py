# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from .context import build_default_context, get_context, set_context
from .facades.ai_chat_facade import build_job_service
from .http_handler import Handler
from .logging_config import configure_logging
from .runtime_units.publish_adapter import resume_pending_publish_jobs
from .services.browser_debug_service import pick_web_port
from .services.config_service import load_env

logger = logging.getLogger(__name__)


def start_agent_job_worker(*, stop_event=None, interval_seconds=1.0):
    context = get_context()
    stopped = stop_event or threading.Event()
    service = build_job_service(context)
    context.agent_calls.mark_interrupted_writes()

    def recover():
        try:
            while not stopped.is_set():
                try:
                    service.scan()
                except Exception:
                    logger.exception("领取或对账后台工具失败")
                stopped.wait(max(0.1, float(interval_seconds)))
        finally:
            service.close()

    worker = threading.Thread(target=recover, name="erp-job-dispatch", daemon=True)
    worker.start()
    return worker


def main() -> None:
    # build_default_context() 构造 ErpDatabase：schema 初始化在构造期完成。
    set_context(build_default_context())
    paths = get_context().paths
    # 日志配置也允许写在 config/.env；环境变量仍具有更高优先级。
    load_env(paths.app_dir)
    log_file = configure_logging(paths.app_dir)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resume_pending_publish_jobs()
    port = pick_web_port(paths.web_port)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    start_agent_job_worker()
    url = f"http://127.0.0.1:{port}/"
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
    server.serve_forever()


if __name__ == "__main__":
    main()
