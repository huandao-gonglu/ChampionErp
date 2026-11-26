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
            return (
                "本轮剩余时间不足 2 分钟，请围绕当前用户问题给出已有依据支持的答案，"
                "不要再开始耗时业务工具。执行任务须说明实际完成范围、失败和未完成项；"
                "查询或解释不附加无关的写入汇报。"
            )
    if remaining == 0:
        return (
            f"本轮工具调用额度已耗尽（上限 {limit} 次），所有业务工具现已关闭，"
            "请仅基于已有信息提交最终输出。"
        )
    low_threshold = min(15, max(5, limit // 5)) if limit > 5 else 0
    if low_threshold and remaining <= low_threshold:
        return (
            f"本轮工具调用额度临近上限（总上限 {limit} 次，剩余不足 {low_threshold + 1} 次）；"
            "同一响应及 run_code 内每次业务函数调用分别计数。"
            "请避免重复读取，尽快完成必要操作并提交最终输出。"
        )
    return (
        f"本轮工具调用上限 {limit} 次（同一响应及 run_code 内每次业务函数分别计数）。"
        "额度是上限非目标；已有足够信息即提交最终输出。"
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
