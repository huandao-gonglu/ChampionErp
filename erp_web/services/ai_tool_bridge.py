"""把 ERP ToolSet 安全转换为 Pydantic AI FunctionToolset。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import CallDeferred

from erp_web.schemas.ai_tools import (
    AiToolCommand, AiToolDefinition, AiToolSchemaError, AiToolExecutionError, validate_json_schema,
)

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_budget import AgentToolBudgetRetry
from .ai_tool_registry import AiToolSet


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
            )
        )
        payload = result.to_dict()
        if not result.ok:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_code = str(error.get("code") or "TOOL_EXECUTION_FAILED")
            raise AiToolBridgeError(
                code=error_code,
                message=str(error.get("message") or ""),
                tool_name=tool_name,
                tool_call_id=call_id,
                retryable=bool(error.get("retryable")),
                model_visible=dependencies.tool_runtime.is_model_visible_error(
                    call_id
                ),
            )
        return payload.get("output")

    def _pydantic_tool(
        self,
        definition: AiToolDefinition,
    ) -> Tool[AiAgentDependencies]:
        agent_deferred = definition.agent_deferred

        def require_budget(ctx: RunContext[AiAgentDependencies]) -> None:
            runtime = ctx.deps.tool_runtime
            # 同批调用的原生 usage 可能在整批结束后更新；Runtime 现有账本提供
            # 串行执行时的即时用量，不创建另一套计数器。
            used = max(ctx.usage.tool_calls, runtime.unique_call_count)
            if used >= runtime.max_tool_calls:
                raise AgentToolBudgetRetry(limit=runtime.max_tool_calls, used=used, requested=1)

        def validate_arguments(ctx: RunContext[AiAgentDependencies], **arguments: Any) -> None:
            # from_schema 不自动校验参数；在执行前交给原生重试，不触发业务副作用。
            self._require_runtime_binding(ctx.deps)
            require_budget(ctx)
            try:
                validate_json_schema(arguments, definition.input_schema)
                binding = self.toolset.bindings[definition.name]
                if binding.arguments_validator is not None:
                    binding.arguments_validator(arguments)
            except AiToolExecutionError as exc:
                raise AiToolArgumentRetry(code=exc.code, message=str(exc), tool_name=definition.name) from exc
            except AiToolSchemaError as exc:
                raise AiToolArgumentRetry(code="TOOL_INPUT_SCHEMA_INVALID", message=f"工具参数不符合定义：{exc}", tool_name=definition.name) from exc

        def invoke(
            ctx: RunContext[AiAgentDependencies],
            **arguments: Any,
        ) -> Any:
            require_budget(ctx)
            try:
                output = self.execute(
                    dependencies=ctx.deps,
                    tool_name=definition.name,
                    tool_call_id=str(ctx.tool_call_id or ""),
                    arguments=arguments,
                    round_number=ctx.run_step,
                )
            except AiToolBridgeError as exc:
                if not exc.model_visible:
                    raise
                # Deferred 控制工具创建失败时必须以稳定错误闭合本次调用，
                # 不能产生第二个未解决 Deferred。
                return {
                    "ok": False,
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                    },
                }
            if not agent_deferred:
                return output
            task_id = str(
                (output or {}).get("task_id")
                if isinstance(output, Mapping)
                else ""
            ).strip()
            if not task_id:
                raise AiToolBridgeError(
                    code="GLOBAL_TASK_DEFERRED_ACCEPTANCE_INVALID",
                    message="Deferred 控制工具未返回可信 task_id，不能挂起。",
                    tool_name=definition.name,
                    tool_call_id=str(ctx.tool_call_id or ""),
                )
            # CallDeferred 是 Pydantic 异常，不是返回值；它不进入 ERP JSON
            # result 序列化，也不会被 AiToolRuntime 捕获成 TOOL_EXECUTION_FAILED。
            # 置位 run 级标志：协议层从该时刻起把官方编码事件切入「事务提交
            # 后才发布」的有界缓冲，终态事件不得先于 history/link/outbox 提交。
            ctx.deps.tool_runtime.deferred_call_started = True
            raise CallDeferred(metadata={"task_id": task_id})

        invoke.__name__ = definition.name
        return Tool.from_schema(
            invoke,
            name=definition.name,
            description=definition.description,
            json_schema=definition.to_dict()["input_schema"],
            takes_ctx=True,
            sequential=True,
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
