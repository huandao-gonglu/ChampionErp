"""通用 Python 工具编排；执行与嵌套调用均由官方 CodeMode 负责。"""

from dataclasses import dataclass, field, replace
from collections.abc import Awaitable, Callable, Sequence
import time
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai import RunContext, UserError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    CapabilityEvent,
    ModelMessage,
    SystemPromptPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
)
from pydantic_core import to_jsonable_python
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness import CodeMode, ToolOutputLimits
from pydantic_ai_harness.code_mode import CodeModeToolset
from pydantic_ai_harness.tool_output_limits import Band, Truncate

from .ai_model_errors import AI_FATAL_MODEL_ERROR_CODES, AI_FATAL_MODEL_HTTP_STATUSES
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


_DIRECT_ONLY_DISCOVERY_PREFIX = (
    "New direct-only tools have been discovered. They are NOT available inside `run_code` "
    "and must be called directly as top-level tool calls"
)
_DIRECT_AND_PYTHON_SEARCH_NOTE = (
    " Note: only discovered tools listed in the `run_code` available-functions catalog are callable "
    "inside `run_code`; approval-required, persistent-job, or direct-only tools must be called "
    "directly as top-level tool calls."
)


class DirectAndPythonToolset(CodeModeToolset[AiAgentDependencies]):
    """只调整工具可见性，调用与沙箱仍完全由官方 Toolset 执行。"""

    async def get_tools(self, ctx: RunContext[AiAgentDependencies]) -> dict[str, ToolsetTool[AiAgentDependencies]]:
        direct = await self.wrapped.get_tools(ctx)
        if not direct:
            return {}
        python = await super().get_tools(ctx)
        if "search_tools" in python and "search_tools" in direct and self.tool_selector != "all":
            base_desc = direct["search_tools"].tool_def.description or ""
            python["search_tools"] = replace(
                python["search_tools"],
                tool_def=replace(
                    python["search_tools"].tool_def,
                    description=f"{base_desc}{_DIRECT_AND_PYTHON_SEARCH_NOTE}",
                ),
            )
        return {**direct, **python}


class DirectAndPythonCodeMode(CodeMode[AiAgentDependencies]):
    """当前官方 CodeMode 没有保留同名直接工具的选项，用公开扩展点补充。"""

    def get_wrapper_toolset(self, toolset: AbstractToolset[AiAgentDependencies]) -> AbstractToolset[AiAgentDependencies]:
        return DirectAndPythonToolset(
            wrapped=toolset, tool_selector=self.tools, max_retries=self.max_retries,
            max_tool_calls=self.max_tool_calls, resource_limits=self.resource_limits, capability=self,
            dynamic_catalog=self.dynamic_catalog,
        )

    def _announce_newly_discovered(self, ctx: RunContext[AiAgentDependencies], names: Sequence[str]) -> None:
        # Harness 0.34.0 在 dynamic_catalog=True 且 tools 为子集时，会把非沙箱工具（如审批、
        # 后台长任务、direct_only）一并广播为“可在 run_code 内调用”，误导模型在脚本里反复重试。
        # 按 self.tools 拆分公告；上游按 tool_selector 过滤后再移除此覆写。
        if not isinstance(self.tools, Sequence) or isinstance(self.tools, str):
            super()._announce_newly_discovered(ctx, names)
            return
        fresh = [name for name in names if name not in self._announced_tools]
        if not fresh:
            return
        allowed = set(self.tools)
        sandbox_fresh = [name for name in fresh if name in allowed]
        direct_only_fresh = [name for name in fresh if name not in allowed]
        if sandbox_fresh:
            super()._announce_newly_discovered(ctx, sandbox_fresh)
        if direct_only_fresh:
            self._announced_tools.update(direct_only_fresh)
            listing = ", ".join(f"`{name}`" for name in direct_only_fresh)
            ctx.enqueue(SystemPromptPart(content=f"{_DIRECT_ONLY_DISCOVERY_PREFIX}: {listing}."))


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


_PROGRESS_THROTTLE_SECONDS = 0.15


@dataclass(kw_only=True)
class CodeProgressEvent(CapabilityEvent, namespace="code_progress"):
    """run_code 内嵌套业务工具的实时展示进度；不进入模型上下文或持久化历史。"""

    parent_tool_call_id: str
    nested_tool_name: str
    started: int
    completed: int
    failed: int
    active: int
    tools: list[dict[str, Any]]
    fatal_errors: list[dict[str, Any]] = field(default_factory=list)


def _result_failed(value: Any) -> bool:
    payload = value.return_value if isinstance(value, ToolReturn) else value
    return isinstance(payload, dict) and payload.get("ok") is False


def _extract_fatal_model_error(value: Any, tool_name: str) -> dict[str, Any] | None:
    payload = value.return_value if isinstance(value, ToolReturn) else value
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = str(error.get("code") or "").strip()
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    raw_status = details.get("status_code")
    status_code = raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    if code not in AI_FATAL_MODEL_ERROR_CODES and status_code not in AI_FATAL_MODEL_HTTP_STATUSES:
        return None
    use_case_id = str(details.get("use_case_id") or "").strip()
    use_case_label = str(details.get("use_case_label") or "").strip()
    model_id = str(details.get("model_id") or "").strip()
    model_name = str(details.get("model_name") or "").strip()
    return {
        "code": code or f"HTTP_{status_code}",
        "message": str(error.get("message") or "").strip(),
        "tool_name": str(tool_name or "").strip(),
        **({"status_code": status_code} if status_code is not None else {}),
        **({"use_case_id": use_case_id} if use_case_id else {}),
        **({"use_case_label": use_case_label} if use_case_label else {}),
        **({"model_id": model_id} if model_id else {}),
        **({"model_name": model_name} if model_name else {}),
    }


