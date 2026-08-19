"""AI chat 的 composition root：从 AppContext 装配 focused services。

实时流入口只负责装配与预流错误映射；SSE 生命周期由
``VercelAiUiService`` 返回的 run 持有，路由不接触 Adapter 或 Agent。
"""

from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.facades.global_task_facade import build_global_chat_toolset
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.vercel_ai_ui_service import (
    VercelAiChatRun,
    VercelAiUiService,
    VercelUiProtocolError,
)


Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]


def _build_ui_service() -> VercelAiUiService:
    context = get_context()
    app_config = context.config.load_app_config()
    chat_service = GlobalAgentChatService(
        app_dir=context.paths.app_dir,
        app_config=app_config,
        message_store=context.pydantic_messages,
        toolset=build_global_chat_toolset(context),
    )
    return VercelAiUiService(
        chat_service=chat_service,
        claim_store=context.chat_turn_claims,
        run_registry=context.chat_runs,
    )


def run_chat_stream(raw_body: bytes) -> VercelAiChatRun:
    """POST /api/v1/ai-chat/runs 的预流入口；失败抛 VercelUiProtocolError。"""

    service = _build_ui_service()
    return service.prepare_run(raw_body)


def ui_messages_payload(conversation_id: str) -> ResponseWithStatus:
    """GET /ui-messages：官方派生的只读 UIMessage[]。"""

    try:
        service = _build_ui_service()
        payload = service.dump_ui_messages(conversation_id)
    except VercelUiProtocolError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, exc.status_code
    return dict(payload), 200


__all__ = [
    "run_chat_stream",
    "ui_messages_payload",
]
