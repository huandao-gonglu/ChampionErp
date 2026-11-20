"""主 Agent 的领域工具、原生会话与后台 Job 组合根。"""

import json

from erp_web.context import get_context
from erp_web.schemas.chat_conversations import is_global_id, reply_prefix
from erp_web.services.chat_titles import chat_titles, schedule_chat_title
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
    chat_config = active.config.load_app_config()
    return GlobalAgentChatService(
        title_scheduler=lambda service, key, messages: schedule_chat_title(active, service, key, messages),
        app_dir=active.paths.app_dir,
        app_config=chat_config,
        message_store=active.pydantic_messages,
        toolset=build_global_chat_toolset(active, chat_config=chat_config),
        call_store=active.agent_calls,
        approval_mode_reader=active.config.ai_tool_approval_mode,
    )


def named_conversations(limit, offset, chats_only):
    context = get_context()
    pins = context.conversation_titles.pinned_ids()
    rows = context.db.list_pydantic_message_histories(limit=limit, chats_only=chats_only, offset=offset, pinned_ids=pins)
    titles = chat_titles(context, [row["conversation_id"] for row in rows])
    return [{**row, **titles.get(row["conversation_id"], {}), "pinned": row["conversation_id"] in pins} for row in rows]


def manage_conversation(key, action, title=None):
    if not is_global_id(key) or action not in ("pin", "unpin", "rename", "delete"):
        raise VercelUiProtocolError(400, "CHAT_ACTION_INVALID", "对话或操作无效。")
    if action == "rename":
        title = (title or "").strip()
        if not 1 <= len(title) <= 80:
            raise VercelUiProtocolError(400, "CHAT_TITLE_INVALID", "请输入 1 至 80 个字的对话名称。")
    context = get_context()
    titles = context.conversation_titles
    with context.chat_runs.input_guard():
        if not context.pydantic_messages.get_version(key):
            if action == "delete" and titles.is_deleted(key):
                return {"ok": True}
            raise VercelUiProtocolError(404, "CHAT_NOT_FOUND", "对话已删除或不存在。")
        if action == "delete":
            if context.wechat_store.state()["conversation_id"] == key:
                raise VercelUiProtocolError(409, "CHAT_CHANNEL_ACTIVE", "请先在微信切换对话，再删除这段历史。")
            scopes = [key, *context.agent_calls.parallel_runs(key)]
            if any(context.chat_runs.is_active(scope) for scope in scopes) or not context.db.delete_idle_chat(
                key, reply_prefix(key) + "*", titles.path,
            ):
                raise VercelUiProtocolError(409, "CHAT_STILL_ACTIVE", "对话中还有任务或待处理消息，请先完成或停止后再删除。")
            for scope in scopes:
                context.agent_calls.notify_changed(scope)
        elif action == "rename":
            titles.rename(key, title)
        else:
            titles.set_pin(key, action == "pin")
    return {"ok": True}


def conversation_title(key):
    return chat_titles(get_context(), [key]).get(key, {"title": "", "title_pending": False})


def build_conversation_service(context=None):
    from erp_web.services.chat_conversation_service import ChatConversationService

    active = context or get_context()
    return ChatConversationService(
        chat_service=build_global_chat_service(active), claim_store=active.chat_turn_claims,
        run_registry=active.chat_runs, call_store=active.agent_calls,
        deleted_reader=active.conversation_titles.is_deleted,
        empty_owner_reader=active.wechat_store.owns_conversation,
        empty_owner_writer=active.wechat_store.reserve_conversation,
    )


def build_ui_service(context=None):
    active = context or get_context()
    job_readers = build_job_status_readers(active)

    def read_job_progress(conversation_id, call_id):
        receipt = active.agent_calls.receipt(conversation_id, call_id)
        if not receipt or receipt["status"] != "waiting_job":
            return ""
        job = json.loads(receipt["job_json"])
        reader = job_readers.get(job["job_type"])
        return reader.read_job_state(job["job_id"]).summary if reader else ""

    return VercelAiUiService(
        job_progress_reader=read_job_progress,
        chat_service=build_global_chat_service(active),
        claim_store=active.chat_turn_claims,
        run_registry=active.chat_runs,
        call_store=active.agent_calls,
        approval_session=active.approval_session,
        deleted_reader=active.conversation_titles.is_deleted,
        empty_owner_reader=active.wechat_store.owns_conversation,
    )


def build_job_service(context=None):
    active = context or get_context()
    return AgentJobService(
        ui_service=build_conversation_service(active),
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


def update_queued_message(conversation_id, message_id, action):
    from erp_web.services.chat_queue_service import act_on_queue

    act_on_queue(build_ui_service(), conversation_id, message_id, action)
    return {"ok": True}


def cancel_chat_run(conversation_id, message_id):
    return build_ui_service().cancel_run(conversation_id, message_id)


def fork_chat_conversation(conversation_id, message_id):
    return build_ui_service().fork_conversation(conversation_id, message_id)


def ui_messages_payload(conversation_id):
    try:
        return build_ui_service().dump_ui_messages(conversation_id), 200
    except VercelUiProtocolError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, exc.status_code
