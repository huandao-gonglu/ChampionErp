"""一次 AI 调用的解析结果、deadline 与 AI Work recorder 边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_work_service import AiWorkConversation


class AiWorkRecorder(Protocol):
    """Pydantic Agent 与普通 Provider invocation 共用的业务记录器接口。"""

    @property
    def conversation_id(self) -> str:
        ...

    def record(self, event_type: str, **payload: Any) -> None:
        ...

    def emit(self, event_type: str, **payload: Any) -> Any:
        ...

    def emit_custom(self, name: str, value: Any) -> Any:
        ...

    def emit_text_delta(self, delta: str) -> None:
        ...

    def emit_reasoning_delta(self, delta: str) -> None:
        ...

    def finish_reasoning_message(self) -> None:
        ...

    def finish_assistant_message(self, raw_text: str = "") -> None:
        ...

    def finish(self, result: Any) -> None:
        ...

    def fail(self, error: Exception) -> None:
        ...


@dataclass(frozen=True)
class ConversationAiWorkRecorder:
    """把现有 AI Work conversation 适配成单次 invocation recorder。"""

    conversation: AiWorkConversation
    execution_context: AiExecutionContext

    @property
    def conversation_id(self) -> str:
        return self.conversation.conversation_id

    def record(self, event_type: str, **payload: Any) -> None:
        self.emit_custom(event_type, payload)

    def emit(self, event_type: str, **payload: Any) -> Any:
        return self.conversation.emit(event_type, **payload)

    def emit_custom(self, name: str, value: Any) -> Any:
        return self.conversation.emit_custom(name, value)

    def emit_text_delta(self, delta: str) -> None:
        self.conversation.emit_text_delta(delta)

    def emit_reasoning_delta(self, delta: str) -> None:
        self.conversation.emit_reasoning_delta(delta)

    def finish_reasoning_message(self) -> None:
        self.conversation.finish_reasoning_message()

    def finish_assistant_message(self, raw_text: str = "") -> None:
        self.conversation.finish_assistant_message(raw_text)

    def finish(self, result: Any) -> None:
        self.emit_custom("business.result", result)
        self.emit("RUN_FINISHED", result=result)

    def fail(self, error: Exception) -> None:
        self.finish_assistant_message()
        payload = {
            key: value
            for key in ("trace_id", "run_id", "task_run_id")
            if (value := str(getattr(error, key, "") or ""))
        }
        self.emit(
            "RUN_ERROR",
            message=str(error),
            code=getattr(error, "code", error.__class__.__name__),
            retryable=bool(getattr(error, "retryable", False)),
            **payload,
        )


@dataclass(frozen=True)
class AiInvocation:
    """一次已解析 provider/model 且只绑定一个 recorder 的 AI 调用。"""

    use_case_id: str
    capability: str
    provider_id: str
    model: dict[str, Any]
    required_capabilities: tuple[str, ...]
    timeout_seconds: int
    execution_context: AiExecutionContext
    recorder: AiWorkRecorder
    generation_settings: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.use_case_id:
            raise ValueError("AiInvocation.use_case_id 不能为空")
        if not self.capability:
            raise ValueError("AiInvocation.capability 不能为空")
        if not self.provider_id:
            raise ValueError("AiInvocation.provider_id 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("AiInvocation.timeout_seconds 必须大于 0")
        object.__setattr__(self, "model", dict(self.model))
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(self.required_capabilities),
        )
        object.__setattr__(
            self,
            "generation_settings",
            dict(self.generation_settings or {}),
        )


__all__ = [
    "AiInvocation",
    "AiWorkRecorder",
    "ConversationAiWorkRecorder",
]
