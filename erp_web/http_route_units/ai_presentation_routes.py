"""通用 AI presentation 入口：reserve / status / stream。

docs/aiworkpage.md §5：

- ``POST /api/v1/ai-presentations``：预留一次展示（短 TTL）。不启动 Agent、
  不读取业务数据、不选择能力；``display_title`` 仅用于 UI，长度受限并清洗。
- ``GET /api/v1/ai-presentations/{presentation_id}``：只含展示元数据的状态
  读取（含脱敏展示错误）；不返回业务 result。
- ``GET /api/v1/ai-presentations/{presentation_id}/stream``：官方 Vercel UI
  SSE observe 流。领取单 presentation lease，从游标 0 重放已缓冲的官方编码
  chunk，实时发送后续 chunk，关闭且读完后结束；未知、过期或 lease 已占用
  返回 204（Vercel reconnect 约定的“无可用流”）。浏览器断开只释放 lease，
  不影响业务请求和 Agent。

业务结果始终来自原业务 API；本模块不提供通用 result endpoint。
"""

from __future__ import annotations

import logging
import urllib.parse

from erp_web.context import get_context
from erp_web.schemas.requests import validate_request_payload
from erp_web.services.ai_presentation_service import (
    presentation_sse_headers,
    reserve_presentation,
)

from .common import JsonRequestHandler


_logger = logging.getLogger(__name__)

GET_PREFIX = "/api/v1/ai-presentations"
RESERVE_PATH = "/api/v1/ai-presentations"
STREAM_SUFFIX = "/stream"


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _presentation_id(path: str, *, stream: bool) -> str | None:
    """从路径提取并清洗 presentation_id；非法返回 None。"""

    working = path
    if stream:
        if not working.endswith(STREAM_SUFFIX):
            return None
        working = working[: -len(STREAM_SUFFIX)]
    suffix = working[len(GET_PREFIX) :]
    if not suffix.startswith("/"):
        return None
    encoded = suffix[1:]
    if not encoded or "/" in encoded:
        return None
    presentation_id = urllib.parse.unquote(encoded).strip()
    if not presentation_id or "/" in presentation_id or "\\" in presentation_id:
        return None
    if _has_control_chars(presentation_id):
        return None
    return presentation_id


def handle_reserve(handler: JsonRequestHandler) -> None:
    """预留一次展示；立即返回 descriptor，不执行 Agent。"""

    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload = reserve_presentation(
        get_context().ai_presentations,
        display_title=body.get("display_title"),
    )
    handler.send_json(payload)


def handle_presentation_status(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """通用展示状态读取；只含元数据，不含业务结果。"""

    presentation_id = _presentation_id(parsed.path, stream=False)
    if presentation_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的 AI 展示操作。"}, 404)
        return
    payload = get_context().ai_presentations.status_payload(presentation_id)
    if payload is None:
        handler.send_json(
            {
                "ok": False,
                "error": "AI 展示不存在或已过期。",
                "error_code": "AI_PRESENTATION_NOT_FOUND",
            },
            404,
        )
        return
    handler.send_json(
        {
            "ok": True,
            "presentation_id": payload["presentation_id"],
            "conversation_id": payload["conversation_id"],
            "display_title": payload["display_title"],
            "status": payload["status"],
            "terminal": payload["terminal"],
            "had_agent_run": payload["had_agent_run"],
            "error_code": payload["error_code"],
            "error_message": payload["error_message"],
        }
    )


def handle_presentation_stream(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """官方 Vercel UI SSE observe 流；只消费 registry 的官方 chunk。"""

    presentation_id = _presentation_id(parsed.path, stream=True)
    if presentation_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的 AI 展示操作。"}, 404)
        return
    registry = get_context().ai_presentations
    descriptor = registry.descriptor(presentation_id)
    if descriptor is None:
        # Vercel reconnect 约定：204 表示没有可用流。
        handler.send_response(204)
        handler.end_headers()
        return
    if not registry.acquire_lease(presentation_id):
        # 唯一 presentation lease 已被占用，或 presentation 已过期。
        handler.send_response(204)
        handler.end_headers()
        return
    try:
        handler.send_sse_headers(
            presentation_sse_headers(str(descriptor["conversation_id"]))
        )
        try:
            for chunk in registry.iter_chunks(presentation_id):
                handler.write_sse_chunk(chunk)
        except OSError:
            # 订阅断开只释放 lease，不影响业务请求和 Agent。
            _logger.info("AI 展示 observe 连接断开：%s", presentation_id)
    finally:
        registry.release_lease(presentation_id)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if not parsed.path.startswith(f"{GET_PREFIX}/"):
        return False
    if parsed.path.endswith(STREAM_SUFFIX):
        handle_presentation_stream(handler, parsed)
    else:
        handle_presentation_status(handler, parsed)
    return True


POST_HANDLERS = {
    RESERVE_PATH: handle_reserve,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True


DYNAMIC_GET_HANDLERS = {
    f"{GET_PREFIX}/<presentation_id>": handle_presentation_status,
    f"{GET_PREFIX}/<presentation_id>{STREAM_SUFFIX}": handle_presentation_stream,
}

__all__ = [
    "DYNAMIC_GET_HANDLERS",
    "GET_PREFIX",
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "RESERVE_PATH",
    "STREAM_SUFFIX",
    "handle_get",
    "handle_post",
    "handle_presentation_status",
    "handle_presentation_stream",
    "handle_reserve",
]
