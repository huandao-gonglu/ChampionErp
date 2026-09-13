"""模型输入历史的最小安全投影（修复计划第 15 节）。

本模块用于 Pydantic AI 官方 ``model_request`` Hook，只处
理 ``RunContext`` 与 ``ModelMessage`` 值：不读数据库、不调模型、不发事件、不执
行工具，也不实现任何 Provider thinking 协议映射——thinking 的回传字段名、签名
与 provider 元数据一律由 Pydantic Provider adapter 负责，本模块不做任何映射。

核心安全门：**只要当前模型请求暴露任意工具，就完整保留全部历史
``ThinkingPart``**。部分 Provider 的思考模式要求请求携带 ``tools`` 时必须完整回
传上一轮的思考内容，遗漏会得到 400；项目不得自行猜测删除。只有当
``RunContext.available_tool_names`` 为空（当前请求不暴露工具）时，才允许从早于
当前 run、已完成的 ``ModelResponse`` 中移除可省略的 ``ThinkingPart``。

投影是纯函数：返回新列表与新的被修改消息对象，绝不原地修改从
``PydanticMessageStore`` 读取的规范对象。
"""

from __future__ import annotations

import dataclasses
import json
from copy import copy
from collections.abc import Awaitable, Callable
from typing import Any, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext


__all__ = ["project_model_context_for_model", "strip_stale_thinking", "project_model_request"]


def _run_id(message: ModelMessage) -> str:
    return str(getattr(message, "run_id", "") or "")


def _returned_tool_call_ids(messages: Sequence[ModelMessage]) -> set[str]:
    returned: set[str] = set()
    for message in messages:
        for part in getattr(message, "parts", ()) or ():
            if isinstance(part, ToolReturnPart):
                returned.add(str(part.tool_call_id))
    return returned


def _has_unmatched_tool_call(
    message: ModelMessage,
    returned_ids: set[str],
) -> bool:
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart) and str(part.tool_call_id) not in returned_ids:
            return True
    return False


def strip_stale_thinking(
    messages: Sequence[ModelMessage],
    *,
    current_run_id: str = "",
) -> list[ModelMessage]:
    """工具不可见时的最小投影：移除旧的已完成 thinking，保护活动尾部。

    保护规则（修复计划 15.4）：
    - 当前 run 的消息原样保留；
    - 含未匹配 ``ToolReturnPart`` 的 ``ToolCallPart``、``state='suspended'``
      消息及其后的连续后缀原样保留（Deferred/tool 尾部）；
    - 仅从更早的、``state='complete'`` 的 ``ModelResponse`` 移除
      ``ThinkingPart``；移除后若响应不再包含任何有意义 part，则删除整个响应，
      不向 Provider 发送空 assistant 消息。
    """

    normalized_current = str(current_run_id or "")
    returned_ids = _returned_tool_call_ids(messages)

    # 计算受保护后缀的起点：取最早出现的“当前 run / suspended / 未闭合工具调用”。
    protected_start = len(messages)
    for index, message in enumerate(messages):
        if _run_id(message) == normalized_current and normalized_current:
            protected_start = index
            break
        state = str(getattr(message, "state", "") or "")
        if state == "suspended":
            protected_start = index
            break
        if _has_unmatched_tool_call(message, returned_ids):
            protected_start = index
            break

    projected: list[ModelMessage] = []
    for index, message in enumerate(messages):
        if index >= protected_start:
            projected.append(message)
            continue
        if not isinstance(message, ModelResponse):
            projected.append(message)
            continue
        state = str(getattr(message, "state", "") or "")
        if state != "complete":
            projected.append(message)
            continue
        parts = list(getattr(message, "parts", ()) or ())
        if not any(isinstance(part, ThinkingPart) for part in parts):
            projected.append(message)
            continue
        remaining = [part for part in parts if not isinstance(part, ThinkingPart)]
        if not remaining:
            # 只剩 thinking 的已完成响应整体删除，避免空 assistant 消息。
            continue
        projected.append(dataclasses.replace(message, parts=remaining))
    return projected


def project_model_context_for_model(
    ctx: RunContext[Any],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """请求投影：工具可见性安全门 + 最小旧 thinking 删除。

    输入输出均为 ``list[ModelMessage]``；Pydantic 在每次模型请求边界调用。
    保持规范历史不变，只供原生模型请求 Hook 使用。
    """

    try:
        available = set(ctx.available_tool_names)
    except Exception:
        available = None
    if available is None or available:
        # 暴露任意工具（或无法可靠确认工具不可见）：安全回退为完整保留。
        return list(messages)
    current_run_id = str(getattr(ctx, "run_id", "") or "")
    return strip_stale_thinking(messages, current_run_id=current_run_id)


def _prior_tool_result_previews(messages: list[ModelMessage]) -> list[ModelMessage]:
    """旧用户回合的大工具结果仅作摘要；本轮结果和所有原生调用配对原样保留。"""
    boundary = max((i for i, m in enumerate(messages)
                    if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)), default=0)
    projected = []
    for index, message in enumerate(messages):
        if index >= boundary or not isinstance(message, ModelRequest):
            projected.append(message)
            continue
        parts = []
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or len(text := part.model_response_str()) <= 4000:
                parts.append(part)
                continue
            content = part.content
            summary = {"history_preview": True,
                       "notice": "这是旧回合结果摘要，不能据此认定任务完成。完整结果保存在原生历史及工具回执；需要当前字段时重新查询业务工具。",
                       "original_characters": len(text)}
            if isinstance(content, dict):
                for key in ("ok", "status", "error", "draft_id", "platform", "target_platform", "site", "category_id", "warning"):
                    if key in content:
                        value = content[key]
                        summary[key] = value if len(json.dumps(value, ensure_ascii=False)) <= 1000 else str(value)[:1000] + "…"
                if isinstance(items := content.get("items"), list):
                    summary["item_count"] = len(items)
                    summary["items_with_error"] = sum(bool(item.get("error")) for item in items if isinstance(item, dict))
            summary["preview"] = text[:1200] + "…"
            parts.append(dataclasses.replace(part, content=summary))
        projected.append(dataclasses.replace(message, parts=parts))
    return projected


async def project_model_request(
    ctx: RunContext[Any], *, request_context: ModelRequestContext,
    handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
) -> ModelResponse:
    """仅向 Provider 发送投影副本；原生 run 历史和持久化仍持有完整消息。

    ProcessHistory 会替换 run 的历史，不能满足完整审计要求。原生请求包裹
    Hook 允许只替换本次发送内容，不创建历史合并、消息恢复或模型调用旁路。
    """
    messages = _prior_tool_result_previews(project_model_context_for_model(ctx, request_context.messages))
    # model_id/streaming 是原生 init=False 字段，dataclasses.replace 会重置它们。
    projected = copy(request_context)
    projected.messages = messages
    return await handler(projected)
