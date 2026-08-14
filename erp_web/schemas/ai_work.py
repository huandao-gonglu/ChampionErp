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


__all__ = [
    "PydanticMessageHistoryDetail",
    "PydanticMessageHistorySummary",
]
