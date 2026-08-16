"""唯一 Vercel AI 协议入口：解析请求、运行官方 SSE、派生展示历史。

不选择业务工具、不改写消息语义：Agent 装配与运行通过
``GlobalAgentChatService`` → ``AiAgentFactory``，全部 UIMessage 与事件转换
使用官方 ``VercelAIAdapter`` / ``VercelAIEventStream``。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    RegenerateMessage,
    SubmitMessage,
    TextUIPart,
    UIMessage,
)

from erp_web.schemas.ai_work import AiWorkUiMessagesDetail
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentStreamSession,
)
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.services.global_agent_chat_service import (
    GLOBAL_CHAT_ACTOR_ID,
    GLOBAL_CHAT_PROFILE_ID,
    GLOBAL_CHAT_TENANT_ID,
    GlobalAgentChatService,
)
from erp_web.stores.ai_chat_turn_claim_store import (
    CANCELLED,
    COMPLETED,
    FAILED,
    AiChatTurnAlreadyAcceptedError,
    AiChatTurnClaim,
    AiChatTurnClaimError,
    AiChatTurnClaimStore,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError


_logger = logging.getLogger(__name__)

VERCEL_SDK_VERSION = 7
MAX_USER_TEXT_CHARS = 32_000

_GLOBAL_CHAT_CONVERSATION_PATTERN = re.compile(
    r"^conversation_global_chat_[0-9a-f]{32}$"
)


class VercelUiProtocolError(Exception):
    """流开始前的协议错误；按 status_code 映射项目标准 JSON 响应。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = int(status_code)
        self.code = str(code)
        super().__init__(message)


@dataclass
class VercelAiChatRun:
    """一次已领取的聊天 run；预流检查完成后只输出官方 SSE。"""

    conversation_id: str
    client_message_id: str
    claim: AiChatTurnClaim
    run_input: SubmitMessage
    new_messages: list[ModelMessage]
    _service: "VercelAiUiService" = field(repr=False)
    _event_stream: VercelAIEventStream[Any, Any] = field(repr=False)

    def sse_headers(self) -> dict[str, str]:
        """官方 SSE 响应头；不设置 Content-Length。"""

        headers = {
            "Content-Type": self._event_stream.content_type,
            "Cache-Control": "no-store",
            "Connection": "close",
        }
        for key, value in (self._event_stream.response_headers or {}).items():
            headers[key] = value
        return headers

    async def stream(self, write_chunk: Callable[[bytes], None]) -> None:
        """消费官方事件流并写 SSE；终态与锁在 finally 统一收尾。"""

        session: AiAgentStreamSession[str] | None = None
        client_disconnected = False
        iterators: list[AsyncIterator[Any]] = []
        try:
            try:
                async with self._service.chat_service.open_chat_run(
                    conversation_id=self.conversation_id,
                    new_messages=self.new_messages,
                ) as chat_session:
                    session = chat_session
                    native = chat_session.events(self.new_messages)
                    transformed = self._event_stream.transform_stream(native)
                    encoded = self._event_stream.encode_stream(transformed)
                    iterators.extend((encoded, transformed, native))
                    try:
                        async for chunk in encoded:
                            write_chunk(chunk.encode("utf-8"))
                    except OSError:
                        client_disconnected = True
            except AiAgentExecutionError as error:
                await self._write_error_stream(write_chunk, error)
            except OSError:
                client_disconnected = True
        finally:
            for iterator in reversed(iterators):
                try:
                    await iterator.aclose()
                except Exception:
                    pass
            if session is not None and session.completed:
                terminal_status = COMPLETED
            elif client_disconnected:
                terminal_status = CANCELLED
            else:
                terminal_status = FAILED
            try:
                self._service.claim_store.finish_turn(
                    self.claim.claim_id,
                    status=terminal_status,
                )
            except AiChatTurnClaimError:
                _logger.warning(
                    "AI chat turn claim 终态更新失败：claim_id=%s",
                    self.claim.claim_id,
                )
            except Exception:
                _logger.warning(
                    "AI chat turn claim 终态更新失败：claim_id=%s",
                    self.claim.claim_id,
                )
            self._service.run_registry.release(self.conversation_id)

    async def _write_error_stream(
        self,
        write_chunk: Callable[[bytes], None],
        error: AiAgentExecutionError,
    ) -> None:
        """流开始前失败时，也只发送官方 error/finish chunk。"""

        async def failing_events() -> AsyncIterator[Any]:
            raise error
            yield  # pragma: no cover - 让函数成为 async generator

        transformed = self._event_stream.transform_stream(failing_events())
        encoded = self._event_stream.encode_stream(transformed)
        try:
            async for chunk in encoded:
                write_chunk(chunk.encode("utf-8"))
        except OSError:
            pass