@dataclass
class PythonToolBudget(AbstractCapability[AiAgentDependencies]):
    """嵌套调用也检查原生 UsageLimits；只补记尚未完成的并发调用，并透出子调用实时进度。"""

    _in_flight: int = 0
    _progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    _last_emit: dict[str, float] = field(default_factory=dict)

    async def for_run(self, ctx: RunContext[AiAgentDependencies]) -> "PythonToolBudget":
        return replace(self, _in_flight=0, _progress={}, _last_emit={})

    async def _emit_progress(
        self,
        ctx: RunContext[AiAgentDependencies],
        parent_id: str,
        nested_tool_name: str,
        *,
        force: bool = False,
    ) -> None:
        state = self._progress.get(parent_id)
        if not state:
            return
        now = time.monotonic()
        if not force and now - self._last_emit.get(parent_id, 0.0) < _PROGRESS_THROTTLE_SECONDS:
            return
        self._last_emit[parent_id] = now
        tools_snapshot = [
            {"name": name, **state["by_tool"][name]}
            for name in state["tool_order"]
        ]
        fatal_snapshot = [dict(item) for item in state["fatal_errors"].values()]
        try:
            await ctx.emit(
                CodeProgressEvent(
                    parent_tool_call_id=parent_id,
                    nested_tool_name=nested_tool_name,
                    started=state["started"],
                    completed=state["completed"],
                    failed=state["failed"],
                    active=state["active"],
                    tools=tools_snapshot,
                    fatal_errors=fatal_snapshot,
                )
            )
        except UserError:
            # 非流式直接调用没有事件缓冲区时静默跳过，不影响工具执行。
            pass

    async def wrap_tool_execute(
        self, ctx: RunContext[AiAgentDependencies], *, call: ToolCallPart,
        tool_def: ToolDefinition, args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        # Core 在模型响应边界检查额度，CodeMode 的脚本内部调用没有该边界。
        # 继续使用原生已完成计数和 UsageLimits，不另存预算或执行计划。
        call_id = str(call.tool_call_id or "")
        is_nested = "__" in call_id and call.tool_name != "run_code"
        try:
            ctx.usage_limits.check_before_tool_call(replace(
                ctx.usage, tool_calls=ctx.usage.tool_calls + self._in_flight + 1,
            ))
        except UsageLimitExceeded as exc:
            if not is_nested:
                raise
            # 嵌套调用在 asyncio.gather 内直接抛异常回传 Monty 0.0.23 会触发 Rust 调度器卡死
            # 并丢失同批已完成写回执；改为返回结构化超额错误，让已完成任务正常收敛。
            limit = ctx.usage_limits.tool_calls_limit
            return ToolReturn(
                return_value={
                    "ok": False,
                    "error": {
                        "code": "TOOL_BUDGET_EXHAUSTED",
                        "message": (
                            f"本轮工具调用额度已达上限（{limit} 次），停止发起新的工具调用；"
                            "请保留并汇总已完成回执后结束本轮。"
                        )
                        if limit is not None
                        else str(exc),
                        "retryable": False,
                    },
                }
            )
        self._in_flight += 1
        parent_id = call_id.rsplit("__", 1)[0] if is_nested else ""
        if is_nested:
            state = self._progress.setdefault(
                parent_id,
                {
                    "started": 0,
                    "completed": 0,
                    "failed": 0,
                    "active": 0,
                    "tool_order": [],
                    "by_tool": {},
                    "fatal_errors": {},
                },
            )
            is_new_tool = call.tool_name not in state["by_tool"]
            if is_new_tool:
                state["tool_order"].append(call.tool_name)
                state["by_tool"][call.tool_name] = {"started": 0, "completed": 0, "failed": 0, "active": 0}
            state["started"] += 1
            state["active"] += 1
            entry = state["by_tool"][call.tool_name]
            entry["started"] += 1
            entry["active"] += 1
            await self._emit_progress(
                ctx,
                parent_id,
                call.tool_name,
                force=state["started"] == 1 or is_new_tool,
            )
        failed = False
        fatal_error: dict[str, Any] | None = None
        try:
            result = await handler(args)
            failed = _result_failed(result)
            if is_nested and failed:
                fatal_error = _extract_fatal_model_error(result, call.tool_name)
            return result
        except BaseException:
            failed = True
            raise
        finally:
            self._in_flight -= 1
            if is_nested:
                state = self._progress.get(parent_id)
                if state is not None:
                    state["active"] = max(0, state["active"] - 1)
                    state["completed"] += 1
                    if failed:
                        state["failed"] += 1
                    is_new_fatal = False
                    if fatal_error is not None:
                        dedupe_key = "|".join(
                            (
                                fatal_error.get("code", ""),
                                fatal_error.get("use_case_id", ""),
                                fatal_error.get("model_id") or fatal_error.get("model_name") or call.tool_name,
                            )
                        )
                        existing = state["fatal_errors"].get(dedupe_key)
                        if existing is None:
                            is_new_fatal = True
                            if len(state["fatal_errors"]) < 5:
                                state["fatal_errors"][dedupe_key] = {**fatal_error, "count": 1}
                        else:
                            existing["count"] = int(existing.get("count") or 1) + 1
                    entry = state["by_tool"].get(call.tool_name)
                    if entry is not None:
                        entry["active"] = max(0, entry["active"] - 1)
                        entry["completed"] += 1
                        if failed:
                            entry["failed"] += 1
                    await self._emit_progress(
                        ctx,
                        parent_id,
                        call.tool_name,
                        force=state["active"] == 0 or failed or is_new_fatal,
                    )
            elif call.tool_name == "run_code" and call_id:
                self._progress.pop(call_id, None)
                self._last_emit.pop(call_id, None)


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
