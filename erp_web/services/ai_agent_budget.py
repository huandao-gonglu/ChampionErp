"""工具执行额度及安全诊断；参数拒绝与消息闭合均交给 Pydantic AI 原生重试。"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded

from .ai_agent_dependencies import AiAgentDependencies


class AgentToolBudgetRetry(ModelRetry):
    """携带安全诊断数据；纠正次数仍由原生工具重试额度限制。"""

    def __init__(self, *, limit: int, used: int, requested: int) -> None:
        self.details = {
            "origin": "local", "resource": "tool_calls", "limit": limit,
            "used": used, "requested": requested, "stage": "before_tool_execution",
        }
        remaining = max(0, limit - used)
        action = (
            "工具额度已用完。请依据已有结果提交最终输出；无法确定时如实说明未完成。"
            if remaining == 0 else f"请将本轮工具调用减少到 {remaining} 次以内，或提交最终输出。"
        )
        super().__init__(
            f"本地工具调用上限 {limit} 次，已执行 {used} 次，本轮请求 {requested} 次。"
            f"本次工具尚未执行。{action}"
        )

def agent_budget_instructions(ctx: RunContext[AiAgentDependencies]) -> str:
    limit = ctx.deps.tool_runtime.max_tool_calls
    remaining = max(0, limit - ctx.usage.tool_calls)
    if remaining > 1:
        return "已有足够信息就提交最终输出，不需要耗尽工具额度。"
    return (
        f"当前工具调用额度：已执行 {ctx.usage.tool_calls} 次，剩余 {remaining} 次；"
        "同一响应中的每次工具调用分别计数；批量工具的一次调用只计一次。"
        "额度是上限，不是目标；已有足够信息就提交最终输出。"
        + ("所有业务工具现已关闭，只能提交最终输出。" if remaining == 0 else "")
    )


def agent_budget_error(
    exc: Exception, *, tool_call_limit: int | None = None, tool_calls_used: int = 0,
    model_requests_used: int = 0,
) -> tuple[str, dict[str, Any]] | None:
    """只读取原生预算异常及其原因链，不把 Provider 限流误报为本地额度。"""
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if (
            isinstance(cause, ModelRetry) and "Unknown tool name" in str(cause)
            and tool_call_limit is not None and tool_calls_used >= tool_call_limit
        ):
            cause = AgentToolBudgetRetry(limit=tool_call_limit, used=tool_calls_used, requested=1)
        if isinstance(cause, AgentToolBudgetRetry):
            details = dict(cause.details)
            return (
                f"本地工具调用额度已达边界（已执行 {details['used']}/{details['limit']} 次，"
                f"又请求 {details['requested']} 次，未执行），模型未能在纠正次数内提交结果。",
                details,
            )
        if isinstance(cause, UsageLimitExceeded):
            raw = str(cause)
            match = re.search(r"(request|tool_calls|input_tokens|output_tokens|total_tokens)_limit of (\d+)", raw)
            resource = match[1] if match else "usage"
            details: dict[str, Any] = {"origin": "local", "resource": resource}
            if match:
                details["limit"] = int(match[2])
            current = re.search(r"\((?:tool_calls|requests|input_tokens|output_tokens|total_tokens)=(\d+)\)", raw)
            if current:
                details["projected"] = int(current[1])
            details["stage"] = "before_model_request" if resource == "request" else "usage_check"
            if resource == "request":
                details["used"] = model_requests_used
            elif resource == "tool_calls":
                details["stage"] = "before_tool_execution"
                details["used"] = tool_calls_used
                if tool_call_limit is not None:
                    details["native_limit"] = details.get("limit")
                    details["limit"] = tool_call_limit
            label = {"request": "模型请求", "tool_calls": "工具调用"}.get(resource, "资源使用")
            return f"本地 {label}额度耗尽（上限 {details.get('limit', '未知')}），本次任务尚未完成。", details
        cause = cause.__cause__ or cause.__context__
    return None


__all__ = ["AgentToolBudgetRetry", "agent_budget_error", "agent_budget_instructions"]
