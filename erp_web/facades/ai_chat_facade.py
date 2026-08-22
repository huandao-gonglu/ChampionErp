"""AI chat 的 composition root：从 AppContext 装配 focused services。

实时流入口只负责装配与预流错误映射；SSE 生命周期由
``VercelAiUiService`` 返回的 run 持有，路由不接触 Adapter 或 Agent。
"""

from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.facades.global_task_facade import build_global_chat_toolset
from erp_web.services.ai_conversation_event_stream import ConversationEventStream
from erp_web.services.ai_conversation_outbox_publisher import (
    AiConversationOutboxPublisher,
)
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.global_task_continuation_service import (
    GlobalTaskContinuationService,
)
from erp_web.services.vercel_ai_ui_service import (
    VercelAiChatRun,
    VercelAiUiService,
    VercelUiProtocolError,
)


Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]


def build_global_chat_service(
    context: Any | None = None,
) -> GlobalAgentChatService:
    """组合唯一 ``global.chat`` 主 Agent service（初始 run 与 continuation 共用）。"""

    active_context = context or get_context()
    return GlobalAgentChatService(
        app_dir=active_context.paths.app_dir,
        app_config=active_context.config.load_app_config(),
        message_store=active_context.pydantic_messages,
        toolset=build_global_chat_toolset(active_context),
    )


def build_continuation_service(
    context: Any | None = None,
) -> GlobalTaskContinuationService:
    """组合后台 continuation owner；与主 Agent 共用同一 chat service。"""

    active_context = context or get_context()
    return GlobalTaskContinuationService(
        chat_service=build_global_chat_service(active_context),
        task_store=active_context.global_tasks,
        deferred_links=active_context.deferred_task_links,
        message_store=active_context.pydantic_messages,
        event_outbox=active_context.ai_event_outbox,
        event_bus=active_context.conversation_event_bus,
        run_registry=active_context.chat_runs,
    )


def build_outbox_publisher(
    context: Any | None = None,
) -> AiConversationOutboxPublisher:
    """组合官方编码事件 outbox 的可靠后台投递器。"""

    active_context = context or get_context()
    return AiConversationOutboxPublisher(
        event_outbox=active_context.ai_event_outbox,
        event_bus=active_context.conversation_event_bus,
    )


def _build_ui_service() -> VercelAiUiService:
    context = get_context()
    return VercelAiUiService(
        chat_service=build_global_chat_service(context),
        claim_store=context.chat_turn_claims,
        run_registry=context.chat_runs,
        deferred_links=context.deferred_task_links,
        event_outbox=context.ai_event_outbox,
        event_bus=context.conversation_event_bus,
    )


def build_conversation_event_stream(
    conversation_id: str,
    *,
    after_history_version: int,
) -> ConversationEventStream:
    """组合活动 conversation 的后台事件订阅（SSE）。"""

    context = get_context()
    return ConversationEventStream(
        conversation_id=conversation_id,
        after_history_version=after_history_version,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=context.conversation_event_bus,
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
    "build_continuation_service",
    "build_conversation_event_stream",
    "build_global_chat_service",
    "build_outbox_publisher",
    "run_chat_stream",
    "ui_messages_payload",
]
