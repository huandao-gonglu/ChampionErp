"""把 ERP ToolSet 安全转换为 Pydantic AI FunctionToolset。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Mapping

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred

from erp_web.schemas.ai_tools import (
    AiToolCommand,
    AiToolDefinition,
    AiToolSchemaError,
    AiToolExecutionError,
    validate_json_schema,
)

from .ai_agent_dependencies import AiAgentDependencies
from .ai_tool_registry import AiToolSet
from .capability_errors import BusinessCapabilityError


class AiToolArgumentRetry(ModelRetry):
    """安全参数错误；纠正、次数限制和工具消息闭合由原生 Agent 负责。"""

    def __init__(self, *, code: str, message: str, tool_name: str) -> None:
        self.code = code
        self.validation_message = message
        self.tool_name = tool_name
        super().__init__(f"{code}: {message}")


def tool_argument_retry_error(exc: Exception) -> AiToolArgumentRetry | None:
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, AiToolArgumentRetry):
            return cause
        cause = cause.__cause__ or cause.__context__
    return None


class AiToolBridgeError(RuntimeError):
    """ERP Runtime 拒绝或无法完成一次 Pydantic tool call。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        tool_name: str,
        tool_call_id: str,
        retryable: bool = False,
        model_visible: bool = False,
    ) -> None:
        self.code = str(code or "TOOL_EXECUTION_FAILED")
        self.retryable = bool(retryable)
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.model_visible = bool(model_visible)
        super().__init__(str(message or "").strip() or f"工具 {tool_name} 执行失败。")


