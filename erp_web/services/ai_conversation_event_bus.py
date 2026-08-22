"""conversation 级官方编码事件的进程内发布/订阅总线。

总线只传递已经与 history/link 同事务提交的 outbox 批次（投递键 +
官方编码 chunk），不定义任何项目自有事件 shape；订阅端按单调递增的
history_version 应用并去重。发布可以来自任意线程（聊天请求线程或后台
recovery worker），订阅端在自己的事件循环里通过线程安全队列消费。
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from erp_web.stores.pydantic_ai_event_outbox_store import OutboxEventBatch


# 报告 A-17：单个订阅者的缓冲批次上限。旧实现使用无界队列（maxsize=0），
# 慢 SSE 订阅者不消费时发布会线性占用进程内存。溢出时订阅被标记，下一次
# poll 返回显式 resync 信号，订阅端按已应用版本重连，outbox 重放补齐缺口。
SUBSCRIPTION_QUEUE_MAXSIZE = 256


class ConversationResyncRequired(RuntimeError):
    """订阅缓冲溢出：订阅端必须以已应用版本为游标重新同步。"""


@dataclass(eq=False)
class ConversationEventSubscription:
    """一个订阅者的线程安全有界批次队列；close 幂等。按身份哈希。"""

    conversation_id: str
    _queue: "queue.Queue[OutboxEventBatch | None]" = field(
        default_factory=lambda: queue.Queue(
            maxsize=SUBSCRIPTION_QUEUE_MAXSIZE
        ),
        repr=False,
    )
    _closed: bool = field(default=False, repr=False)
    _overflowed: bool = field(default=False, repr=False)

    def poll(self, timeout: float | None = None) -> OutboxEventBatch | None:
        """阻塞读取下一个批次；超时返回 None；订阅关闭抛出 StopIteration。

        报告 A-17：缓冲溢出过的订阅抛出 ``ConversationResyncRequired``——
        不得让订阅者静默漏掉批次形成 history_version 缺口。已关闭的订阅优
        先走关闭路径（close 时队列已清空并只剩哨兵），保证确定性终止。
        """

        if self._closed:
            try:
                batch = self._queue.get(timeout=timeout)
            except queue.Empty:
                raise StopIteration("conversation 事件订阅已关闭") from None
            if batch is None:
                raise StopIteration("conversation 事件订阅已关闭")
            return batch
        if self._overflowed:
            raise ConversationResyncRequired(
                "conversation 事件订阅缓冲溢出，需要重新同步"
            )
        try:
            batch = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if batch is None:
            raise StopIteration("conversation 事件订阅已关闭")
        if self._overflowed:
            raise ConversationResyncRequired(
                "conversation 事件订阅缓冲溢出，需要重新同步"
            )
        return batch

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 报告 A-17：满队列下 close 不得阻塞——先清空已缓冲批次，再投递关闭
        # 哨兵；订阅端最多消费到哨兵即终止。
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put_nowait(None)


class AiConversationEventBus:
    """按 conversation_id 广播已提交 outbox 批次。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[ConversationEventSubscription]] = {}

    def subscribe(
        self,
        conversation_id: str,
    ) -> ConversationEventSubscription:
        normalized = str(conversation_id or "").strip()
        if not normalized:
            raise ValueError("conversation_id 不能为空")
        subscription = ConversationEventSubscription(
            conversation_id=normalized
        )
        with self._lock:
            self._subscribers.setdefault(normalized, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: ConversationEventSubscription) -> None:
        subscription.close()
        with self._lock:
            subscriptions = self._subscribers.get(subscription.conversation_id)
            if subscriptions is not None:
                subscriptions.discard(subscription)
                if not subscriptions:
                    self._subscribers.pop(subscription.conversation_id, None)

    def publish(
        self,
        conversation_id: str,
        batch: OutboxEventBatch,
    ) -> int:
        """向该 conversation 的全部订阅者投递批次；返回投递数量。

        只在 history/link/outbox 原子提交成功之后调用；订阅缓冲满时不阻塞
        发布方，而是把该订阅标记为溢出——其下一次 poll 抛出显式 resync 信号，
        订阅端以已应用版本为游标重连，由 outbox 重放补齐缺口，绝不静默造成
        history_version 缺口（报告 A-17）。
        """

        normalized = str(conversation_id or "").strip()
        if not normalized:
            return 0
        with self._lock:
            subscriptions = list(self._subscribers.get(normalized, ()))
        delivered = 0
        for subscription in subscriptions:
            if subscription._closed:
                continue
            try:
                subscription._queue.put_nowait(batch)
                delivered += 1
            except queue.Full:
                subscription._overflowed = True
        return delivered


__all__ = [
    "SUBSCRIPTION_QUEUE_MAXSIZE",
    "AiConversationEventBus",
    "ConversationEventSubscription",
    "ConversationResyncRequired",
]
