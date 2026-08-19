"""Pydantic ``ModelMessage`` 历史的唯一持久化边界。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
)

from erp_web.db import ErpDatabase


_logger = logging.getLogger(__name__)


INTERRUPTED_TOOL_RETURN_CONTENT = (
    "The tool call was interrupted before a result was produced."
)
SYNTHESIZED_TOOL_RETURN_METADATA_KEY = "pydantic_ai_synthesized_tool_return"


def repair_orphaned_tool_returns(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    """补齐缺失的工具返回，保证历史对模型回放始终合法。

    当一次 run 被新用户消息打断时，底层框架可能只持久化了部分工具返回：
    已经发出的 ``ToolCallPart`` 缺少对应 ``ToolReturnPart``。绝大多数模型
    协议要求每个 ``tool_call_id`` 必须有匹配的工具结果，否则回放会直接报错。

    本函数是幂等的纯修复：扫描全部消息，找出没有任何返回对应的工具调用，
    在紧随其后的 ``ModelResponse`` 之后插入一条标记为 ``interrupted`` 的
    合成 ``ToolReturnPart``。不改动任何已有消息，也不产生写库副作用。
    遇到无法解释的结构时原样返回输入，绝不让修复本身破坏读写路径。
    """

    try:
        return _repair_orphaned_tool_returns(messages)
    except Exception:
        _logger.warning(
            "对话历史工具返回修复失败，返回原始历史。",
            exc_info=True,
        )
        return list(messages)


def _repair_orphaned_tool_returns(
    messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    message_list = list(messages)
    returned_ids: set[str] = set()
    for message in message_list:
        for part in getattr(message, "parts", None) or ():
            if isinstance(part, BaseToolReturnPart):
                call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                if call_id:
                    returned_ids.add(call_id)

    repaired: list[ModelMessage] = []
    synthesized_count = 0
    for message in message_list:
        repaired.append(message)
        if not isinstance(message, ModelResponse):
            continue
        # 用来源响应自身的时间戳，保证同一份历史每次读取得到完全一致的
        # 修复结果（确定性），而不是随读取时刻漂移。
        source_timestamp = message.timestamp or datetime.now(timezone.utc)
        orphan_returns: list[ToolReturnPart] = []
        for part in message.parts:
            if not isinstance(part, BaseToolCallPart):
                continue
            call_id = str(getattr(part, "tool_call_id", "") or "").strip()
            if not call_id or call_id in returned_ids:
                continue
            orphan_returns.append(
                ToolReturnPart(
                    tool_name=str(getattr(part, "tool_name", "") or ""),
                    content=INTERRUPTED_TOOL_RETURN_CONTENT,
                    tool_call_id=call_id,
                    tool_kind=getattr(part, "tool_kind", None),
                    metadata={SYNTHESIZED_TOOL_RETURN_METADATA_KEY: True},
                    timestamp=source_timestamp,
                    outcome="interrupted",
                )
            )
            returned_ids.add(call_id)
        if orphan_returns:
            synthesized_count += len(orphan_returns)
            repaired.append(ModelRequest(parts=orphan_returns))

    if synthesized_count:
        _logger.info(
            "补齐了 %d 条缺失的工具返回，保证对话历史可安全回放。",
            synthesized_count,
        )
    return repaired


class PydanticMessageStoreError(RuntimeError):
    """消息历史无法按 Pydantic 官方契约读写。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "PYDANTIC_MESSAGE_STORE_ERROR")
        super().__init__(message)


@dataclass(frozen=True)
class PydanticConversationSummary:
    """对话列表所需的最小 SQLite 索引信息。"""

    conversation_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PydanticMessageHistory:
    """一份经过官方 adapter 验证的原生消息 JSON。"""

    conversation_id: str
    messages_json: bytes
    created_at: str
    updated_at: str

    def model_messages(self) -> list[ModelMessage]:
        """按官方类型恢复消息，不创建项目自有 message shape。

        读取后立即补齐缺失的工具返回，保证交给模型回放的历史始终合法
        （每个 tool_call 都有对应的 tool return）。
        """

        messages = list(ModelMessagesTypeAdapter.validate_json(self.messages_json))
        return repair_orphaned_tool_returns(messages)


def _required_conversation_id(value: Any) -> str:
    conversation_id = str(value or "").strip()
    if not conversation_id:
        raise PydanticMessageStoreError(
            "PYDANTIC_MESSAGE_CONVERSATION_ID_INVALID",
            "conversation_id 不能为空。",
        )
    return conversation_id


