"""进程内活动 chat run 互斥表。

仅作为单进程 ``ThreadingHTTPServer`` 下的并发屏障：按 conversation ID 原子
领取和释放活动 run。它不保存消息、claim 或业务状态；durable 归属由
``ai_chat_turn_claims`` 表承担。若未来改成多进程/多 worker，必须升级为数据
库 lease 或 revision/CAS，不能继续声称进程内 registry 足够。
"""

from __future__ import annotations

import threading
from pydantic_ai import CancellationToken


class AiChatRunRegistry:
    """按 conversation ID 原子领取/释放活动 run。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}

    def input_guard(self):
        """串行化短暂的输入接收、取消和后台领取；不得跨 Agent 执行持锁。"""
        return self._lock

    def token(self, conversation_id: str) -> CancellationToken:
        with self._lock:
            return self._tokens.setdefault(conversation_id, CancellationToken())

    def new_input(self, conversation_id: str) -> bool:
        """仅新的用户输入可在已取消运行退出后建立新的原生停止范围。"""
        with self._lock:
            if self.token(conversation_id).cancelled:
                if conversation_id in self._active:
                    return False
                self._tokens[conversation_id] = CancellationToken()
            return True

    def acquire(self, conversation_id: str) -> bool:
        """领取成功返回 True；已有活动 run 时返回 False。"""

        normalized = str(conversation_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            if normalized in self._active or self.token(normalized).cancelled:
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
