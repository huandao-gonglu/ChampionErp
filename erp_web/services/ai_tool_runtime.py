"""受限 AI 工具执行 Runtime。"""

from __future__ import annotations

import hashlib
import json
import time
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping

from erp_web.schemas.ai_tools import (
    AiToolCommand,
    AiToolExecutionError,
    AiToolResult,
    AiToolSchemaError,
    validate_json_schema,
)
from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_tool_registry import AiToolSet
from .ai_run_cancellation import check_cancellation
from pydantic_ai import RunCancelled


_HIDDEN_EXECUTION_ERROR_MESSAGE = "工具执行失败，请稍后重试。"

# executor 已完成领域写入后，若在结果投影阶段（Schema 校验、JSON 编码、
# 大小检查）失败，错误必须携带该最小详情：调用方不得把投影失败解释成
# “业务没有执行”。调用方必须对账后才能决定是否重试。
_RESULT_PROJECTION_FAILURE_DETAILS: Mapping[str, Any] = MappingProxyType(
    {
        "outcome_unknown": True,
        "failure_stage": "result_projection",
        "side_effect_may_have_completed": True,
    }
)


def _executor_error_details(
    exc: Exception,
) -> tuple[str, str, bool, Mapping[str, Any] | None]:
    """只公开显式安全错误；未知异常不得穿透工具边界。"""

    if isinstance(exc, TimeoutError):
        return "TASK_DEADLINE_EXCEEDED", "工具执行超时。", True, None
    if isinstance(exc, AiToolExecutionError):
        return exc.code, str(exc), exc.retryable, exc.details
    if isinstance(exc, AiToolSchemaError):
        return exc.code, str(exc), False, None
    return "TOOL_EXECUTION_FAILED", _HIDDEN_EXECUTION_ERROR_MESSAGE, True, None


