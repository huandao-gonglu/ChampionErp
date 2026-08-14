# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from .context import build_default_context, get_context, set_context
from .facades.global_agent_facade import build_global_task_controller
from .http_handler import Handler
from .logging_config import configure_logging
from .runtime_units.publish_adapter import resume_pending_publish_jobs
from .services.browser_debug_service import pick_web_port
from .services.config_service import load_env

logger = logging.getLogger(__name__)
GLOBAL_TASK_RECOVERY_INTERVAL_SECONDS = 2.5


def start_global_task_recovery_worker(
    *,
    stop_event: threading.Event | None = None,
    interval_seconds: float = GLOBAL_TASK_RECOVERY_INTERVAL_SECONDS,
) -> threading.Thread:
    """低频协调可恢复任务；执行 claim 保证不会与 HTTP 写命令并行推进。"""

    context = get_context()
    stopped = stop_event or threading.Event()

    def recover() -> None:
        controller = build_global_task_controller(context)
        while not stopped.is_set():
            try:
                controller.recover_unfinished_tasks()
            except Exception:
                logger.exception("协调未完成的全局任务失败")
            stopped.wait(max(0.1, float(interval_seconds)))

    worker = threading.Thread(
        target=recover,
        name="global-task-recovery",
        daemon=True,
    )
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
    start_global_task_recovery_worker()
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
