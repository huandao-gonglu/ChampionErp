"""POST /api/v1/ai-chat/runs 的薄路由：预流校验 + 官方 SSE 写入。

路由不导入 Adapter，也不装配 Agent；流生命周期由 facade 返回的 run 持有。
开始输出 SSE 后，异常只能结束连接或成为官方 error chunk，绝不再追加 JSON。
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse

from erp_web.facades import ai_chat_facade
from erp_web.http_request import safe_json_body_with_raw
from erp_web.schemas.requests import validate_request_payload
from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError

from .common import JsonRequestHandler


_logger = logging.getLogger(__name__)

CHAT_RUNS_PATH = "/api/v1/ai-chat/runs"


def handle_chat_run(handler: JsonRequestHandler) -> None:
    payload, raw_body = safe_json_body_with_raw(handler)
    validate_request_payload(payload, endpoint=handler.path)
    try:
        run = ai_chat_facade.run_chat_stream(raw_body)
    except VercelUiProtocolError as exc:
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": exc.code,
            },
            exc.status_code,
        )
        return
    # 报告 A-02：registry/claim 已在 run_chat_stream() 内领取。此后写响应头
    # 或创建 loop 的任何失败都必须进入确定性收尾（claim failed + 释放 run
    # lock），否则 conversation 永久锁死（后续请求全部 AI_CHAT_RUN_ACTIVE）。
    # stream 一旦启动，收尾由进程级 runner 上的 producer 负责，此处不重复。
    try:
        handler.send_sse_headers(run.sse_headers())
    except BaseException:
        run.abort_before_stream()
        raise
    try:
        loop = asyncio.new_event_loop()
    except BaseException:
        run.abort_before_stream()
        raise
    try:
        loop.run_until_complete(run.stream(handler.write_sse_chunk))
    except Exception:
        # stream() 在提交 producer 之前失败时已自行执行 abort_before_stream；
        # 提交之后的异常由 producer 收尾，这里只记录。
        _logger.exception("AI chat SSE 流异常结束：%s", run.conversation_id)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


POST_HANDLERS = {
    CHAT_RUNS_PATH: handle_chat_run,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: urllib.parse.ParseResult) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True


__all__ = [
    "CHAT_RUNS_PATH",
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "handle_post",
]