class AiToolRuntime:
    """查找、校验、授权、去重并执行一个 ToolSet 中的调用。

    Runtime 不创建无法安全取消的后台线程。同步 executor 必须在注册时声明
    cooperative deadline，并把 execution context 给出的 timeout 用于全部阻塞 I/O。
    """

    def __init__(
        self,
        *,
        toolset: AiToolSet,
        execution_context: AiExecutionContext,
        max_tool_calls: int = 4,
        max_output_bytes: int = 64 * 1024,
        before_executor: Callable[[AiToolCommand], None] | None = None,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于 0")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes 必须大于 0")
        self.toolset = toolset
        self.execution_context = execution_context
        self.max_tool_calls = max_tool_calls
        self.max_output_bytes = max_output_bytes
        self.before_executor = before_executor
        self._calls_by_id: dict[str, tuple[str, AiToolResult]] = {}
        self._call_locks: dict[str, threading.Lock] = {}
        self._cache_lock = threading.RLock()
        self.run_support: Any = None
        self._model_visible_error_signatures: set[str] = set()
        self._model_visible_call_ids: set[str] = set()

    @property
    def unique_call_count(self) -> int:
        return len(self._calls_by_id)

    def is_model_visible_error(self, call_id: str) -> bool:
        """该调用是否因显式安全的 executor 业务错误失败。"""

        recorded = self._calls_by_id.get(str(call_id or "").strip())
        return bool(
            str(call_id or "").strip() in self._model_visible_call_ids
            or (recorded and recorded[0] in self._model_visible_error_signatures)
        )

    @staticmethod
    def _signature(command: AiToolCommand) -> str:
        payload = {
            "tool_name": command.tool_name,
            "tool_version": command.tool_version,
            "arguments": command.arguments_dict(),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _error_result(
        command: AiToolCommand,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        duration_ms: int = 0,
        truncated: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> AiToolResult:
        error: dict[str, Any] = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        if details is not None:
            error["details"] = dict(details)
        return AiToolResult(
            call_id=command.call_id,
            tool_name=command.tool_name,
            ok=False,
            error=error,
            duration_ms=max(0, duration_ms),
            truncated=truncated,
        )

    def _remember(
        self,
        command: AiToolCommand,
        signature: str,
        result: AiToolResult,
        *,
        cache_signature: bool = True,
    ) -> AiToolResult:
        self._calls_by_id[command.call_id] = (signature, result)
        return result

    def execute(
        self,
        command: AiToolCommand,
        *,
        execution_context: AiExecutionContext | None = None,
    ) -> AiToolResult:
        # 仅按调用身份防止同一调用同时执行；不同调用和只读查询不缓存旧结果。
        with self._cache_lock:
            lock = self._call_locks.setdefault(command.call_id, threading.Lock())
        with lock:
            return self._execute(command, execution_context or self.execution_context)

    def _execute(
        self, command: AiToolCommand, execution: AiExecutionContext
    ) -> AiToolResult:
        signature = self._signature(command)
        previous_call = self._calls_by_id.get(command.call_id)
        if previous_call is not None:
            previous_signature, previous_result = previous_call
            if previous_signature != signature:
                conflict = self._error_result(
                    command,
                    code="TOOL_CALL_INVALID",
                    message="同一 call_id 不能使用不同工具、版本或参数",
                )
                return conflict
            result = previous_result.as_deduplicated()
            return result

        if execution.expired():
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TASK_DEADLINE_EXCEEDED",
                    message="AI Task 总 deadline 已耗尽",
                    retryable=True,
                ),
                cache_signature=False,
            )

        binding = self.toolset.get(command.tool_name)
        if binding is None:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_NOT_ALLOWED",
                    message=(
                        f"ToolSet {self.toolset.toolset_id} "
                        f"未允许工具 {command.tool_name}"
                    ),
                ),
            )
        definition = binding.definition
        if command.tool_version != definition.version:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_VERSION_MISMATCH",
                    message=(
                        f"工具 {command.tool_name} 版本不匹配："
                        f"请求 {command.tool_version}，允许 {definition.version}"
                    ),
                ),
            )
        if definition.required_permission not in execution.permissions:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_PERMISSION_DENIED",
                    message=f"缺少权限 {definition.required_permission}",
                ),
            )
        if definition.side_effect == "write" and not execution.allow_write:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_WRITE_NOT_ALLOWED",
                    message=f"当前 invocation 不允许执行写工具 {command.tool_name}",
                ),
            )
        if definition.idempotency == "required":
            missing_idempotency_keys = [
                key
                for key in definition.idempotency_keys
                if not str(execution.idempotency_context.get(key) or "").strip()
            ]
            if missing_idempotency_keys:
                return self._remember(
                    command,
                    signature,
                    self._error_result(
                        command,
                        code="TOOL_IDEMPOTENCY_CONTEXT_REQUIRED",
                        message=(
                            "可信幂等上下文缺少工具要求的键："
                            + "、".join(missing_idempotency_keys)
                        ),
                    ),
                    cache_signature=False,
                )
        arguments = command.arguments_dict()
        try:
            validate_json_schema(arguments, definition.input_schema)
        except AiToolSchemaError as exc:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_INPUT_SCHEMA_INVALID",
                    message=str(exc),
                ),
            )
        if (
            definition.approval_required
            and command.call_id not in execution.approved_tool_call_ids
        ):
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_APPROVAL_REQUIRED",
                    message=f"工具 {command.tool_name} 需要显式审批",
                ),
            )

        if self.before_executor is not None and definition.side_effect == "write":
            try:
                self.before_executor(command)
            except Exception:
                return self._remember(
                    command,
                    signature,
                    self._error_result(
                        command,
                        code="TOOL_EXECUTION_CHECKPOINT_FAILED",
                        message="工具执行前检查点无法持久化",
                        retryable=True,
                    ),
                    cache_signature=False,
                )

        call_execution = execution
        started_at = time.monotonic()
        check_cancellation()
        try:
            output = binding.executor(
                arguments,
                call_execution,
            )
        except RunCancelled:
            raise
        except Exception as exc:
            duration_ms = round((time.monotonic() - started_at) * 1000)
            if definition.side_effect == "write" and not isinstance(
                exc, (AiToolExecutionError, AiToolSchemaError)
            ):
                self._model_visible_call_ids.add(command.call_id)
            if isinstance(exc, AiToolExecutionError):
                # 业务 executor 显式声明的安全错误可回到模型；鉴权、预算、
                # schema、deadline 与未知异常都不进入这个集合。
                self._model_visible_error_signatures.add(signature)
            error_code, error_message, retryable, error_details = (
                _executor_error_details(exc)
            )
            if definition.side_effect == "write" and not isinstance(
                exc, (AiToolExecutionError, AiToolSchemaError)
            ):
                error_details = dict(_RESULT_PROJECTION_FAILURE_DETAILS)
                retryable = False
            if (
                definition.side_effect != "none"
                and isinstance(exc, AiToolSchemaError)
                and exc.code == "TOOL_OUTPUT_SCHEMA_INVALID"
            ):
                # executor 已执行完领域写入，仅结果投影失败：不得被解释成
                # 业务未执行。输入校验失败使用 TOOL_INPUT_SCHEMA_INVALID，
                # 发生在副作用之前，不进入该分支。
                error_details = {
                    **(error_details or {}),
                    **_RESULT_PROJECTION_FAILURE_DETAILS,
                }
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code=error_code,
                    message=error_message,
                    retryable=retryable,
                    duration_ms=duration_ms,
                    details=error_details,
                ),
            )
        duration_ms = round((time.monotonic() - started_at) * 1000)
        # executor 成功返回即代表领域副作用（如有）已经发出；此后的任何
        # 投影失败都必须携带 outcome_unknown，而不是普通失败。
        projection_details: Mapping[str, Any] | None = (
            dict(_RESULT_PROJECTION_FAILURE_DETAILS)
            if definition.side_effect != "none"
            else None
        )
        try:
            validate_json_schema(output, definition.output_schema)
        except AiToolSchemaError as exc:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_OUTPUT_SCHEMA_INVALID",
                    message=str(exc),
                    duration_ms=duration_ms,
                    details=projection_details,
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
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_OUTPUT_SCHEMA_INVALID",
                    message=f"工具输出不是 JSON：{exc}",
                    duration_ms=duration_ms,
                    details=projection_details,
                ),
            )
        if output_size > self.max_output_bytes:
            return self._remember(
                command,
                signature,
                self._error_result(
                    command,
                    code="TOOL_OUTPUT_TOO_LARGE",
                    message=(
                        f"工具输出 {output_size} 字节，超过上限 "
                        f"{self.max_output_bytes} 字节"
                    ),
                    duration_ms=duration_ms,
                    truncated=True,
                    details=projection_details,
                ),
            )
        return self._remember(
            command,
            signature,
            AiToolResult(
                call_id=command.call_id,
                tool_name=command.tool_name,
                ok=True,
                output=output,
                duration_ms=duration_ms,
            ),
        )


__all__ = ["AiToolRuntime"]
