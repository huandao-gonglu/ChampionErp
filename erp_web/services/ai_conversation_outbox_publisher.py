"""官方编码事件 outbox 的可靠后台投递器。

首次握手与 continuation 在组合事务提交后会立即向事件总线广播一次批次；如果
进程在提交成功后、广播前退出，活动订阅者就会漏掉该批次。publisher 定期扫描
``published_at`` 为空的已提交 outbox 记录并重投，然后记录投递；订阅端按单调
递增的 ``history_version`` 去重，重复投递不会造成重复展示。新订阅者接入时仍
由 SSE 端点从 outbox 重放，两条通道互为兜底。
"""

from __future__ import annotations

import logging

from erp_web.services.ai_conversation_event_bus import AiConversationEventBus
from erp_web.stores.pydantic_ai_event_outbox_store import (
    OUTBOX_PUBLISHED_RETENTION_PER_CONVERSATION,
    PydanticAiEventOutboxStore,
)


_logger = logging.getLogger(__name__)


class AiConversationOutboxPublisher:
    """扫描未投递 outbox 批次并重投到 conversation 事件总线。"""

    def __init__(
        self,
        *,
        event_outbox: PydanticAiEventOutboxStore,
        event_bus: AiConversationEventBus,
    ) -> None:
        self.event_outbox = event_outbox
        self.event_bus = event_bus

    def publish_pending(self, *, limit: int = 200) -> int:
        """重投全部未发布批次并记账；返回本轮重投数量。"""

        batches = self.event_outbox.list_unpublished(limit=limit)
        if not batches:
            return 0
        for batch in batches:
            try:
                self.event_bus.publish(batch.conversation_id, batch)
            except Exception:
                _logger.exception(
                    "outbox 批次重投失败：outbox_id=%s conversation=%s",
                    batch.outbox_id,
                    batch.conversation_id,
                )
                # 单批失败不阻塞后续批次；未记账的批次下一轮继续重投。
                continue
            self.event_outbox.mark_published([batch])
        return len(batches)

    def prune_published(self) -> int:
        """报告 A-14：清理超出保留窗口的已发布批次；返回删除数量。

        与 ``publish_pending`` 同轮执行：先重投未发布批次，再按保留窗口清理
        已发布批次，约束 outbox 无限增长。未发布批次不受影响。
        """

        return self.event_outbox.prune_published(
            keep_latest=OUTBOX_PUBLISHED_RETENTION_PER_CONVERSATION
        )


__all__ = ["AiConversationOutboxPublisher"]
