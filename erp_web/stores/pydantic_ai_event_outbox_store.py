"""官方编码 Agent 事件的发布 outbox。

outbox 只保存由官方 ``VercelAIEventStream`` 编码的有界事件批次以及
``conversation_id``、``history_version``、``run_id`` 等投递键，不定义项目
自有 Assistant 消息或 Agent event shape。记录只能在与 history/link 同事务
提交后写入（见 ``PydanticDeferredTaskLinkStore`` 的组合提交），发布器读取
已提交记录并按 ``after_history_version`` 重放；前端按投递键去重，以
``/ui-messages`` 的已提交历史为最终事实源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from erp_web.db import ErpDatabase


# 报告 A-14：每个 conversation 保留的已发布批次上限。订阅端在游标早于最早
# 保留版本时由 SSE 端回 resync_required 并重读 /ui-messages，不依赖旧批次。
OUTBOX_PUBLISHED_RETENTION_PER_CONVERSATION = 50


class PydanticEventOutboxError(RuntimeError):
    """outbox 记录无法按服务端契约读写。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "PYDANTIC_EVENT_OUTBOX_ERROR")
        super().__init__(message)


@dataclass(frozen=True)
class OutboxEventBatch:
    """一次原子提交产生的官方编码事件批次。"""

    outbox_id: int
    conversation_id: str
    run_id: str
    history_version: int
    kind: str
    events: tuple[str, ...] = field(repr=False)
    created_at: str
    published_at: str


def _batch_from_row(row: Mapping[str, Any]) -> OutboxEventBatch:
    try:
        parsed = json.loads(str(row["events_json"] or "[]"))
        events = tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
        return OutboxEventBatch(
            outbox_id=int(row["outbox_id"] or 0),
            conversation_id=str(row["conversation_id"] or ""),
            run_id=str(row["run_id"] or ""),
            history_version=int(row["history_version"] or 0),
            kind=str(row["kind"] or ""),
            events=events,
            created_at=str(row["created_at"] or ""),
            published_at=str(row["published_at"] or ""),
        )
    except Exception:
        raise PydanticEventOutboxError(
            "PYDANTIC_EVENT_OUTBOX_CORRUPT",
            "已保存的官方事件 outbox 记录损坏或格式无效。",
        ) from None


class PydanticAiEventOutboxStore:
    """已提交 outbox 记录的只读重放与投递记账。"""

    def __init__(self, db: ErpDatabase) -> None:
        self.db = db

    def list_after(
        self,
        conversation_id: str,
        *,
        after_history_version: int,
        limit: int = 200,
    ) -> list[OutboxEventBatch]:
        """重放该 history version 之后的保留批次（按版本升序）。"""

        normalized_id = str(conversation_id or "").strip()
        if not normalized_id:
            raise PydanticEventOutboxError(
                "PYDANTIC_EVENT_OUTBOX_CONVERSATION_INVALID",
                "conversation_id 不能为空。",
            )
        rows = self.db.list_outbox_events_after(
            normalized_id,
            after_history_version=max(0, int(after_history_version)),
            limit=limit,
        )
        return [_batch_from_row(row) for row in rows]

    def latest_history_version(self, conversation_id: str) -> int:
        return self.db.latest_outbox_history_version(
            str(conversation_id or "").strip()
        )

    def list_unpublished(self, *, limit: int = 200) -> list[OutboxEventBatch]:
        """按提交顺序列出尚未投递的批次；供后台 publisher 崩溃重投。"""

        rows = self.db.list_unpublished_outbox_events(limit=limit)
        return [_batch_from_row(row) for row in rows]

    def mark_published(self, batches: Sequence[OutboxEventBatch]) -> None:
        identifiers = [int(batch.outbox_id) for batch in batches]
        self.db.mark_outbox_published(identifiers)

    def prune_published(
        self,
        *,
        keep_latest: int = OUTBOX_PUBLISHED_RETENTION_PER_CONVERSATION,
    ) -> int:
        """报告 A-14：清理超出保留窗口的已发布批次；返回删除数量。

        只删除已发布记录，未发布批次保留给后台重投；保留窗口让「游标早于
        最早保留版本则 resync」形成真实可测的边界。
        """

        return self.db.prune_published_outbox_events(keep_latest=keep_latest)


__all__ = [
    "OUTBOX_PUBLISHED_RETENTION_PER_CONVERSATION",
    "OutboxEventBatch",
    "PydanticAiEventOutboxStore",
    "PydanticEventOutboxError",
]
