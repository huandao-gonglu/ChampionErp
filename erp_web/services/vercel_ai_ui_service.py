"""官方 Vercel AI 请求、事件编码和展示历史的薄适配层。"""

from __future__ import annotations

import asyncio
import json
import queue
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from typing import Any, Sequence
from uuid import uuid4

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, RetryPromptPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    FileUIPart,
    RegenerateMessage,
    SubmitMessage,
    TextUIPart,
    UIMessage,
)
from erp_web.services.chat_images import (
    IMAGE_TYPES, MAX_CHAT_IMAGES, MAX_CHAT_TOTAL_BYTES, chat_image_reason, identify_uploads, prompt_summary, validate_image_attachment,
)
from erp_web.schemas.chat_conversations import is_global_id
from erp_web.services.chat_conversation_service import (
    AcceptedChatInput, ChatConversationError as VercelUiProtocolError,
    ChatConversationRun, ChatConversationService, MAX_USER_TEXT_CHARS,
)
from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
from erp_web.services.agent_run_storage import stored_run_error
from erp_web.services.manual_ai_action import manual_action_with_progress, read_manual_ai_action
from erp_web.schemas.ai_page_context import AiPageContext
from erp_web.schemas.ai_work import AiTurnTiming
from erp_web.services.global_agent_chat_service import (
    GLOBAL_CHAT_ACTOR_ID,
    GLOBAL_CHAT_TENANT_ID,
    GLOBAL_CHAT_PROFILE_ID,
)
from erp_web.stores.agent_call_store import UserInputNotPending
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnAlreadyAcceptedError

VERCEL_SDK_VERSION = 7
MAX_DELIVERY_QUEUE_CHUNKS = 1024


def new_event_stream(conversation_id: str) -> VercelAIEventStream:
    return VercelAIEventStream(
        SubmitMessage(id=conversation_id, messages=[]), sdk_version=VERCEL_SDK_VERSION
    )


