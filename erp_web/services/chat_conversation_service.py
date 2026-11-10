"""网页与微信共用的输入接收和原生运行投递；不实现 Agent 循环或消息编码。"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

from pydantic_ai import RunCancelled
from pydantic_ai.messages import ModelRequest, UserPromptPart

from erp_web.schemas.chat_conversations import is_global_id, reply_root
from erp_web.services.ai_agent_factory import AiAgentExecutionError
from erp_web.services.ai_run_cancellation import bind_cancellation_token
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_ACTOR_ID, GLOBAL_CHAT_TENANT_ID, GLOBAL_CHAT_PROFILE_ID
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError

from .desktop_connection_service import current_desktop_connection, bind_desktop_connection
from .operation_availability import model_platform_reason

_logger = logging.getLogger(__name__)
MAX_USER_TEXT_CHARS = 32000


class ChatConversationError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass
class AcceptedChatInput:
    conversation_id: str
    message_id: str
    status: str = "received"
    message: str = "已收到，等待当前操作结束后应用"


@dataclass
class ChatConversationRun:
    conversation_id: str
    client_message_id: str
    _service: Any

    async def events(self):
        """原样传递 Factory 的原生事件，提交与取消仍由既有 owner 负责。"""
        service = self._service
        # 用户输入和原工具调用记录决定来源；后台线程、流连接或审批端都不能指定设备。
        cancelled = False
        with bind_desktop_connection(""), bind_cancellation_token(service.run_registry.token(self.conversation_id)):
            try:
                async with service.chat_service.open_chat_run(
                    conversation_id=self.conversation_id, client_message_id=self.client_message_id,
                ) as session:
                    async for event in session.events([]):
                        yield event
                    if session.failure_error is not None:
                        raise session.failure_error
                    cancelled = session.cancelled
                    if not cancelled and service.call_store.pending(self.conversation_id) is None:
                        service.finish_claims(self.conversation_id, "completed")
            except RunCancelled:
                cancelled = True
                raise
            except Exception as exc:
                _logger.exception("主 Agent 运行失败：%s", self.conversation_id)
                service.call_store.fail_unconsumed(self.conversation_id)
                service.finish_claims(self.conversation_id, "failed", getattr(exc, "code", "AI_AGENT_RUN_FAILED"))
                if isinstance(exc, AiAgentExecutionError):
                    raise
                raise RuntimeError("模型运行未能完成，请在 ERP 中查看本轮结果。") from None
            finally:
                with service.run_registry.input_guard():
                    superseded = service.run_registry.is_superseded(self.conversation_id)
                    if cancelled or service.run_registry.token(self.conversation_id).cancelled:
                        service.call_store.cancel_pending_work(
                            self.conversation_id, preserve_queue=superseded,
                        )
                    service.run_registry.release(self.conversation_id)
                    if superseded:
                        service.run_registry.new_input(self.conversation_id)
                    if not service.call_store.release_replacement(self.conversation_id) and service.call_store.pending(self.conversation_id) is None:
                        service.call_store.release_queue(self.conversation_id)
                service.call_store.notify_changed(self.conversation_id)

    async def run(self):
        try:
            async for _ in self.events():
                pass
        except Exception:
            # 已由 events 保存失败并释放互斥；后台投递不重放用户操作。
            pass


class ChatConversationService:
    def __init__(self, *, chat_service, claim_store, run_registry, call_store,
                 empty_owner_reader=None, empty_owner_writer=None, deleted_reader=None):
        self.chat_service = chat_service
        self.claim_store = claim_store
        self.run_registry = run_registry
        self.call_store = call_store
        self.empty_owner_reader = empty_owner_reader
        self.empty_owner_writer = empty_owner_writer
        self.deleted_reader = deleted_reader

    def create_conversation(self, conversation_id: str | None = None) -> str:
        """预留有可信归属的空会话，不伪造用户消息、模型回复或运行记录。"""
        conversation_id = self._validate_conversation_id(conversation_id or "conversation_global_chat_" + uuid4().hex)
        with self.run_registry.input_guard():
            self._require_live_id(conversation_id)
            if self.chat_service.message_store.get(conversation_id) is not None:
                self._verify_history_ownership(conversation_id)
                return conversation_id
            if self.empty_owner_writer is None:
                raise RuntimeError("当前入口没有配置空会话归属存储。")
            self.empty_owner_writer(conversation_id, profile_id=GLOBAL_CHAT_PROFILE_ID,
                                    actor_id=GLOBAL_CHAT_ACTOR_ID, tenant_id=GLOBAL_CHAT_TENANT_ID)
            self.chat_service.message_store.save(conversation_id, [])
        return conversation_id

    def accept_input(self, conversation_id, message_id, messages, *, draft_ids=(), queued=False, answer_to=None):
        conversation_id = self._validate_conversation_id(conversation_id)
        self._verify_history_ownership(conversation_id)
        reason = model_platform_reason(self.chat_service.app_config, GLOBAL_CHAT_PROFILE_ID)
        if reason:
            raise ChatConversationError(409, "OPERATION_UNAVAILABLE", reason)
        if self.run_registry.token(conversation_id).cancelled and self.run_registry.is_active(conversation_id):
            raise ChatConversationError(409, "AI_CHAT_STOPPING", "当前操作正在停止，请稍后发送。")
        # 仅写入服务端收件箱；装配原生历史前移除连接标识。客户端 metadata 不能指定设备。
        for message in messages:
            message.metadata = {**(message.metadata or {}), "desktop_connection": current_desktop_connection()}
        self.call_store.accept_input(
            conversation_id, message_id, messages, draft_ids=draft_ids, queued=queued, answer_to=answer_to,
            profile_id=GLOBAL_CHAT_PROFILE_ID, actor_id=GLOBAL_CHAT_ACTOR_ID, tenant_id=GLOBAL_CHAT_TENANT_ID,
        )
        self.run_registry.new_input(conversation_id)
        title_scheduler = getattr(self.chat_service, "title_scheduler", None)
        if not queued and answer_to is None and title_scheduler:
            try:
                title_scheduler(self.chat_service, conversation_id, messages)
            except Exception:
                _logger.warning("对话已接收，暂时无法生成标题。", exc_info=True)

    def accept_text(self, conversation_id: str, message_id: str, text: str):
        if not text.strip() or len(text) > MAX_USER_TEXT_CHARS:
            raise ChatConversationError(400, "AI_CHAT_MESSAGE_INVALID", "请输入不超过 32000 字符的文字。")
        with self.run_registry.input_guard():
            self.accept_input(conversation_id, message_id, [ModelRequest(parts=[UserPromptPart(text)])])
            return self.prepare_accepted(conversation_id, message_id)

    def prepare_accepted(self, conversation_id, message_id):
        if self.call_store.pending(conversation_id) is not None and not self.call_store.ready(conversation_id):
            return AcceptedChatInput(conversation_id, message_id, message="已收到，等待其余工具结果或审批齐备后应用")
        if not self.run_registry.acquire(conversation_id):
            return AcceptedChatInput(conversation_id, message_id)
        return self.make_run(conversation_id, message_id)

    def make_run(self, conversation_id, message_id):
        return ChatConversationRun(conversation_id, message_id, self)

    def cancel_run(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        conversation_id = self._validate_conversation_id(conversation_id)
        with self.run_registry.input_guard():
            self._verify_history_ownership(conversation_id)
            # 重复取消旧消息不应中断后来用户明确启动的新操作。
            target = self.claim_store.get(conversation_id, message_id)
            latest = self.claim_store.find_for_conversation(conversation_id)
            if target and latest and latest.client_message_id != message_id:
                raise ChatConversationError(409, "AI_CHAT_STOP_TARGET_STALE", "对话已收到新消息，请刷新后停止当前操作。")
            if target and target.status == "cancelled":
                return {"ok": True, "conversation_id": conversation_id}
            self.run_registry.token(conversation_id).cancel()
            self.call_store.cancel_input(
                conversation_id, message_id, profile_id=GLOBAL_CHAT_PROFILE_ID,
                actor_id=GLOBAL_CHAT_ACTOR_ID, tenant_id=GLOBAL_CHAT_TENANT_ID,
            )
            self.call_store.cancel_pending_work(conversation_id)
            if not self.run_registry.is_active(conversation_id):
                history = self.chat_service.message_store.get(conversation_id)
                self.call_store.commit(
                    conversation_id, history.model_messages() if history else [],
                    expected_version=history.history_version if history else 0,
                )
            return {"ok": True, "conversation_id": conversation_id}

    def finish_claims(
        self, conversation_id: str, status: str, error_code: str = ""
    ) -> None:
        with self.call_store.db._connect() as conn:
            rows = conn.execute(
                "SELECT claim_id FROM ai_chat_turn_claims WHERE conversation_id=? AND status='claimed' AND client_message_id IN (SELECT message_id FROM ai_chat_inbox WHERE conversation_id=? AND status IN ('applied','failed'))",
                (conversation_id, conversation_id),
            ).fetchall()
        for row in rows:
            self.claim_store.finish_turn(row[0], status=status, error_code=error_code)

    def _validate_conversation_id(self, value: Any) -> str:
        conversation_id = str(value or "").strip()
        if reply_root(conversation_id) and self.claim_store.find_for_conversation(conversation_id):
            return conversation_id
        if not is_global_id(conversation_id):
            raise ChatConversationError(
                400,
                "AI_CHAT_CONVERSATION_ID_INVALID",
                "conversation ID 必须是 conversation_global_chat_ 加 32 位十六进制。",
            )
        return conversation_id

    def _require_live_id(self, conversation_id):
        if self.deleted_reader and self.deleted_reader(reply_root(conversation_id) or conversation_id):
            raise ChatConversationError(410, "CHAT_DELETED", "这段对话已删除，请切换对话。")

    def _verify_history_ownership(self, conversation_id: str) -> None:
        """已有历史必须归属 global.chat profile；否则拒绝继续运行。"""

        self._require_live_id(conversation_id)
        try:
            history = self.chat_service.message_store.get(conversation_id)
        except PydanticMessageStoreError as exc:
            raise ChatConversationError(500, exc.code, str(exc)) from None
        claim = self.claim_store.find_for_conversation(conversation_id)
        if history is None and claim is None:
            return
        if history is not None and claim is None:
            messages = history.model_messages()
            if not messages and self.empty_owner_reader is not None and self.empty_owner_reader(
                conversation_id, profile_id=GLOBAL_CHAT_PROFILE_ID,
                actor_id=GLOBAL_CHAT_ACTOR_ID, tenant_id=GLOBAL_CHAT_TENANT_ID,
            ):
                return
            branch = (messages[-1].metadata or {}).get("ai_chat_branch", {}) if messages else {}
            if (
                isinstance(branch, dict)
                and branch.get("profile_id") == GLOBAL_CHAT_PROFILE_ID
                and branch.get("actor_id") == GLOBAL_CHAT_ACTOR_ID
                and branch.get("tenant_id") == GLOBAL_CHAT_TENANT_ID
            ):
                return
        if (
            claim is None
            or claim.profile_id != GLOBAL_CHAT_PROFILE_ID
            or claim.actor_id != GLOBAL_CHAT_ACTOR_ID
            or claim.tenant_id != GLOBAL_CHAT_TENANT_ID
        ):
            raise ChatConversationError(
                409,
                "AI_CHAT_CONVERSATION_UNOWNED",
                "该历史不属于全局对话 profile，不能继续运行。",
            )


__all__ = ["AcceptedChatInput", "ChatConversationError", "ChatConversationRun", "ChatConversationService"]