@dataclass(frozen=True)
class PydanticToolBridge:
    """固定 Pydantic schema 与 ERP Runtime allowlist 的一一对应关系。"""

    toolset: AiToolSet

    def _require_runtime_binding(self, dependencies: AiAgentDependencies) -> None:
        if dependencies.tool_runtime.toolset is not self.toolset:
            raise AiToolBridgeError(
                code="TOOLSET_BINDING_MISMATCH",
                message="Agent ToolSet 与 Runtime 绑定不一致。",
                tool_name=self.toolset.toolset_id,
                tool_call_id="unbound",
            )

    def execute(
        self,
        *,
        dependencies: AiAgentDependencies,
        tool_name: str,
        tool_call_id: str,
        arguments: Mapping[str, Any],
        round_number: int,
        execution_context: Any = None,
    ) -> Any:
        """唯一执行入口；不会读取或调用 ToolSet binding.executor。"""

        self._require_runtime_binding(dependencies)
        call_id = str(tool_call_id or "").strip()
        if not call_id:
            raise AiToolBridgeError(
                code="TOOL_CALL_INVALID",
                message="工具调用缺少 call_id。",
                tool_name=tool_name,
                tool_call_id="missing",
            )
        binding = self.toolset.get(tool_name)
        if binding is None:
            raise AiToolBridgeError(
                code="TOOL_NOT_ALLOWED",
                message=f"当前 ToolSet 未允许工具 {tool_name}。",
                tool_name=tool_name,
                tool_call_id=call_id,
            )
        result = dependencies.tool_runtime.execute(
            AiToolCommand(
                call_id=call_id,
                tool_name=tool_name,
                tool_version=binding.definition.version,
                arguments=dict(arguments),
                round=max(1, int(round_number)),
            ),
            execution_context=execution_context,
        )
        payload = result.to_dict()
        if not result.ok:
            error = (
                payload.get("error") if isinstance(payload.get("error"), dict) else {}
            )
            error_code = str(error.get("code") or "TOOL_EXECUTION_FAILED")
            if dependencies.tool_runtime.is_model_visible_error(call_id) or error.get(
                "details", {}
            ).get("outcome_unknown"):
                return payload
            raise AiToolBridgeError(
                code=error_code,
                message=str(error.get("message") or ""),
                tool_name=tool_name,
                tool_call_id=call_id,
                retryable=bool(error.get("retryable")),
                model_visible=dependencies.tool_runtime.is_model_visible_error(call_id),
            )
        return payload.get("output")

    def _pydantic_tool(
        self,
        definition: AiToolDefinition,
    ) -> Tool[AiAgentDependencies]:
        def validate_arguments(
            ctx: RunContext[AiAgentDependencies], **arguments: Any
        ) -> None:
            self._require_runtime_binding(ctx.deps)
            try:
                validate_json_schema(arguments, definition.input_schema)
                binding = self.toolset.bindings[definition.name]
                if binding.arguments_validator is not None:
                    binding.arguments_validator(arguments)
            except AiToolExecutionError as exc:
                raise AiToolArgumentRetry(
                    code=exc.code, message=str(exc), tool_name=definition.name
                ) from exc
            except AiToolSchemaError as exc:
                raise AiToolArgumentRetry(
                    code="TOOL_INPUT_SCHEMA_INVALID",
                    message=f"工具参数不符合定义：{exc}",
                    tool_name=definition.name,
                ) from exc

        def invoke(ctx: RunContext[AiAgentDependencies], **arguments: Any) -> Any:
            runtime = ctx.deps.tool_runtime
            support = runtime.run_support
            writing = definition.side_effect == "write"
            if support is not None:
                support.before_tool(ctx, writing=writing)
                execution = support.execution(ctx, arguments)
            else:
                execution = replace(
                    ctx.deps.execution_context,
                    approved_tool_call_ids=frozenset({str(ctx.tool_call_id)})
                    if ctx.tool_call_approved
                    else frozenset(),
                )
            metadata = dict(ctx.tool_call_metadata or {})
            if support is not None and writing:
                unknown = support.store.unresolved_outcome(
                    support.conversation_id, definition.name, arguments
                )
                if unknown is not None:
                    return unknown
            if definition.approval_required and not ctx.tool_call_approved:
                from .tool_approval import approval_binding_digest

                binding = self.toolset.bindings[definition.name]
                try:
                    snapshot = binding.approval_preparer(arguments)
                except (AiToolExecutionError, BusinessCapabilityError) as exc:
                    return {
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": str(exc),
                            "details": exc.details,
                        },
                    }
                revision = support.version if support is not None else 1
                metadata = {
                    "summary": snapshot.summary,
                    "approval_revision": revision,
                    "approval_digest": approval_binding_digest(
                        snapshot=snapshot,
                        capability_name=definition.name,
                        capability_version=definition.version,
                        operation_key=execution.idempotency_context.get(
                            "operation_key", ""
                        ),
                        tool_call_id=str(ctx.tool_call_id),
                        approval_revision=revision,
                    ),
                }
                raise ApprovalRequired(metadata=metadata)
            if definition.execution_mode == "persistent_job":
                # 先由原生 Agent 产生请求，history/request/投递记录同事务提交后才运行。
                raise CallDeferred(metadata=metadata)
            if support is not None and writing:
                if not support.store.begin_write(
                    support.conversation_id,
                    str(ctx.tool_call_id),
                    definition.name,
                    arguments,
                ):
                    receipt = support.store.receipt(
                        support.conversation_id, str(ctx.tool_call_id)
                    )
                    if receipt and (
                        receipt["tool_name"] != definition.name
                        or json.loads(receipt["arguments_json"]) != arguments
                    ):
                        raise AiToolBridgeError(
                            code="TOOL_CALL_ID_CONFLICT",
                            message="工具调用 ID 已用于其他参数，不能重新执行。",
                            tool_name=definition.name,
                            tool_call_id=str(ctx.tool_call_id),
                        )
                    if receipt and receipt["output_json"]:
                        return json.loads(receipt["output_json"])
                    return {
                        "ok": False,
                        "error": {
                            "code": "TOOL_OUTCOME_UNKNOWN",
                            "message": "该调用已开始但缺少完成回执，请先查询业务状态。",
                            "details": {"outcome_unknown": True},
                        },
                    }
            try:
                output = self.execute(
                    dependencies=ctx.deps,
                    tool_name=definition.name,
                    tool_call_id=str(ctx.tool_call_id or ""),
                    arguments=arguments,
                    round_number=ctx.run_step,
                    execution_context=execution,
                )
            except Exception:
                if support is not None and writing:
                    support.store.mark_unknown(
                        support.conversation_id, str(ctx.tool_call_id)
                    )
                raise
            if support is not None and writing:
                support.store.finish(
                    support.conversation_id, str(ctx.tool_call_id), output
                )
            return output

        invoke.__name__ = definition.name
        # 可信 Injected 参数不暴露给模型；from_schema 仅保留机械参数适配。
        # 同步函数由 Pydantic 在线程中执行，独立调用使用原生并发调度。
        return Tool.from_schema(
            invoke,
            name=definition.name,
            description=definition.description,
            json_schema=definition.to_dict()["input_schema"],
            takes_ctx=True,
            sequential=False,
            args_validator=validate_arguments,
        )

    def as_toolset(self) -> FunctionToolset[AiAgentDependencies]:
        return FunctionToolset(
            tools=[self._pydantic_tool(item) for item in self.toolset.definitions],
            id=self.toolset.toolset_id,
        )


def build_pydantic_toolset(
    toolset: AiToolSet,
) -> FunctionToolset[AiAgentDependencies]:
    return PydanticToolBridge(toolset).as_toolset()


__all__ = [
    "AiToolBridgeError",
    "tool_argument_retry_error",
    "PydanticToolBridge",
    "build_pydantic_toolset",
]
