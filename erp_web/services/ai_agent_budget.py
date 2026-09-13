"""工具执行额度及安全诊断；参数拒绝与消息闭合均交给 Pydantic AI 原生重试。"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import UsageLimitExceeded

from .ai_agent_dependencies import AiAgentDependencies


def agent_budget_instructions(ctx: RunContext[AiAgentDependencies]) -> str:
    limit = ctx.deps.tool_runtime.max_tool_calls
    remaining = max(0, limit - ctx.usage.tool_calls)
    support = ctx.deps.tool_runtime.run_support
    if support is not None:
        seconds = ctx.deps.execution_context.remaining_seconds()
        if seconds < 120:
            return f"本轮剩余 {seconds:.0f} 秒，请立即汇总各目标已写入内容、失败和未完成项，不要再开始耗时业务工具。"
    return (
        f"当前工具调用额度：已执行 {ctx.usage.tool_calls} 次，剩余 {remaining} 次；"
        "同一响应中的每次工具调用分别计数；批量工具的一次调用只计一次。"
        "额度是上限，不是目标；已有足够信息就提交最终输出。"
        + ("所有业务工具现已关闭，只能提交最终输出。" if remaining == 0 else "")
    )


def agent_budget_error(
    exc: Exception,
    *,
    tool_call_limit: int | None = None,
    tool_calls_used: int = 0,
    model_requests_used: int = 0,
) -> tuple[str, dict[str, Any]] | None:
    """只读取原生预算异常及其原因链，不把 Provider 限流误报为本地额度。"""
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, UsageLimitExceeded):
            raw = str(cause)
            match = re.search(
                r"(request|tool_calls|input_tokens|output_tokens|total_tokens)_limit of (\d+)",
                raw,
            )
            resource = match[1] if match else "usage"
            details: dict[str, Any] = {"origin": "local", "resource": resource}
            if match:
                details["limit"] = int(match[2])
            current = re.search(
                r"\((?:tool_calls|requests|input_tokens|output_tokens|total_tokens)=(\d+)\)",
                raw,
            )
            if current:
                details["projected"] = int(current[1])
            details["stage"] = (
                "before_model_request" if resource == "request" else "usage_check"
            )
            if resource == "request":
                details["used"] = model_requests_used
            elif resource == "tool_calls":
                details["stage"] = "before_tool_execution"
                details["used"] = tool_calls_used
                if tool_call_limit is not None:
                    details["native_limit"] = details.get("limit")
                    details["limit"] = tool_call_limit
            label = {"request": "模型请求", "tool_calls": "工具调用"}.get(
                resource, "资源使用"
            )
            return (
                f"本地 {label}额度耗尽（上限 {details.get('limit', '未知')}），本次任务尚未完成。",
                details,
            )
        cause = cause.__cause__ or cause.__context__
    return None


__all__ = ["agent_budget_error", "agent_budget_instructions"]