class VercelAiUiService:
    """实时 SSE 与历史 UIMessage 派生的协议边界。"""

    def __init__(
        self,
        *,
        chat_service: GlobalAgentChatService,
        claim_store: AiChatTurnClaimStore,
        run_registry: AiChatRunRegistry,
    ) -> None:
        self.chat_service = chat_service
        self.claim_store = claim_store
        self.run_registry = run_registry

    def prepare_run(
        self,
        body: bytes,
    ) -> VercelAiChatRun:
        """预流校验 + 领取锁与 claim；失败时仍返回标准 JSON 错误。"""

        run_input = self._parse_run_input(body)
        conversation_id = self._validate_conversation_id(run_input.id)
        ui_message = self._validate_single_user_message(run_input.messages)
        if not self.run_registry.acquire(conversation_id):
            raise VercelUiProtocolError(
                409,
                "AI_CHAT_RUN_ACTIVE",
                "该 conversation 已有活动 run，请稍后再试。",
            )
        claim: AiChatTurnClaim | None = None
        try:
            self._verify_history_ownership(conversation_id)
            try:
                claim = self.claim_store.claim_turn(
                    conversation_id=conversation_id,
                    client_message_id=str(ui_message.id or "").strip(),
                    profile_id=GLOBAL_CHAT_PROFILE_ID,
                    actor_id=GLOBAL_CHAT_ACTOR_ID,
                    tenant_id=GLOBAL_CHAT_TENANT_ID,
                )
            except AiChatTurnAlreadyAcceptedError:
                raise VercelUiProtocolError(
                    409,
                    "AI_CHAT_TURN_ALREADY_ACCEPTED",
                    "本轮消息已被服务端接受，请重新读取消息历史。",
                ) from None
            new_messages = self._load_new_messages(ui_message)
            event_stream: VercelAIEventStream[Any, Any] = VercelAIEventStream(
                run_input,
                sdk_version=VERCEL_SDK_VERSION,
            )
            assert claim is not None
            return VercelAiChatRun(
                conversation_id=conversation_id,
                client_message_id=str(ui_message.id or "").strip(),
                claim=claim,
                run_input=run_input,
                new_messages=new_messages,
                _service=self,
                _event_stream=event_stream,
            )
        except Exception:
            if claim is not None:
                try:
                    self.claim_store.finish_turn(
                        claim.claim_id,
                        status=FAILED,
                    )
                except Exception:
                    _logger.warning(
                        "AI chat 预流失败后的 claim 收尾失败：claim_id=%s",
                        claim.claim_id,
                    )
            self.run_registry.release(conversation_id)
            raise

    def dump_ui_messages(self, conversation_id: str) -> AiWorkUiMessagesDetail:
        """用官方 Adapter 派生只读 UIMessage[]；不存在时抛 404 协议错误。"""

        normalized_id = str(conversation_id or "").strip()
        try:
            history = self.chat_service.message_store.get(normalized_id)
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        if history is None:
            raise VercelUiProtocolError(
                404,
                "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND",
                "Pydantic 对话不存在。",
            )
        try:
            messages = history.model_messages()
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        ui_messages = VercelAIAdapter.dump_messages(
            messages,
            sdk_version=VERCEL_SDK_VERSION,
        )
        return {
            "ok": True,
            "conversation_id": history.conversation_id,
            "created_at": history.created_at,
            "updated_at": history.updated_at,
            "messages": [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in ui_messages
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
                "只接受 submit-message；不支持 regenerate 或客户端工具审批请求。",
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
        if history is None:
            return
        claim = self.claim_store.find_for_conversation(conversation_id)
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


__all__ = [
    "MAX_USER_TEXT_CHARS",
    "VERCEL_SDK_VERSION",
    "VercelAiChatRun",
    "VercelAiUiService",
    "VercelUiProtocolError",
]
