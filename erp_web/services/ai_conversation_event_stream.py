"""活动 conversation 的后台官方事件订阅通道（SSE）。

协议约束：
- 只传输已提交 outbox 中的官方编码事件批次与投递键（history_version/run_id/
  kind），不定义项目自有 Assistant 消息或事件 shape；
- 重连先读取快照版本，再以 ``after_history_version`` 订阅：服务端先从 outbox
  重放该版本之后的保留批次，再转 live，保证“快照与订阅之间提交”的事件不丢；
- 当前 history version 超出已重放范围（outbox 未覆盖）时，明确回
  ``resync_required``，由客户端重新读取 ``/ui-messages`` 后再订阅，绝不静默
  从 live 位置开始；
- 订阅端断开只移除订阅，不影响 Agent run 与 history 提交。

前端按单调递增的 history version 应用批次，并以 ``/ui-messages`` 的已提交
历史为最终事实源。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from erp_web.services.ai_conversation_event_bus import (
    AiConversationEventBus,
    ConversationResyncRequired,
)
from erp_web.stores.pydantic_ai_event_outbox_store import (
    OutboxEventBatch,
    PydanticAiEventOutboxStore,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStore


_logger = logging.getLogger(__name__)

# 无批次时下发的 SSE 保活注释间隔（秒），防止中间层关闭空闲连接。
_KEEPALIVE_INTERVAL_SECONDS = 15.0


class ConversationEventStream:
    """单个 conversation 的 SSE 订阅 run；路由只负责写出。"""

    def __init__(
        self,
        *,
        conversation_id: str,
        after_history_version: int,
        message_store: PydanticMessageStore,
        event_outbox: PydanticAiEventOutboxStore,
        event_bus: AiConversationEventBus,
    ) -> None:
        self.conversation_id = str(conversation_id or "").strip()
        self.after_history_version = max(0, int(after_history_version))
        self.message_store = message_store
        self.event_outbox = event_outbox
        self.event_bus = event_bus

    def sse_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "close",
            "X-Accel-Buffering": "no",
        }

    @staticmethod
    def _batch_event(batch: OutboxEventBatch) -> str:
        payload = {
            "type": "batch",
            "history_version": batch.history_version,
            "run_id": batch.run_id,
            "kind": batch.kind,
            "events": list(batch.events),
        }
        return (
            "data: "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n\n"
        )

    @staticmethod
    def _resync_event(history_version: int) -> str:
        payload = {
            "type": "resync_required",
            "history_version": history_version,
        }
        return (
            "data: "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n\n"
        )

    async def stream(self, write_chunk: Callable[[bytes], None]) -> None:
        """重放 + live；客户端断线只退出当前订阅。"""

        subscription = self.event_bus.subscribe(self.conversation_id)
        replayed_max = self.after_history_version
        try:
            # 先注册订阅再重放，保证重放与 live 之间不丢事件。
            current_version = self.message_store.get_version(
                self.conversation_id
            )
            replay_batches = self.event_outbox.list_after(
                self.conversation_id,
                after_history_version=self.after_history_version,
            )
            # 报告 A-14：retention 清理后，请求 cursor 可能落在最早保留批次
            # 之前，中间版本已不存在。重放前识别 cursor+1 与最早保留批次的
            # 缺口：有缺口时直接回 resync_required，绝不把不连续批次伪装成
            # 完整重放（否则客户端会永久漏掉缺口版本）。
            if (
                replay_batches
                and replay_batches[0].history_version
                > self.after_history_version + 1
            ):
                write_chunk(
                    self._resync_event(current_version).encode("utf-8")
                )
                return
            for batch in replay_batches:
                write_chunk(self._batch_event(batch).encode("utf-8"))
                replayed_max = max(replayed_max, batch.history_version)

            # 快照版本超出已重放范围：outbox 未能覆盖，要求客户端重新同步。
            if current_version > replayed_max:
                write_chunk(
                    self._resync_event(current_version).encode("utf-8")
                )
                return

            while True:
                try:
                    batch = await asyncio.to_thread(
                        subscription.poll,
                        _KEEPALIVE_INTERVAL_SECONDS,
                    )
                except ConversationResyncRequired:
                    # 报告 A-17：订阅缓冲溢出——明确回 resync_required，客户端
                    # 以已应用版本为游标重连，outbox 重放补齐缺口；绝不把
                    # 丢失的批次静默跳过。
                    write_chunk(
                        self._resync_event(replayed_max).encode("utf-8")
                    )
                    return
                if batch is None:
                    # 保活注释帧；不携带数据，客户端忽略。
                    write_chunk(b": keepalive\n\n")
                    continue
                if batch.history_version <= replayed_max:
                    continue
                write_chunk(self._batch_event(batch).encode("utf-8"))
                replayed_max = batch.history_version
        finally:
            self.event_bus.unsubscribe(subscription)


__all__ = ["ConversationEventStream"]