def _canonical_messages_json(value: Any, *, stored: bool) -> bytes:
    try:
        if stored:
            if isinstance(value, memoryview):
                value = value.tobytes()
            if not isinstance(value, (bytes, bytearray)):
                raise TypeError("messages_json 必须是 SQLite BLOB")
            messages = ModelMessagesTypeAdapter.validate_json(value)
        else:
            messages = ModelMessagesTypeAdapter.validate_python(list(value))
        return ModelMessagesTypeAdapter.dump_json(messages)
    except Exception:
        code = (
            "PYDANTIC_MESSAGE_HISTORY_CORRUPT"
            if stored
            else "PYDANTIC_MESSAGE_HISTORY_INVALID"
        )
        message = (
            "已保存的 Pydantic 消息历史损坏或格式无效。"
            if stored
            else "消息历史不符合 Pydantic ModelMessage 契约。"
        )
        raise PydanticMessageStoreError(code, message) from None


def _history_from_row(row: dict[str, Any]) -> PydanticMessageHistory:
    try:
        conversation_id = _required_conversation_id(row["conversation_id"])
        created_at = str(row["created_at"] or "")
        updated_at = str(row["updated_at"] or "")
        messages_json = _canonical_messages_json(
            row["messages_json"],
            stored=True,
        )
    except PydanticMessageStoreError:
        raise
    except Exception:
        raise PydanticMessageStoreError(
            "PYDANTIC_MESSAGE_HISTORY_CORRUPT",
            "已保存的 Pydantic 消息历史损坏或格式无效。",
        ) from None
    return PydanticMessageHistory(
        conversation_id=conversation_id,
        messages_json=messages_json,
        created_at=created_at,
        updated_at=updated_at,
    )


class PydanticMessageStore:
    """原子保存并验证 Pydantic 官方消息 JSON。"""

    def __init__(self, db: ErpDatabase) -> None:
        self.db = db

    def save(
        self,
        conversation_id: str,
        messages: Sequence[ModelMessage],
    ) -> PydanticMessageHistory:
        """以完整消息列表原子替换同一 conversation 的历史。"""

        normalized_id = _required_conversation_id(conversation_id)
        messages_json = _canonical_messages_json(messages, stored=False)
        row = self.db.replace_pydantic_message_history(
            normalized_id,
            messages_json,
            now=datetime.now(timezone.utc).isoformat(),
        )
        return _history_from_row(row)

    def get(self, conversation_id: str) -> PydanticMessageHistory | None:
        """读取并验证一份消息历史。"""

        normalized_id = _required_conversation_id(conversation_id)
        row = self.db.get_pydantic_message_history(normalized_id)
        return _history_from_row(row) if row is not None else None

    def list(self, *, limit: int = 100) -> list[PydanticConversationSummary]:
        """按更新时间列出最小对话索引，不读取或解释消息内容。"""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise PydanticMessageStoreError(
                "PYDANTIC_MESSAGE_HISTORY_LIMIT_INVALID",
                "消息历史列表 limit 必须是 1 到 1000 之间的整数。",
            )
        rows = self.db.list_pydantic_message_histories(limit=limit)
        summaries: list[PydanticConversationSummary] = []
        try:
            for row in rows:
                summaries.append(
                    PydanticConversationSummary(
                        conversation_id=_required_conversation_id(
                            row["conversation_id"]
                        ),
                        created_at=str(row["created_at"] or ""),
                        updated_at=str(row["updated_at"] or ""),
                    )
                )
        except PydanticMessageStoreError:
            raise
        except Exception:
            raise PydanticMessageStoreError(
                "PYDANTIC_MESSAGE_HISTORY_CORRUPT",
                "已保存的 Pydantic 消息索引损坏或格式无效。",
            ) from None
        return summaries

    def delete(self, conversation_id: str) -> bool:
        """删除指定 conversation 的消息历史。"""

        return self.db.delete_pydantic_message_history(
            _required_conversation_id(conversation_id)
        )


__all__ = [
    "INTERRUPTED_TOOL_RETURN_CONTENT",
    "PydanticConversationSummary",
    "PydanticMessageHistory",
    "PydanticMessageStore",
    "PydanticMessageStoreError",
    "SYNTHESIZED_TOOL_RETURN_METADATA_KEY",
    "repair_orphaned_tool_returns",
]
