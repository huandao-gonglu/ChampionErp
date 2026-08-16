"""进程内活动 chat run 互斥表。

仅作为单进程 ``ThreadingHTTPServer`` 下的并发屏障：按 conversation ID 原子
领取和释放活动 run。它不保存消息、claim 或业务状态；durable 归属由
``ai_chat_turn_claims`` 表承担。若未来改成多进程/多 worker，必须升级为数据
库 lease 或 revision/CAS，不能继续声称进程内 registry 足够。
"""

from __future__ import annotations

import threading


class AiChatRunRegistry:
    """按 conversation ID 原子领取/释放活动 run。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, conversation_id: str) -> bool:
        """领取成功返回 True；已有活动 run 时返回 False。"""

        normalized = str(conversation_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            if normalized in self._active:
                return False
            self._active.add(normalized)
            return True

    def release(self, conversation_id: str) -> None:
        """释放活动 run；未领取时是幂等 no-op。"""

        normalized = str(conversation_id or "").strip()
        if not normalized:
            return
        with self._lock:
            self._active.discard(normalized)

    def is_active(self, conversation_id: str) -> bool:
        normalized = str(conversation_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            return normalized in self._active


__all__ = ["AiChatRunRegistry"]
