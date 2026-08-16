"""只读 Pydantic message history API 数据形状。"""

from __future__ import annotations

from typing import Any, TypedDict


class PydanticMessageHistorySummary(TypedDict):
    conversation_id: str
    created_at: str
    updated_at: str


class PydanticMessageHistoryDetail(PydanticMessageHistorySummary):
    """``messages`` 保持 Pydantic 官方 ModelMessage JSON 结构。"""

    messages: list[dict[str, Any]]


class AiWorkUiMessagesDetail(PydanticMessageHistorySummary):
    """``/ui-messages`` 的外层响应；``messages`` 只承接 Adapter 已序列化的 JSON。

    这里不复制第三方 ``UIMessagePart`` 联合类型；展示消息由
    ``VercelAIAdapter.dump_messages(...)`` 官方派生并以 JSON alias 序列化。
    """

    ok: bool
    messages: list[dict[str, Any]]


__all__ = [
    "AiWorkUiMessagesDetail",
    "PydanticMessageHistoryDetail",
    "PydanticMessageHistorySummary",
]
