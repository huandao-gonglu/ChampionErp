"""主 Agent 的领域工具、原生会话与后台 Job 组合根。"""

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import (
    build_global_chat_toolset,
    build_job_status_readers,
)
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.vercel_ai_ui_service import (
    VercelAiUiService,
    VercelUiProtocolError,
)
from erp_web.services.ai_conversation_event_stream import ConversationEventStream
from erp_web.services.agent_job_service import AgentJobService


def build_global_chat_service(context=None):
    active = context or get_context()
    return GlobalAgentChatService(
        app_dir=active.paths.app_dir,
        app_config=active.config.load_app_config(),
        message_store=active.pydantic_messages,
        toolset=build_global_chat_toolset(active),
        call_store=active.agent_calls,
    )


def build_ui_service(context=None):
    active = context or get_context()
    return VercelAiUiService(
        chat_service=build_global_chat_service(active),
        claim_store=active.chat_turn_claims,
        run_registry=active.chat_runs,
        call_store=active.agent_calls,
        approval_session=active.approval_session,
    )


def build_job_service(context=None):
    active = context or get_context()
    return AgentJobService(
        ui_service=build_ui_service(active),
        job_readers=build_job_status_readers(active),
    )


def build_conversation_event_stream(conversation_id, *, after_history_version):
    return ConversationEventStream(
        conversation_id=conversation_id,
        after_history_version=after_history_version,
        message_store=get_context().pydantic_messages,
    )


def run_chat_stream(raw_body, *, approval_token=""):
    return build_ui_service().prepare_run(raw_body, approval_token=approval_token)


def cancel_chat_run(conversation_id, message_id):
    return build_ui_service().cancel_run(conversation_id, message_id)


def ui_messages_payload(conversation_id):
    try:
        return build_ui_service().dump_ui_messages(conversation_id), 200
    except VercelUiProtocolError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, exc.status_code
