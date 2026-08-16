"""进程内 presentation registry：reservation/claim、单 lease、官方 chunk 缓冲。

由 ``AppContext`` 单例持有，面向当前单进程 ``ThreadingHTTPServer``
（docs/aiworkpage.md §3/§5）：

- ``reserve``：前端预留一次展示（服务端生成 ID 后写入），短 TTL 内必须被业务
  请求 claim，否则过期；reservation 不执行 Agent、不读取业务数据。
- ``claim``：HTTP 公共边界用 ``X-AI-Presentation-ID`` 原子领取已预留
  presentation；一个 presentation 只能 claim 一次。
- 保存短期、有界的**官方编码 SSE chunk** replay buffer，同时覆盖“stream 早于
  业务绑定”与“业务早于 stream”两种顺序；缓冲不持久化，terminal 后按 TTL 清理。
- 提供唯一的 presentation stream lease；浏览器断连只释放 lease，不影响业务。
- 缓冲溢出必须显式 failed 并关闭，不得静默丢弃产生残缺的官方流。

registry **不保存业务结果**：类目候选、属性 mutation 等类型化结果只由原业务
接口拥有；规范消息仍只由 ``PydanticMessageStore`` 持久化。若未来改成多进程，
必须升级为数据库 lease/CAS 加外部事件通道。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


RESERVED = "reserved"
BOUND = "bound"
RUNNING = "running"
FINALIZING = "finalizing"
COMPLETED = "completed"
FAILED = "failed"
EXPIRED = "expired"

TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, EXPIRED})

BUFFER_OVERFLOW_CODE = "AI_PRESENTATION_BUFFER_OVERFLOW"

__all__ = [
    "BOUND",
    "BUFFER_OVERFLOW_CODE",
    "COMPLETED",
    "EXPIRED",
    "FAILED",
    "FINALIZING",
    "RESERVED",
    "RUNNING",
    "TERMINAL_STATUSES",
    "AiPresentationRegistry",
]


@dataclass
class _PresentationState:
    presentation_id: str
    conversation_id: str
    display_title: str
    status: str = RESERVED
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    terminal_at: str | None = None
    reserved_expires_at: datetime | None = None
    had_agent_run: bool = False
    root_run_id: str = ""
    error_code: str = ""
    error_message: str = ""
    chunks: list[bytes] = field(default_factory=list)
    closed: bool = False
    leased: bool = False


class AiPresentationRegistry:
    """按 ``presentation_id`` 协调业务请求、Agent observer 与展示订阅者。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = 600.0,
        reservation_ttl_seconds: float = 120.0,
        max_buffered_chunks: int = 50_000,
    ) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._presentations: dict[str, _PresentationState] = {}
        self._ttl_seconds = float(ttl_seconds)
        self._reservation_ttl_seconds = float(reservation_ttl_seconds)
        self._max_chunks = int(max_buffered_chunks)

    # -- 预留与 claim ------------------------------------------------------

    def reserve(
        self,
        *,
        presentation_id: str,
        conversation_id: str,
        display_title: str,
    ) -> bool:
        """原子预留 presentation（status=reserved）；重复 ID 返回 False。"""

        normalized = str(presentation_id or "").strip()
        if not normalized:
            return False
        with self._condition:
            self._cleanup_expired_locked()
            if normalized in self._presentations:
                return False
            self._presentations[normalized] = _PresentationState(
                presentation_id=normalized,
                conversation_id=str(conversation_id or ""),
                display_title=str(display_title or ""),
                reserved_expires_at=datetime.fromtimestamp(
                    _now().timestamp() + self._reservation_ttl_seconds,
                    tz=timezone.utc,
                ),
            )
            return True

    def claim(self, presentation_id: str) -> bool:
        """业务请求原子 claim 已预留 presentation；非法/过期/重复返回 False。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.status != RESERVED:
                return False
            if (
                state.reserved_expires_at is not None
                and _now() >= state.reserved_expires_at
            ):
                state.status = EXPIRED
                state.terminal_at = _now_iso()
                state.closed = True
                self._condition.notify_all()
                return False
            state.status = BOUND
            state.updated_at = _now_iso()
            self._condition.notify_all()
            return True

    def descriptor(self, presentation_id: str) -> dict[str, str] | None:
        """reserve 响应的公开描述。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None:
                return None
            return {
                "presentation_id": state.presentation_id,
                "conversation_id": state.conversation_id,
                "display_title": state.display_title,
                "status": state.status,
            }

    # -- presentation lease ------------------------------------------------

    def acquire_lease(self, presentation_id: str) -> bool:
        """领取唯一 presentation stream lease；已领取/不存在/已过期返回 False。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.leased or state.status == EXPIRED:
                return False
            state.leased = True
            return True

    def release_lease(self, presentation_id: str) -> None:
        """释放 lease；幂等。浏览器断连只释放 lease，不影响业务请求。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is not None:
                state.leased = False

    def claim_root_run(self, presentation_id: str, run_id: str) -> str:
        """原子领取 presentation 的唯一 root run 槽位。

        一次前台交互最多一个根流：同一请求内顺序运行的多个 Agent 只有第一个
        成为 root，后续运行必须作为 child 展示。返回值：

        - 领取成功：当前 ``run_id``；
        - 已有 root：已领取的 root ``run_id``（调用方派生 child 用它作 parent）；
        - presentation 不存在/已过期：空字符串（调用方放弃展示关联，业务照跑）。
        """

        normalized = str(presentation_id or "").strip()
        candidate = str(run_id or "").strip()
        if not normalized or not candidate:
            return ""
        with self._condition:
            state = self._presentations.get(normalized)
            if state is None or state.status == EXPIRED:
                return ""
            if state.root_run_id:
                return state.root_run_id
            state.root_run_id = candidate
            state.updated_at = _now_iso()
            return candidate

    # -- 业务/observer 侧：生命周期与 chunk 发布 ---------------------------

    def mark_agent_started(self, presentation_id: str) -> None:
        """该请求产生了第一个 Agent run；BOUND→RUNNING 并记录 had_agent_run。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.status in TERMINAL_STATUSES:
                return
            state.had_agent_run = True
            if state.status in {RESERVED, BOUND}:
                state.status = RUNNING
                state.updated_at = _now_iso()
            self._condition.notify_all()

    def update_status(self, presentation_id: str, status: str) -> None:
        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.status in TERMINAL_STATUSES:
                return
            state.status = status
            state.updated_at = _now_iso()
            self._condition.notify_all()

    def publish_chunk(self, presentation_id: str, chunk: bytes) -> bool:
        """追加一个官方编码 chunk 并唤醒订阅者。

        缓冲溢出时显式标记 failed 并关闭（不得静默丢 chunk 造成残缺官方流），
        返回 False；调用方应停止发布并降级展示，不得改写业务执行语义。
        """

        normalized = str(presentation_id or "").strip()
        with self._condition:
            state = self._presentations.get(normalized)
            if state is None or state.closed:
                return False
            if len(state.chunks) >= self._max_chunks:
                state.status = FAILED
                state.closed = True
                state.terminal_at = _now_iso()
                state.updated_at = state.terminal_at
                state.error_code = BUFFER_OVERFLOW_CODE
                state.error_message = "展示缓冲溢出，实时流已安全关闭。"
                self._condition.notify_all()
                return False
            state.chunks.append(chunk)
            self._condition.notify_all()
            return True

    def mark_terminal(
        self,
        *,
        presentation_id: str,
        status: str,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        """标记终态并关闭 chunk 流；不保存任何业务结果。"""

        if status not in TERMINAL_STATUSES:
            raise ValueError(f"mark_terminal 只接受终态，收到：{status!r}")
        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.status in TERMINAL_STATUSES:
                return
            state.status = status
            state.closed = True
            state.terminal_at = _now_iso()
            state.updated_at = state.terminal_at
            state.error_code = str(error_code or "")
            state.error_message = str(error_message or "")
            self._condition.notify_all()

    def finish_request(
        self,
        presentation_id: str,
        *,
        request_failed: bool = False,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        """HTTP 公共边界在 handler 返回/抛错后收尾 presentation。

        整个请求未产生 Agent run 时关闭空 presentation（SSE 确定结束，不得
        永久等待）；已产生 Agent run 时按业务请求成败标记终态。
        """

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None or state.status in TERMINAL_STATUSES:
                return
            if request_failed:
                state.status = FAILED
                state.error_code = str(error_code or "AI_BUSINESS_REQUEST_FAILED")
                state.error_message = str(error_message or "业务请求失败。")
            else:
                state.status = COMPLETED
            state.closed = True
            state.terminal_at = _now_iso()
            state.updated_at = state.terminal_at
            self._condition.notify_all()

    # -- 订阅者侧：chunk 读取 ----------------------------------------------

    def read_chunks(
        self,
        presentation_id: str,
        cursor: int,
        *,
        wait_timeout: float | None = 1.0,
    ) -> tuple[list[bytes], int, bool]:
        """阻塞直到有新 chunk 或流关闭。

        返回 ``(new_chunks, new_cursor, closed)``。超时且无新 chunk 时返回
        ``([], cursor, False)``，调用方可继续循环（周期性 flush/心跳）。
        stream 早于业务绑定时在缓冲上等待，不丢首个 chunk。
        """

        normalized = str(presentation_id or "").strip()
        with self._condition:
            while True:
                state = self._presentations.get(normalized)
                if state is None or state.status == EXPIRED:
                    return [], cursor, True
                if (
                    state.status == RESERVED
                    and state.reserved_expires_at is not None
                    and _now() >= state.reserved_expires_at
                ):
                    # 订阅者等待期间 reservation 过期：就地标记终态，保证
                    # stream 确定结束，不依赖外部清理周期。
                    state.status = EXPIRED
                    state.closed = True
                    state.terminal_at = _now_iso()
                    self._condition.notify_all()
                    return [], cursor, True
                if cursor < len(state.chunks) or state.closed:
                    new_chunks = state.chunks[cursor:]
                    new_cursor = len(state.chunks)
                    return new_chunks, new_cursor, state.closed
                if not self._condition.wait(timeout=wait_timeout):
                    return [], cursor, False

    def iter_chunks(
        self,
        presentation_id: str,
        *,
        wait_timeout: float | None = 1.0,
    ) -> Iterator[bytes]:
        """从游标 0 顺序产出所有 chunk，直到流关闭并读完。"""

        cursor = 0
        while True:
            chunks, cursor, closed = self.read_chunks(
                presentation_id,
                cursor,
                wait_timeout=wait_timeout,
            )
            for chunk in chunks:
                yield chunk
            if closed and not chunks:
                return

    # -- 状态读取（仅展示元数据，不含业务结果）------------------------------

    def status_payload(self, presentation_id: str) -> dict[str, object] | None:
        """通用状态接口的读取视图；不存在返回 None。"""

        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            if state is None:
                return None
            return {
                "presentation_id": state.presentation_id,
                "conversation_id": state.conversation_id,
                "display_title": state.display_title,
                "status": state.status,
                "terminal": state.status in TERMINAL_STATUSES,
                "had_agent_run": state.had_agent_run,
                "error_code": state.error_code,
                "error_message": state.error_message,
            }

    def conversation_id(self, presentation_id: str) -> str:
        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            return str(state.conversation_id if state else "")

    def is_terminal(self, presentation_id: str) -> bool:
        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            return bool(state and state.status in TERMINAL_STATUSES)

    def had_agent_run(self, presentation_id: str) -> bool:
        with self._condition:
            state = self._presentations.get(str(presentation_id or "").strip())
            return bool(state and state.had_agent_run)

    # -- TTL 清理 -----------------------------------------------------------

    def cleanup_expired(self) -> int:
        with self._condition:
            return self._cleanup_expired_locked()

    def _cleanup_expired_locked(self) -> int:
        now = _now()
        removed = 0
        for presentation_id in list(self._presentations):
            state = self._presentations[presentation_id]
            if state.status == RESERVED:
                if (
                    state.reserved_expires_at is not None
                    and now >= state.reserved_expires_at
                ):
                    state.status = EXPIRED
                    state.closed = True
                    state.terminal_at = _now_iso()
                    removed += 1
                continue
            if state.terminal_at is None:
                continue
            try:
                terminal_at = datetime.fromisoformat(state.terminal_at)
            except ValueError:
                continue
            if (now - terminal_at).total_seconds() >= self._ttl_seconds:
                del self._presentations[presentation_id]
                removed += 1
        if removed:
            self._condition.notify_all()
        return removed
