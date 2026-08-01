"""受限 AI 工具执行 Runtime。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from erp_web.schemas.ai_tools import (
    AiToolCall,
    AiToolResult,
    AiToolSchemaError,
    validate_json_schema,
)
from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_invocation import AiWorkRecorder
from .ai_tool_registry import AiToolSet


class AiToolRuntime:
    """查找、校验、授权、去重、执行并记录一个 ToolSet 中的调用。

    Runtime 不创建无法安全取消的后台线程。同步 executor 必须在注册时声明
    cooperative deadline，并把 execution context 给出的 timeout 用于全部阻塞 I/O。
    """

    def __init__(
        self,
        *,
        toolset: AiToolSet,
        execution_context: AiExecutionContext,
        recorder: AiWorkRecorder,
        max_tool_calls: int = 4,
        max_output_bytes: int = 64 * 1024,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes 必须大于 0")
        self.toolset = toolset
        self.execution_context = execution_context
        self.recorder = recorder
        self.max_tool_calls = max_tool_calls
        self.max_output_bytes = max_output_bytes
        self._calls_by_id: dict[str, tuple[str, AiToolResult]] = {}
        self._results_by_signature: dict[str, AiToolResult] = {}

    @property
    def unique_call_count(self) -> int:
        return len(self._calls_by_id)

    @staticmethod
    def _signature(call: AiToolCall) -> str:
        payload = {
            "tool_name": call.tool_name,
            "tool_version": call.tool_version,
            "arguments": call.to_dict()["arguments"],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _record_started(self, call: AiToolCall) -> None:
        self.recorder.record(
            "TOOL_CALL_STARTED",
            tool_call_id=call.call_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            round=call.round,
            arguments=call.to_dict()["arguments"],
        )

    def _record_finished(self, result: AiToolResult) -> None:
        self.recorder.record(
            "TOOL_CALL_FINISHED",
            tool_call_id=result.call_id,
            tool_name=result.tool_name,
            result=result.to_dict(),
        )

    @staticmethod
    def _error_result(
        call: AiToolCall,
        *,
        code: str,
        message: str,
        duration_ms: int = 0,
        truncated: bool = False,
    ) -> AiToolResult:
        return AiToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            ok=False,
            error={"code": code, "message": message},
            duration_ms=max(0, duration_ms),
            truncated=truncated,
        )

    def _remember(
        self,
        call: AiToolCall,
        signature: str,
        result: AiToolResult,
        *,
        cache_signature: bool = True,
    ) -> AiToolResult:
        self._calls_by_id[call.call_id] = (signature, result)
        if cache_signature:
            self._results_by_signature[signature] = result
        self._record_finished(result)
        return result

    def execute(self, call: AiToolCall) -> AiToolResult:
        signature = self._signature(call)
        previous_call = self._calls_by_id.get(call.call_id)
        if previous_call is not None:
            previous_signature, previous_result = previous_call
            if previous_signature != signature:
                conflict = self._error_result(
                    call,
                    code="TOOL_CALL_INVALID",
                    message="同一 call_id 不能使用不同工具、版本或参数",
                )
                self._record_started(call)
                self._record_finished(conflict)
                return conflict
            result = previous_result.as_deduplicated()
            self._record_started(call)
            self._record_finished(result)
            return result

        self._record_started(call)
        if self.execution_context.expired():
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TASK_DEADLINE_EXCEEDED",
                    message="AI Task 总 deadline 已耗尽",
                ),
                cache_signature=False,
            )
        if self.unique_call_count >= self.max_tool_calls:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_CALL_BUDGET_EXCEEDED",
                    message=f"工具调用数超过上限 {self.max_tool_calls}",
                ),
                cache_signature=False,
            )

        deduplicated = self._results_by_signature.get(signature)
        if deduplicated is not None:
            result = deduplicated.as_deduplicated(call_id=call.call_id)
            return self._remember(call, signature, result)

        binding = self.toolset.get(call.tool_name)
        if binding is None:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_NOT_ALLOWED",
                    message=f"ToolSet {self.toolset.toolset_id} 未允许工具 {call.tool_name}",
                ),
            )
        definition = binding.definition
        if call.tool_version != definition.version:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_VERSION_MISMATCH",
                    message=(
                        f"工具 {call.tool_name} 版本不匹配："
                        f"请求 {call.tool_version}，允许 {definition.version}"
                    ),
                ),
            )
        if definition.required_permission not in self.execution_context.permissions:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_PERMISSION_DENIED",
                    message=f"缺少权限 {definition.required_permission}",
                ),
            )
        if definition.side_effect == "write" and not self.execution_context.allow_write:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_WRITE_NOT_ALLOWED",
                    message=f"当前 invocation 不允许执行写工具 {call.tool_name}",
                ),
            )
        if (
            definition.approval_required
            and call.call_id not in self.execution_context.approved_tool_call_ids
        ):
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_APPROVAL_REQUIRED",
                    message=f"工具 {call.tool_name} 需要显式审批",
                ),
            )
        arguments = call.to_dict()["arguments"]
        try:
            validate_json_schema(arguments, definition.input_schema)
        except AiToolSchemaError as exc:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_INPUT_SCHEMA_INVALID",
                    message=str(exc),
                ),
            )

        started_at = time.monotonic()
        try:
            output = binding.executor(
                arguments,
                self.execution_context,
            )
        except Exception as exc:
            duration_ms = round((time.monotonic() - started_at) * 1000)
            error_code = (
                "TASK_DEADLINE_EXCEEDED"
                if isinstance(exc, TimeoutError)
                else str(getattr(exc, "code", "") or "TOOL_EXECUTION_FAILED")
            )
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code=error_code,
                    message=str(exc) or exc.__class__.__name__,
                    duration_ms=duration_ms,
                ),
            )
        duration_ms = round((time.monotonic() - started_at) * 1000)
        if self.execution_context.expired():
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TASK_DEADLINE_EXCEEDED",
                    message="工具执行完成时 AI Task 总 deadline 已耗尽",
                    duration_ms=duration_ms,
                ),
                cache_signature=False,
            )
        try:
            validate_json_schema(output, definition.output_schema)
        except AiToolSchemaError as exc:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_OUTPUT_SCHEMA_INVALID",
                    message=str(exc),
                    duration_ms=duration_ms,
                ),
            )
        try:
            output_size = len(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_OUTPUT_SCHEMA_INVALID",
                    message=f"工具输出不是 JSON：{exc}",
                    duration_ms=duration_ms,
                ),
            )
        if output_size > self.max_output_bytes:
            return self._remember(
                call,
                signature,
                self._error_result(
                    call,
                    code="TOOL_OUTPUT_TOO_LARGE",
                    message=(
                        f"工具输出 {output_size} 字节，超过上限 "
                        f"{self.max_output_bytes} 字节"
                    ),
                    duration_ms=duration_ms,
                    truncated=True,
                ),
            )
        return self._remember(
            call,
            signature,
            AiToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=True,
                output=output,
                duration_ms=duration_ms,
            ),
        )


__all__ = ["AiToolRuntime"]
