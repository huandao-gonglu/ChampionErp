"""共享的最小 AI Task tool-loop 执行框架。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

from erp_web.schemas.ai_tools import (
    AiToolSchemaError,
    AiToolTurn,
    validate_json_schema,
)

from .ai_invocation import AiInvocation
from .ai_provider_contracts import AiToolTurnProvider, AiToolTurnRequest
from .ai_tool_registry import AiToolSet
from .ai_tool_runtime import AiToolRuntime


class AiTaskExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AiTaskRunner:
    max_tool_rounds: int = 3
    max_tool_calls: int = 4
    max_tool_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if self.max_tool_rounds < 1:
            raise ValueError("max_tool_rounds 必须大于 0")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        if self.max_tool_output_bytes < 1:
            raise ValueError("max_tool_output_bytes 必须大于 0")

    @staticmethod
    def _ensure_deadline(invocation: AiInvocation) -> None:
        if invocation.execution_context.expired():
            raise AiTaskExecutionError(
                "TASK_DEADLINE_EXCEEDED",
                "AI Task 总 deadline 已耗尽",
            )

    def run(
        self,
        invocation: AiInvocation,
        *,
        messages: Sequence[Mapping[str, Any]],
        toolset: AiToolSet,
        result_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            provider = invocation.provider
            if not isinstance(provider, AiToolTurnProvider):
                raise AiTaskExecutionError(
                    "TOOL_PROTOCOL_UNSUPPORTED",
                    f"Provider {provider.provider_id} 未实现 tool-turn 协议",
                )
            runtime = AiToolRuntime(
                toolset=toolset,
                execution_context=invocation.execution_context,
                recorder=invocation.recorder,
                max_tool_calls=self.max_tool_calls,
                max_output_bytes=self.max_tool_output_bytes,
            )
            safe_messages = tuple(dict(message) for message in messages)
            accumulated_results = []
            tool_rounds = 0
            model_round = 1
            invocation.recorder.record(
                "TASK_STARTED",
                use_case_id=invocation.use_case_id,
                toolset_id=toolset.toolset_id,
            )
            while True:
                self._ensure_deadline(invocation)
                model_call_id = f"model_{uuid4().hex}"
                invocation.recorder.record(
                    "MODEL_CALL_STARTED",
                    model_call_id=model_call_id,
                    round=model_round,
                )
                try:
                    turn = provider.run_tool_turn(
                        AiToolTurnRequest(
                            invocation=invocation,
                            messages=safe_messages,
                            tools=toolset.definitions,
                            tool_results=tuple(accumulated_results),
                            round=model_round,
                        )
                    )
                except AiToolSchemaError as exc:
                    raise AiTaskExecutionError(
                        "MODEL_RESPONSE_SCHEMA_INVALID",
                        str(exc),
                    ) from exc
                if not isinstance(turn, AiToolTurn):
                    raise AiTaskExecutionError(
                        "MODEL_RESPONSE_SCHEMA_INVALID",
                        "AiToolTurnProvider 必须返回 AiToolTurn",
                    )
                self._ensure_deadline(invocation)
                invocation.recorder.record(
                    "MODEL_CALL_FINISHED",
                    model_call_id=model_call_id,
                    round=model_round,
                    turn_type=turn.type,
                )
                if turn.type == "final":
                    result = turn.to_dict()["result"]
                    if result_schema is not None:
                        try:
                            validate_json_schema(result, result_schema)
                        except AiToolSchemaError as exc:
                            raise AiTaskExecutionError(
                                "MODEL_RESPONSE_SCHEMA_INVALID",
                                str(exc),
                            ) from exc
                    invocation.recorder.record("TASK_FINISHED", result=result)
                    invocation.recorder.finish({"result": result})
                    return result

                if tool_rounds >= self.max_tool_rounds:
                    raise AiTaskExecutionError(
                        "TOOL_CALL_BUDGET_EXCEEDED",
                        f"tool round 超过上限 {self.max_tool_rounds}",
                    )
                expected_round = tool_rounds + 1
                if any(call.round != expected_round for call in turn.calls):
                    raise AiTaskExecutionError(
                        "TOOL_CALL_INVALID",
                        f"tool call round 必须为 {expected_round}",
                    )
                tool_rounds += 1
                for call in turn.calls:
                    accumulated_results.append(runtime.execute(call))
                model_round += 1
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, AiTaskExecutionError)
                else AiTaskExecutionError(exc.__class__.__name__, str(exc))
            )
            invocation.recorder.record(
                "TASK_FAILED",
                code=error.code,
                message=str(error),
            )
            invocation.recorder.fail(error)
            raise error


__all__ = ["AiTaskExecutionError", "AiTaskRunner"]
