"""Pydantic ``ModelMessage`` 历史的唯一持久化边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from erp_web.db import ErpDatabase


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
        """按官方类型恢复消息，不创建项目自有 message shape。"""

        return list(ModelMessagesTypeAdapter.validate_json(self.messages_json))


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
    "PydanticConversationSummary",
    "PydanticMessageHistory",
    "PydanticMessageStore",
    "PydanticMessageStoreError",
]
