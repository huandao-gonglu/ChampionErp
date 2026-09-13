"""官方 Vercel AI 请求、事件编码和展示历史的薄适配层。"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    RegenerateMessage,
    SubmitMessage,
    TextUIPart,
    UIMessage,
)
from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
from erp_web.services.ai_run_cancellation import bind_cancellation_token
from erp_web.schemas.ai_page_context import AiPageContext
from erp_web.services.global_agent_chat_service import (
    GLOBAL_CHAT_ACTOR_ID,
    GLOBAL_CHAT_TENANT_ID,
    GLOBAL_CHAT_PROFILE_ID,
)
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnAlreadyAcceptedError
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError

_logger = logging.getLogger(__name__)
VERCEL_SDK_VERSION = 7
MAX_USER_TEXT_CHARS = 32000
MAX_DELIVERY_QUEUE_CHUNKS = 1024
_GLOBAL_CHAT_CONVERSATION_PATTERN = re.compile(
    r"^conversation_global_chat_[0-9a-f]{32}$"
)


class VercelUiProtocolError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def new_event_stream(conversation_id: str) -> VercelAIEventStream:
    return VercelAIEventStream(
        SubmitMessage(id=conversation_id, messages=[]), sdk_version=VERCEL_SDK_VERSION
    )


@dataclass
class AcceptedChatInput:
    conversation_id: str
    message_id: str
    status: str = "received"
    message: str = "已收到，等待当前操作结束后应用"


@dataclass
class VercelAiChatRun:
    conversation_id: str
    client_message_id: str
    _service: Any

    def sse_headers(self) -> dict[str, str]:
        stream = new_event_stream(self.conversation_id)
        return {
            "Content-Type": stream.content_type,
            "Cache-Control": "no-store",
            "Connection": "close",
            **(stream.response_headers or {}),
        }

    def abort_before_stream(self) -> None:
        self._service.run_registry.release(self.conversation_id)
        # 消息已持久接收，由后台投递器继续处理。

    async def stream(self, write_chunk) -> None:
        chunks: queue.Queue = queue.Queue(maxsize=MAX_DELIVERY_QUEUE_CHUNKS)
        overflow = False

        def deliver(chunk):
            nonlocal overflow
            if overflow:
                return
            try:
                chunks.put_nowait(chunk)
            except queue.Full:
                overflow = True

        future = get_detached_chat_runner().submit(self._produce_and_finalize(deliver))
        connected = True
        while not future.done() or not chunks.empty():
            try:
                chunk = await asyncio.to_thread(chunks.get, True, 0.1)
            except queue.Empty:
                continue
            if connected:
                try:
                    write_chunk(chunk.encode("utf-8"))
                except OSError:
                    connected = False
        if overflow and connected:
            # 不丢弃中间 delta 后继续伪装完整流；明确使客户端进入错误并重读历史。
            stream = new_event_stream(self.conversation_id)
            for events in (
                stream.on_error(
                    RuntimeError("客户端消费过慢，请重新读取已提交的对话历史。")
                ),
                stream.after_stream(),
            ):
                async for chunk in stream.encode_stream(events):
                    write_chunk(chunk.encode("utf-8"))

    async def _produce_and_finalize(self, deliver) -> None:
        with bind_cancellation_token(self._service.run_registry.token(self.conversation_id)):
            await self._run_and_finalize(deliver)

    async def _run_and_finalize(self, deliver) -> None:
        service = self._service
        started = False
        try:
            async with service.chat_service.open_chat_run(
                conversation_id=self.conversation_id,
                client_message_id=self.client_message_id,
            ) as session:
                stream = new_event_stream(self.conversation_id)
                started = True
                async for chunk in stream.encode_stream(
                    stream.transform_stream(session.events([]))
                ):
                    deliver(chunk)
                if session.failure_error is not None:
                    raise session.failure_error
                if session.cancelled:
                    service.call_store.cancel_pending_work(self.conversation_id)
                elif service.call_store.pending(self.conversation_id) is None:
                    service.finish_claims(self.conversation_id, "completed")
        except Exception as exc:
            _logger.exception("主 Agent 运行失败：%s", self.conversation_id)
            service.call_store.fail_unconsumed(self.conversation_id)
            if not started:
                service.call_store.fail_unconsumed(self.conversation_id)

                async def failed():
                    raise RuntimeError(
                        "模型运行未能启动，请检查模型配置后重新发送消息。"
                    )
                    yield

                stream = new_event_stream(self.conversation_id)
                async for chunk in stream.encode_stream(
                    stream.transform_stream(failed())
                ):
                    deliver(chunk)
            service.finish_claims(
                self.conversation_id,
                "failed",
                getattr(exc, "code", "AI_AGENT_RUN_FAILED"),
            )
        finally:
            with service.run_registry.input_guard():
                if service.run_registry.token(self.conversation_id).cancelled:
                    service.call_store.cancel_pending_work(self.conversation_id)
                service.run_registry.release(self.conversation_id)


class VercelAiUiService:
    def __init__(
        self,
        *,
        chat_service,
        claim_store,
        run_registry,
        call_store,
        approval_session=None,
    ):
        self.chat_service = chat_service
        self.claim_store = claim_store
        self.run_registry = run_registry
        self.call_store = call_store
        self.approval_session = approval_session

    def prepare_run(self, body: bytes, *, approval_token: str = ""):
        with self.run_registry.input_guard():
            return self._prepare_run(body, approval_token=approval_token)

    def cancel_run(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        conversation_id = self._validate_conversation_id(conversation_id)
        with self.run_registry.input_guard():
            self._verify_history_ownership(conversation_id)
            # 重复取消旧消息不应中断后来用户明确启动的新操作。
            target = self.claim_store.get(conversation_id, message_id)
            latest = self.claim_store.find_for_conversation(conversation_id)
            if target and latest and latest.client_message_id != message_id:
                raise VercelUiProtocolError(409, "AI_CHAT_STOP_TARGET_STALE", "对话已收到新消息，请刷新后停止当前操作。")
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

    def _prepare_run(self, body: bytes, *, approval_token: str = ""):
        run_input = self._parse_run_input(body)
        conversation_id = self._validate_conversation_id(run_input.id)
        self._verify_history_ownership(conversation_id)
        raw = json.loads(body)
        if self.run_registry.token(conversation_id).cancelled and self.run_registry.is_active(conversation_id):
            raise VercelUiProtocolError(409, "AI_CHAT_STOPPING", "当前操作正在停止，请稍后发送。")
        draft_ids = raw.get("target_draft_ids", [])
        if (
            not isinstance(draft_ids, list)
            or len(draft_ids) > 100
            or any(not isinstance(x, str) or not x.strip() for x in draft_ids)
        ):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_TARGET_INVALID",
                "目标草稿必须是最多 100 个稳定 draft_id。",
            )
        if len(run_input.messages) == 1 and run_input.messages[0].role == "user":
            message = self._validate_single_user_message(run_input.messages)
            try:
                page_context = (
                    AiPageContext.model_validate(raw["page_context"]).model_dump(exclude_none=True)
                    if "page_context" in raw else None
                )
            except ValidationError:
                raise VercelUiProtocolError(
                    422, "AI_CHAT_PAGE_CONTEXT_INVALID", "页面背景只能包含有效的页面位置和资源 ID。"
                ) from None
            messages = self._load_new_messages(message)
            for item in messages:
                item.metadata = {"page_context": page_context}
            try:
                self.call_store.accept_input(
                    conversation_id,
                    message.id,
                    messages,
                    draft_ids=draft_ids,
                    profile_id=GLOBAL_CHAT_PROFILE_ID,
                    actor_id=GLOBAL_CHAT_ACTOR_ID,
                    tenant_id=GLOBAL_CHAT_TENANT_ID,
                )
            except AiChatTurnAlreadyAcceptedError:
                raise VercelUiProtocolError(
                    409,
                    "AI_CHAT_TURN_ALREADY_ACCEPTED",
                    "本轮消息已接收，请读取消息历史。",
                ) from None
            self.run_registry.new_input(conversation_id)
            message_id = message.id
        else:
            message_id = self._accept_approvals(run_input, approval_token)
        if self.call_store.pending(
            conversation_id
        ) is not None and not self.call_store.ready(conversation_id):
            return AcceptedChatInput(
                conversation_id,
                message_id,
                message="已收到，等待其余工具结果或审批齐备后应用",
            )
        if not self.run_registry.acquire(conversation_id):
            return AcceptedChatInput(conversation_id, message_id)
        return VercelAiChatRun(conversation_id, message_id, self)

    def _accept_approvals(self, run_input, token: str) -> str:
        if not run_input.messages or any(
            message.role != "assistant"
            or not message.parts
            or any(
                getattr(part, "state", None) != "approval-responded"
                for part in message.parts
            )
            for message in run_input.messages
        ):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "只能提交一条新用户文本或原生工具审批响应。",
            )
        from erp_web.services.approval_session import ApprovalSessionError

        try:
            if self.approval_session is None:
                raise VercelUiProtocolError(
                    403, "AI_APPROVAL_UNAUTHORIZED", "当前请求不能审批。"
                )
            actor = self.approval_session.require_approver(token)
        except ApprovalSessionError as exc:
            raise VercelUiProtocolError(403, exc.code, str(exc)) from None
        pending = self.call_store.pending(run_input.id)
        if pending is None:
            raise VercelUiProtocolError(
                409, "AI_APPROVAL_NOT_PENDING", "没有待审批调用。"
            )
        requests, saved_results, _ = pending
        allowed = {c.tool_call_id: c for c in requests.approvals}
        adapter = VercelAIAdapter(None, run_input, sdk_version=VERCEL_SDK_VERSION)
        results = adapter.deferred_tool_results
        if results is None or not results.approvals:
            raise VercelUiProtocolError(
                400, "AI_CHAT_MESSAGE_INVALID", "只能提交新用户文本或原生工具审批响应。"
            )
        for message in run_input.messages:
            if message.role != "assistant":
                raise VercelUiProtocolError(
                    400, "AI_APPROVAL_INVALID", "审批响应不能携带客户端历史。"
                )
            for part in message.parts:
                call_id = getattr(part, "tool_call_id", None)
                call = allowed.get(call_id)
                if (
                    call is None
                    or getattr(part, "state", None) != "approval-responded"
                    or getattr(part, "input", None) != call.args_as_dict()
                ):
                    raise VercelUiProtocolError(
                        403,
                        "AI_APPROVAL_INVALID",
                        "审批调用或参数与服务端已保存请求不一致。",
                    )
                if getattr(part, "type", "") != "tool-" + call.tool_name:
                    raise VercelUiProtocolError(
                        403, "AI_APPROVAL_INVALID", "审批工具名称不一致。"
                    )
                if part.approval.id != call_id:
                    raise VercelUiProtocolError(
                        403, "AI_APPROVAL_INVALID", "审批 ID 与服务端调用不一致。"
                    )
                results.metadata[call_id] = {
                    **requests.metadata.get(call_id, {}),
                    "approver": actor,
                    "approval_confirmed_at": saved_results.metadata.get(
                        call_id, {}
                    ).get("approval_confirmed_at")
                    or datetime.now(timezone.utc).isoformat(),
                }
        try:
            self.call_store.record_results(run_input.id, results)
        except ValueError as exc:
            raise VercelUiProtocolError(409, "AI_APPROVAL_CONFLICT", str(exc)) from None
        return "approval"

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

    def dump_ui_messages(self, conversation_id: str) -> dict:
        history = self.chat_service.message_store.get(conversation_id)
        claim = self.claim_store.find_for_conversation(conversation_id)
        if history is None and claim is None:
            raise VercelUiProtocolError(
                404, "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND", "Pydantic 对话不存在。"
            )
        ui = VercelAIAdapter.dump_messages(
            history.model_messages() if history else [], sdk_version=VERCEL_SDK_VERSION
        )
        visible = []
        for message in ui:
            metadata = message.metadata or {}
            if message.role == "user" and "presentation_user_message" in metadata:
                text = metadata["presentation_user_message"]
                if not text:
                    continue
                message = message.model_copy(update={"parts": [TextUIPart(text=text)]})
            visible.append(message)
        ui = visible
        pending = self.call_store.pending(conversation_id)
        # metadata 是领域展示摘要；工具 state 与审批 ID 仍由官方 Adapter 提供。
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "history_version": history.history_version if history else 0,
            "run_active": self.run_registry.is_active(conversation_id),
            "run_status": claim.status if claim else None,
            "latest_message_id": claim.client_message_id if claim else None,
            "created_at": history.created_at if history else claim.claimed_at,
            "updated_at": history.updated_at if history else claim.claimed_at,
            "run_error": {
                "code": claim.error_code,
                "message": "本轮运行失败，请检查错误或模型配置后重新发送。",
            }
            if claim and claim.status == "failed"
            else None,
            "messages": [
                m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in ui
            ],
            "pending_tool_calls": [
                {
                    "tool_call_id": c.tool_call_id,
                    "tool_name": c.tool_name,
                    "kind": kind,
                    "summary": pending[0]
                    .metadata.get(c.tool_call_id, {})
                    .get("summary", ""),
                }
                for kind, calls in (
                    ("approval", pending[0].approvals),
                    ("external", pending[0].calls),
                )
                for c in calls
                if c.tool_call_id
                not in (
                    pending[1].approvals if kind == "approval" else pending[1].calls
                )
            ]
            if pending
            else [],
            "received_messages": [
                {"message_id": r["message_id"], "status": "received"}
                for r in self.call_store.inbox(conversation_id)
            ],
        }

    # -- 预流校验 ----------------------------------------------------------

    def _parse_run_input(self, body: bytes) -> SubmitMessage:
        try:
            run_input = VercelAIAdapter.build_run_input(body)
        except ValidationError:
            raise VercelUiProtocolError(
                422,
                "AI_CHAT_REQUEST_SCHEMA_INVALID",
                "请求体不符合 Vercel AI 消息协议。",
            ) from None
        if isinstance(run_input, RegenerateMessage):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_TRIGGER_UNSUPPORTED",
                "只接受 submit-message；不支持 regenerate。",
            )
        return run_input

    def _validate_conversation_id(self, value: Any) -> str:
        conversation_id = str(value or "").strip()
        if not _GLOBAL_CHAT_CONVERSATION_PATTERN.fullmatch(conversation_id):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_CONVERSATION_ID_INVALID",
                "conversation ID 必须是 conversation_global_chat_ 加 32 位十六进制。",
            )
        return conversation_id

    def _validate_single_user_message(
        self,
        messages: Sequence[UIMessage],
    ) -> UIMessage:
        if len(messages) != 1:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "本轮只能提交恰好一条新用户消息。",
            )
        message = messages[0]
        if message.role != "user":
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "本轮只能提交 user 消息。",
            )
        if not str(message.id or "").strip():
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息必须携带客户端生成的 id。",
            )
        if not message.parts:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息不能为空。",
            )
        total_chars = 0
        for part in message.parts:
            if not isinstance(part, TextUIPart):
                raise VercelUiProtocolError(
                    400,
                    "AI_CHAT_PART_UNSUPPORTED",
                    "本期入口只接受非空 text part。",
                )
            if not str(part.text or "").strip():
                raise VercelUiProtocolError(
                    400,
                    "AI_CHAT_MESSAGE_INVALID",
                    "文本消息不能为空。",
                )
            total_chars += len(str(part.text or ""))
        if total_chars > MAX_USER_TEXT_CHARS:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_TOO_LONG",
                f"用户消息不能超过 {MAX_USER_TEXT_CHARS} 字符。",
            )
        return message

    def _verify_history_ownership(self, conversation_id: str) -> None:
        """已有历史必须归属 global.chat profile；否则拒绝继续运行。"""

        try:
            history = self.chat_service.message_store.get(conversation_id)
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        claim = self.claim_store.find_for_conversation(conversation_id)
        if history is None and claim is None:
            return
        if (
            claim is None
            or claim.profile_id != GLOBAL_CHAT_PROFILE_ID
            or claim.actor_id != GLOBAL_CHAT_ACTOR_ID
            or claim.tenant_id != GLOBAL_CHAT_TENANT_ID
        ):
            raise VercelUiProtocolError(
                409,
                "AI_CHAT_CONVERSATION_UNOWNED",
                "该历史不属于全局对话 profile，不能继续运行。",
            )

    def _load_new_messages(self, ui_message: UIMessage) -> list[ModelMessage]:
        """官方 load_messages 转换后由服务端重新标记，忽略客户端 metadata。"""

        try:
            loaded = VercelAIAdapter.load_messages([ui_message])
        except Exception:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息无法转换为 Pydantic 消息。",
            ) from None
        for message in loaded:
            message.metadata = None
            message.timestamp = None
        return loaded