def _display_history(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """原生输出校验的重试提示只发给模型；网页和分支共用相同的消息映射。"""
    visible = []
    for message in messages:
        if "image_batch_item" in (message.metadata or {}):
            continue
        if isinstance(message, ModelRequest):
            parts = [part for part in message.parts if not (isinstance(part, RetryPromptPart) and part.tool_name is None)]
            if len(parts) != len(message.parts):
                previous = visible[-1] if visible else None
                if (isinstance(previous, ModelResponse) and previous.run_id == message.run_id
                        and not (previous.metadata or {}).get("chat_final")
                        and all(isinstance(part, (TextPart, ThinkingPart)) for part in previous.parts)):
                    visible.pop()
                if not parts:
                    continue
                message = replace(message, parts=parts)
        visible.append(message)
    return visible


def _pending_tool_calls(pending, history):
    if not pending:
        return []
    requests, results, _ = pending
    remaining = requests.remaining(results)
    if not remaining:
        return []

    # 同一模型回复可以同时产生审批、提问和后台操作，保留其原始调用顺序。
    positions = {
        part.tool_call_id: (message.timestamp.isoformat(), index)
        for message in history if isinstance(message, ModelResponse)
        for index, part in enumerate(message.parts) if isinstance(part, ToolCallPart)
    }
    rows = []
    for kind, calls in (("approval", remaining.approvals), ("external", remaining.calls)):
        for call in calls:
            call_id = call.tool_call_id
            row = {
                "tool_call_id": call_id, "tool_name": call.tool_name, "kind": kind,
                "requested_at": positions.get(call_id, ("", 0))[0],
                "summary": remaining.metadata.get(call_id, {}).get("summary", ""),
            }
            if call.tool_name == "request_user_input":
                args = call.args_as_dict()
                row.update(kind="input", title=args["title"], question=args["question"], options=args.get("options") or [])
            rows.append(row)
    return sorted(rows, key=lambda row: positions.get(row["tool_call_id"], ("", 0)))


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
        await self._run_and_finalize(deliver)

    async def _run_and_finalize(self, deliver) -> None:
        stream = new_event_stream(self.conversation_id)
        run = ChatConversationRun(self.conversation_id, self.client_message_id, self._service)
        async for chunk in stream.encode_stream(stream.transform_stream(run.events())):
            deliver(chunk)


class VercelAiUiService(ChatConversationService):
    def __init__(
        self,
        *,
        chat_service,
        claim_store,
        run_registry,
        call_store,
        approval_session=None,
        empty_owner_reader=None,
        deleted_reader=None,
        job_progress_reader=None,
    ):
        super().__init__(chat_service=chat_service, claim_store=claim_store,
                         run_registry=run_registry, call_store=call_store, empty_owner_reader=empty_owner_reader, deleted_reader=deleted_reader)
        self.approval_session = approval_session
        self.job_progress_reader = job_progress_reader

    def prepare_run(self, body: bytes, *, approval_token: str = ""):
        run_input = self._parse_run_input(body)
        conversation_id = self._validate_conversation_id(run_input.id)
        raw = json.loads(body)
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
                item.metadata = {"page_context": page_context, "web_input": True}
            from .chat_queue_service import related_context
            answer_to = raw.get("answer_to")
            related = related_context(self, conversation_id) if answer_to is None else []
            if related:
                for item in messages:
                    item.metadata["immediate_context"] = related
            with self.run_registry.input_guard():
                try:
                    queued = raw.get("queue_if_busy") is True and (
                        self.run_registry.is_active(conversation_id) or self.call_store.pending(conversation_id) is not None
                    )
                    self.accept_input(conversation_id, message.id, messages, draft_ids=draft_ids if answer_to is None else (),
                                      queued=queued, answer_to=answer_to)
                except UserInputNotPending as exc:
                    raise VercelUiProtocolError(409, "AI_CHAT_QUESTION_CLOSED", str(exc)) from None
                except AiChatTurnAlreadyAcceptedError:
                    raise VercelUiProtocolError(
                        409, "AI_CHAT_TURN_ALREADY_ACCEPTED", "本轮消息已接收，请读取消息历史。",
                    ) from None
                return self.prepare_accepted(conversation_id, message.id)
        with self.run_registry.input_guard():
            self._verify_history_ownership(conversation_id)
            if self.run_registry.token(conversation_id).cancelled and self.run_registry.is_active(conversation_id):
                raise VercelUiProtocolError(409, "AI_CHAT_STOPPING", "当前操作正在停止，请稍后发送。")
            message_id = self._accept_approvals(run_input, approval_token)
            return self.prepare_accepted(conversation_id, message_id)

    def make_run(self, conversation_id, message_id):
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
                "只能提交一条新用户消息或原生工具审批响应。",
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

    def dump_ui_messages(self, conversation_id: str) -> dict:
        result = self._dump_turn(conversation_id)
        if is_global_id(conversation_id):
            result["parallel_replies"] = [self._dump_turn(run_id) for run_id in self.call_store.parallel_runs(conversation_id)]
        return result

    def _dump_turn(self, conversation_id: str) -> dict:
        history = self.chat_service.message_store.get(conversation_id)
        claim = self.claim_store.find_for_conversation(conversation_id)
        if history is None and claim is None:
            self._require_live_id(conversation_id)
            raise VercelUiProtocolError(
                404, "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND", "Pydantic 对话不存在。"
            )
        native_history = history.model_messages() if history else []
        run_active = self.run_registry.is_active(conversation_id)
        ui = VercelAIAdapter.dump_messages(
            _display_history(native_history),
            sdk_version=VERCEL_SDK_VERSION
        )
        script_receipts = self.call_store.script_write_receipts(conversation_id)
        visible = []
        turn = None
        for message in ui:
            metadata = dict(message.metadata or {})
            if "immediate_context" in metadata:
                metadata.pop("immediate_context")
                message = message.model_copy(update={"metadata": metadata})
            if message.role == "user":
                turn = self._turn_display(conversation_id, metadata)
                action = read_manual_ai_action(self.call_store, metadata, active=run_active)
                if action is not None:
                    metadata["manual_ai_action"] = manual_action_with_progress(action, include_image_status=False)
                    message = message.model_copy(update={"metadata": metadata})
            if message.role in ("user", "assistant") and turn:
                metadata = {**metadata, "ai_turn": turn}
                message = message.model_copy(update={"metadata": metadata})
            if message.role == "user" and "presentation_user_message" in metadata:
                text = metadata["presentation_user_message"]
                if not text:
                    continue
                message = message.model_copy(update={"parts": [TextUIPart(text=text)]})
            receipts = [
                receipt for part in message.parts
                if part.type == "tool-run_code"
                for receipt in script_receipts.get(part.tool_call_id, [])
            ]
            if receipts:
                message = message.model_copy(update={"metadata": {
                    **metadata, "business_write_receipts": receipts,
                }})
            visible.append(message)
        ui = visible
        pending = self.call_store.pending(conversation_id)
        pending_tools = _pending_tool_calls(pending, native_history)
        if self.job_progress_reader:
            for tool in pending_tools:
                if tool["kind"] == "external":
                    progress = self.job_progress_reader(conversation_id, tool["tool_call_id"])
                    if progress:
                        tool["progress"] = progress
        # metadata 是领域展示摘要；工具 state 与审批 ID 仍由官方 Adapter 提供。
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "history_version": history.history_version if history else 0,
            "run_active": run_active,
            "run_status": claim.status if claim else None,
            "latest_message_id": claim.client_message_id if claim else None,
            "created_at": history.created_at if history else claim.claimed_at,
            "updated_at": history.updated_at if history else claim.claimed_at,
            "run_error": stored_run_error(
                native_history,
                claim.client_message_id, claim.error_code,
            )
            if claim and claim.status == "failed" and not (
                claim.client_message_id == conversation_id and native_history
                and (native_history[0].metadata or {}).get("manual_ai_action")
            )
            else None,
            "messages": [
                m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in ui
            ],
            "pending_tool_calls": pending_tools,
            "received_messages": [
                {"message_id": r["message_id"],
                 "status": "queued" if pending and not run_active and r["status"] == "received" else r["status"],
                 "text": prompt_summary(
                    ModelMessagesTypeAdapter.validate_json(r["messages_json"])
                )}
                for r in self.call_store.inbox(conversation_id, include_queued=True)
            ],
        }

    def _turn_display(self, conversation_id: str, metadata: dict) -> AiTurnTiming | None:
        message_id = metadata.get("user_message_id")
        claim = self.claim_store.get(conversation_id, message_id) if message_id else None
        if claim:
            return {"started_at": claim.claimed_at, "finished_at": claim.finished_at,
                    "status": claim.status}
        # 分支保留原轮次时间；旧记录缺少时间时不使用页面打开时间补造。
        return metadata.get("ai_turn")

    def fork_conversation(self, conversation_id: str, message_id: str) -> dict:
        """复制已闭合的原生历史前缀；不复制运行、审批、任务或业务写权限。"""
        conversation_id = self._validate_conversation_id(conversation_id)
        self._verify_history_ownership(conversation_id)
        history = self.chat_service.message_store.get(conversation_id)
        if history is None:
            raise VercelUiProtocolError(404, "AI_CHAT_BRANCH_SOURCE_MISSING", "原对话不存在。")
        messages = history.model_messages()
        display = _display_history(messages)
        ui = VercelAIAdapter.dump_messages(display, sdk_version=VERCEL_SDK_VERSION)
        matches = [i for i, item in enumerate(ui) if item.id == message_id and item.role == "assistant"]
        if len(matches) != 1:
            raise VercelUiProtocolError(409, "AI_CHAT_BRANCH_MESSAGE_INVALID", "回复尚未保存或已变化，请刷新后重试。")
        target = matches[0]
        if target + 1 < len(ui) and ui[target + 1].role == "assistant":
            raise VercelUiProtocolError(409, "AI_CHAT_BRANCH_INCOMPLETE", "请从一轮已完成的回复创建分支。")
        user = next((item for item in reversed(ui[:target]) if item.role == "user"), None)
        turn = self._turn_display(conversation_id, user.metadata or {}) if user else None
        if not turn or turn.get("status") != "completed":
            raise VercelUiProtocolError(409, "AI_CHAT_BRANCH_INCOMPLETE", "请等待本轮完成后创建分支。")
        # 官方 ID 回调提供 UI 消息与原生消息的对应关系，不反向重建工具历史。
        positions = {id(message): index for index, message in enumerate(messages)}
        indexed = VercelAIAdapter.dump_messages(
            display, sdk_version=VERCEL_SDK_VERSION,
            generate_message_id=lambda message, role, index: str(positions[id(message)]) if role == "assistant" else str(index),
        )
        prefix = messages[:int(indexed[target].id) + 1]
        outstanding: set[str] = set()
        for message in prefix:
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    outstanding.add(part.tool_call_id)
                elif isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    outstanding.discard(part.tool_call_id)
        if outstanding or not isinstance(prefix[-1], ModelResponse):
            raise VercelUiProtocolError(409, "AI_CHAT_BRANCH_INCOMPLETE", "该回复仍有未结束的操作，不能创建分支。")
        for message in prefix:
            metadata = message.metadata or {}
            timing = self._turn_display(conversation_id, metadata)
            if metadata.get("user_message_id") and timing:
                message.metadata = {**metadata, "ai_turn": timing}
        branch_id = f"conversation_global_chat_{uuid4().hex}"
        prefix[-1].metadata = {**(prefix[-1].metadata or {}), "ai_chat_branch": {
            "source_conversation_id": conversation_id, "source_message_id": message_id,
            "profile_id": GLOBAL_CHAT_PROFILE_ID, "actor_id": GLOBAL_CHAT_ACTOR_ID,
            "tenant_id": GLOBAL_CHAT_TENANT_ID,
        }}
        self.chat_service.message_store.save(branch_id, prefix)
        return self.dump_ui_messages(branch_id)

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
        images = [part for part in message.parts if isinstance(part, FileUIPart)]
        if any(part.media_type not in IMAGE_TYPES for part in images):
            raise VercelUiProtocolError(400, "AI_CHAT_PART_UNSUPPORTED", "支持 JPEG、PNG 和 WebP 图片。")
        if images:
            reason = chat_image_reason(self.chat_service.app_config)
            if reason:
                raise VercelUiProtocolError(409, "AI_CHAT_IMAGE_UNSUPPORTED", reason)
            try:
                if len(images) > MAX_CHAT_IMAGES:
                    raise ValueError("每条消息最多可附 4 张图片。")
                if sum(validate_image_attachment(part) for part in images) > MAX_CHAT_TOTAL_BYTES:
                    raise ValueError("每条消息的图片合计不能超过 20 MiB。")
            except ValueError as exc:
                raise VercelUiProtocolError(400, "AI_CHAT_IMAGE_INVALID", str(exc)) from None
        total_chars = 0
        for part in message.parts:
            if isinstance(part, FileUIPart):
                continue
            if not isinstance(part, TextUIPart):
                raise VercelUiProtocolError(
                    400,
                    "AI_CHAT_PART_UNSUPPORTED",
                    "用户消息仅支持文字和图片。",
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
        identify_uploads(loaded)
        for message in loaded:
            message.metadata = None
            message.timestamp = None
        return loaded


__all__ = ["AcceptedChatInput", "VercelUiProtocolError", "VercelAiChatRun", "VercelAiUiService", "VERCEL_SDK_VERSION"]
