"""通用 Python 工具编排；执行与嵌套调用均由官方 CodeMode 负责。"""

from dataclasses import dataclass, replace
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ToolCallPart, ToolReturnPart
from pydantic_ai.messages import ToolReturn
from pydantic_core import to_jsonable_python
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness import CodeMode, ToolOutputLimits
from pydantic_ai_harness.code_mode import CodeModeToolset
from pydantic_ai_harness.tool_output_limits import Band, Truncate

from .ai_tool_registry import AiToolSet
from .ai_agent_dependencies import AiAgentDependencies


def iter_message_parts(message: ModelMessage):
    """同时读取官方 CodeMode 留在原生 metadata 中的真实嵌套回执。"""
    for part in message.parts:
        yield part
        if isinstance(part, ToolReturnPart) and (part.metadata or {}).get("code_mode"):
            for nested in part.metadata.get("tool_calls", {}).values():
                yield ToolCallPart(**nested) if isinstance(nested, dict) else nested
            for nested in part.metadata.get("tool_returns", {}).values():
                yield ToolReturnPart(**nested) if isinstance(nested, dict) else nested


class DirectAndPythonToolset(CodeModeToolset[AiAgentDependencies]):
    """只调整工具可见性，调用与沙箱仍完全由官方 Toolset 执行。"""

    async def get_tools(self, ctx: RunContext[AiAgentDependencies]) -> dict[str, ToolsetTool[AiAgentDependencies]]:
        direct = await self.wrapped.get_tools(ctx)
        python = await super().get_tools(ctx)
        return {**direct, **python}


class DirectAndPythonCodeMode(CodeMode[AiAgentDependencies]):
    """当前官方 CodeMode 没有保留同名直接工具的选项，用公开扩展点补充。"""

    def get_wrapper_toolset(self, toolset: AbstractToolset[AiAgentDependencies]) -> AbstractToolset[AiAgentDependencies]:
        return DirectAndPythonToolset(
            wrapped=toolset, tool_selector=self.tools, max_retries=self.max_retries,
            max_tool_calls=self.max_tool_calls, resource_limits=self.resource_limits, capability=self,
            dynamic_catalog=self.dynamic_catalog,
        )


class PythonOutputLimits(ToolOutputLimits[AiAgentDependencies]):
    def get_toolset(self) -> None:
        """只截断汇总，不生成溢出文件或额外文件读取工具。"""
        return None

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        if call.tool_name == 'run_code' and isinstance(result, ToolReturn) and not isinstance(result.return_value, BaseException):
            # Harness 0.34.0 会把 type([]) 等结果原样返回，随后原生消息的 JSON
            # 编码会失败。仅规范化脚本的对外结果；内核变量、嵌套业务回执不变。
            result = replace(result, return_value=to_jsonable_python(result.return_value, fallback=repr))
        return await super().after_tool_execute(ctx, call=call, tool_def=tool_def, args=args, result=result)


@dataclass
class PythonToolBudget(AbstractCapability[AiAgentDependencies]):
    """嵌套调用也检查原生 UsageLimits；只补记尚未完成的并发调用。"""

    _in_flight: int = 0

    async def for_run(self, ctx: RunContext[AiAgentDependencies]) -> "PythonToolBudget":
        return replace(self, _in_flight=0)

    async def wrap_tool_execute(
        self, ctx: RunContext[AiAgentDependencies], *, call: ToolCallPart,
        tool_def: ToolDefinition, args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        # Core 在模型响应边界检查额度，CodeMode 的脚本内部调用没有该边界。
        # 继续使用原生已完成计数和 UsageLimits，不另存预算或执行计划。
        ctx.usage_limits.check_before_tool_call(replace(
            ctx.usage, tool_calls=ctx.usage.tool_calls + self._in_flight + 1,
        ))
        self._in_flight += 1
        try:
            return await handler(args)
        finally:
            self._in_flight -= 1


def build_python_capabilities(
    toolset: AiToolSet, *, max_tool_calls: int, retries: int, max_output_bytes: int,
) -> list[AbstractCapability[AiAgentDependencies]]:
    """保留所有直接工具；同步、无需审批且非仅直接调用的工具另外可在 Python 中调用。"""
    code_mode = DirectAndPythonCodeMode(
        dynamic_catalog=toolset.defer_loading,
        tools=[
            definition.name
            for binding in toolset.bindings.values()
            if not binding.direct_only
            for definition in (binding.definition,)
            if not definition.approval_required
            and definition.execution_mode != "persistent_job"
        ],
        max_tool_calls=max_tool_calls,
        max_retries=retries,
        resource_limits={
            "max_duration_secs": 10,
            "max_memory": 256 * 1024 * 1024,
            "max_suspensions": 4096,
        },
    )
    # 官方输出限制只作用于脚本汇总，不裁剪传给脚本的业务数据或审计 metadata。
    max_chars = max(1, max_output_bytes // 4)
    return [
        code_mode,
        PythonToolBudget(),
        PythonOutputLimits(
            tool_filter=["run_code"],
            bands=[Band(over=max_chars, action=Truncate(max_chars=max_chars))],
        ),
    ]
