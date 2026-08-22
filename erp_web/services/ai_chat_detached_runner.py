"""进程级长生命周期 runner：托管 AI chat producer（报告 R-02）。

HTTP 路由为每次请求创建一次性 event loop，请求退出时立即执行
``shutdown_asyncgens()`` 与 ``loop.close()``。若 producer 由请求 loop 托管，
请求协程取消/loop 关闭会连坐取消 producer，history、link、outbox 与 claim
无法形成正确终态。本 runner 持有专用 daemon 线程与 event loop，producer 通过
``run_coroutine_threadsafe`` 提交；请求 loop 的取消与关闭不影响其完成。

runner 只做托管：commit、claim 收尾与注册表释放全部在 producer 协程内完成，
runner 不持有任何业务状态。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Coroutine


class DetachedChatRunner:
    """专用后台 event loop 的进程级 owner。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def submit(
        self,
        coro: Coroutine[Any, Any, None],
    ) -> concurrent.futures.Future:
        """把 producer 协程提交到 runner loop；线程安全，可并发调用。"""

        loop = self._ensure_running()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def _ensure_running(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            loop_alive = (
                self._thread is not None and self._thread.is_alive()
            )
            if (
                self._loop is None
                or self._loop.is_closed()
                or not loop_alive
            ):
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever,
                    name="ai-chat-detached-runner",
                    daemon=True,
                )
                self._thread.start()
            return self._loop

    def shutdown(self, *, timeout_seconds: float = 10.0) -> None:
        """停止 runner loop（进程退出或测试清理用）；幂等。"""

        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        try:
            loop.close()
        except Exception:  # pragma: no cover - 清理路径尽力而为
            pass


_runner = DetachedChatRunner()


def get_detached_chat_runner() -> DetachedChatRunner:
    """进程级单例：所有 AI chat producer 的唯一托管者。"""

    return _runner


def stop_detached_chat_runner(*, timeout_seconds: float = 10.0) -> None:
    """停止进程级 runner（测试清理/优雅停机钩子）。"""

    _runner.shutdown(timeout_seconds=timeout_seconds)


__all__ = [
    "DetachedChatRunner",
    "get_detached_chat_runner",
    "stop_detached_chat_runner",
]
