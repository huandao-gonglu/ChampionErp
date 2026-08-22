"""Pydantic Deferred Tool call 与 ERP Global Task 的关联 ledger。

link ledger 只记录关联、首次 history 提交屏障、continuation claim 与结果
提交状态；不复制 Agent graph、工具请求正文、完整消息历史或事件，因此不是
第二套 Agent 状态机。Pydantic message history 保存原始 ``ToolCallPart``，
不保存 ``DeferredToolRequests`` output 和 ``CallDeferred.metadata``；本表是
task/call 关联与必要 Deferred metadata 的唯一持久化事实源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from pydantic_ai.messages import ModelMessage

from erp_web.db import ErpDatabase
from erp_web.stores.pydantic_message_store import canonical_model_messages_json


AWAITING_HISTORY = "awaiting_history"
READY = "ready"
RESOLVED = "resolved"
ABANDONED = "abandoned"
ACTIVE_LINK_STATUSES = frozenset({AWAITING_HISTORY, READY})

DEFERRED_HANDSHAKE_OUTBOX_KIND = "deferred_handshake"
CONTINUATION_OUTBOX_KIND = "continuation"

# outbox 批次只持久化 run 的“终态段”：订阅端批次仅用于推进已提交版本游标并
# 触发 /ui-messages 重同步，流式 delta 的完整内容以已提交历史（/ui-messages）
# 为事实源。线上事故（2026-08-21，conversation_global_chat_ab3810c1...）证明
# 流式 run 按 delta 逐条编码时 chunk 数轻松达到数百上千；若持久化整条 delta
# 流，条数防线会必然击穿并让合法的 Deferred 握手提交失败。
MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS = 64

# 单批官方编码事件的条数防线：终态段截取后批次必然不超过
# MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS，该上限只是防止采集侧失控的最后防线。
MAX_OUTBOX_EVENT_CHUNKS = 512
# 单条官方编码 chunk 与单批总量的字节上限；防止超大输出击穿 outbox。
MAX_OUTBOX_EVENT_CHUNK_BYTES = 64 * 1024
MAX_OUTBOX_EVENT_TOTAL_BYTES = 1024 * 1024

# continuation claim 的默认租约：必须显著大于模型运行超时（global.chat 为
# 180 秒），确保租约存续期间第二个 worker 不可能再次领取并重复调用模型。
DEFAULT_CONTINUATION_LEASE_SECONDS = 600.0


class PydanticDeferredLinkError(RuntimeError):
    """Deferred link 无法按服务端契约读写。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "PYDANTIC_DEFERRED_LINK_ERROR")
        super().__init__(message)


@dataclass(frozen=True)
class DeferredTaskLink:
    """一条 task ↔ tool_call 关联记录的类型化投影。"""

    link_id: str
    conversation_id: str
    request_run_id: str
    tool_call_id: str
    task_id: str
    link_status: str
    history_version: int
    created_at: str
    ready_at: str
    lease_id: str
    lease_expires_at: float
    continuation_run_id: str
    resolved_at: str
    abandoned_at: str
    last_error_code: str

    @property
    def active(self) -> bool:
        return self.link_status in ACTIVE_LINK_STATUSES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(name: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_LINK_INVALID",
            f"Deferred task link 缺少 {name}。",
        )
    return normalized


def _link_from_row(row: Mapping[str, Any]) -> DeferredTaskLink:
    try:
        return DeferredTaskLink(
            link_id=str(row["link_id"] or ""),
            conversation_id=str(row["conversation_id"] or ""),
            request_run_id=str(row["request_run_id"] or ""),
            tool_call_id=str(row["tool_call_id"] or ""),
            task_id=str(row["task_id"] or ""),
            link_status=str(row["link_status"] or ""),
            history_version=int(row["history_version"] or 0),
            created_at=str(row["created_at"] or ""),
            ready_at=str(row["ready_at"] or ""),
            lease_id=str(row["lease_id"] or ""),
            lease_expires_at=float(row["lease_expires_at"] or 0),
            continuation_run_id=str(row["continuation_run_id"] or ""),
            resolved_at=str(row["resolved_at"] or ""),
            abandoned_at=str(row["abandoned_at"] or ""),
            last_error_code=str(row["last_error_code"] or ""),
        )
    except Exception:
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_LINK_CORRUPT",
            "已保存的 Deferred task link 损坏或格式无效。",
        ) from None


def _terminal_event_segment(encoded_chunks: Sequence[str]) -> list[str]:
    """截取 run 官方编码流的有界终态段用于 outbox 持久化。

    流式 run 按 delta 逐条编码，整条 delta 流可达数千 chunk；outbox 批次
    不用于重放完整流式内容（订阅端以 /ui-messages 的已提交历史为内容事实
    源，批次只推进已提交版本游标并触发重同步），因此只保留按原顺序取尾的
    终态段，既能让原始流消费者看到回合闭合事件，又把批次大小固定在常数内。
    """

    chunks = [str(chunk) for chunk in list(encoded_chunks)]
    return chunks[-MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS:]


def _bounded_events_json(encoded_chunks: Sequence[str]) -> str:
    import json

    chunks = [str(chunk) for chunk in list(encoded_chunks)]
    if len(chunks) > MAX_OUTBOX_EVENT_CHUNKS:
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE",
            f"官方编码事件批次超过 {MAX_OUTBOX_EVENT_CHUNKS} 条上限。",
        )
    total_bytes = 0
    for chunk in chunks:
        chunk_bytes = len(chunk.encode("utf-8"))
        if chunk_bytes > MAX_OUTBOX_EVENT_CHUNK_BYTES:
            raise PydanticDeferredLinkError(
                "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE",
                f"单条官方编码事件 {chunk_bytes} 字节，"
                f"超过 {MAX_OUTBOX_EVENT_CHUNK_BYTES} 字节上限。",
            )
        total_bytes += chunk_bytes
    if total_bytes > MAX_OUTBOX_EVENT_TOTAL_BYTES:
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE",
            f"官方编码事件批次共 {total_bytes} 字节，"
            f"超过 {MAX_OUTBOX_EVENT_TOTAL_BYTES} 字节上限。",
        )
    return json.dumps(chunks, ensure_ascii=False)


class PydanticDeferredTaskLinkStore:
    """link 生命周期、原子提交屏障与 continuation claim 的持久化边界。"""

    def __init__(self, db: ErpDatabase) -> None:
        self.db = db

    # -- 创建与读取 ---------------------------------------------------------

    def create_with_task(
        self,
        task_payload: Mapping[str, Any],
        *,
        conversation_id: str,
        request_run_id: str,
        tool_call_id: str,
    ) -> tuple[dict[str, Any], DeferredTaskLink]:
        """同一 SQLite 事务创建 Task 与 provisional link。

        link 初始 ``awaiting_history`` 且 ``ready_at`` 为空；conversation 级
        active link 唯一约束在这里拒绝同一会话的第二个未解决 Deferred。
        """

        _required("conversation_id", conversation_id)
        _required("request_run_id", request_run_id)
        _required("tool_call_id", tool_call_id)
        link_id = f"dlink_{uuid4().hex}"
        try:
            payload, link_row = self.db.create_global_task_with_deferred_link(
                dict(task_payload),
                link_id=link_id,
                conversation_id=conversation_id.strip(),
                request_run_id=request_run_id.strip(),
                tool_call_id=tool_call_id.strip(),
                now=_now().isoformat(),
            )
        except ValueError as exc:
            raise PydanticDeferredLinkError(
                "PYDANTIC_DEFERRED_LINK_INVALID",
                str(exc),
            ) from None
        return payload, _link_from_row(link_row)

    def get(self, link_id: str) -> DeferredTaskLink | None:
        row = self.db.get_deferred_task_link(str(link_id or "").strip())
        return _link_from_row(row) if row is not None else None

    def get_by_task(self, task_id: str) -> DeferredTaskLink | None:
        row = self.db.get_deferred_task_link_by_task(
            str(task_id or "").strip()
        )
        return _link_from_row(row) if row is not None else None

    def active_for_conversation(
        self,
        conversation_id: str,
    ) -> DeferredTaskLink | None:
        row = self.db.active_deferred_task_link_for_conversation(
            str(conversation_id or "").strip()
        )
        return _link_from_row(row) if row is not None else None

    def has_active(self, conversation_id: str) -> bool:
        return self.active_for_conversation(conversation_id) is not None

    # -- 首次 Deferred history 提交（ready 屏障） ---------------------------

    def commit_initial_deferred_history(
        self,
        conversation_id: str,
        messages: Sequence[ModelMessage],
        *,
        link_id: str,
        request_run_id: str,
        encoded_chunks: Sequence[str],
    ) -> int:
        """同一事务保存官方 history、置 link ready 并写首次握手 outbox。

        outbox 批次只持久化 ``encoded_chunks`` 的有界终态段（见
        ``_terminal_event_segment``），不持久化整条流式 delta 流。
        返回提交后的 conversation history version。
        """

        normalized_id = _required("conversation_id", conversation_id)
        normalized_link_id = _required("link_id", link_id)
        messages_json = canonical_model_messages_json(messages, stored=False)
        # outbox 只持久化有界终态段（不是整条流式 delta 流）；字节上限校验
        # 必须在状态转换前完成：它是 PydanticDeferredLinkError（RuntimeError
        # 子类），不能被下方的状态错误分支吞掉并改码。
        events_json = _bounded_events_json(
            _terminal_event_segment(encoded_chunks)
        )
        try:
            row = self.db.commit_deferred_history_ready(
                normalized_id,
                messages_json,
                now=_now().isoformat(),
                link_id=normalized_link_id,
                outbox_run_id=_required("request_run_id", request_run_id),
                outbox_kind=DEFERRED_HANDSHAKE_OUTBOX_KIND,
                outbox_events_json=events_json,
            )
        except RuntimeError as exc:
            raise PydanticDeferredLinkError(
                "PYDANTIC_DEFERRED_LINK_NOT_AWAITING",
                str(exc),
            ) from None
        except ValueError as exc:
            raise PydanticDeferredLinkError(
                "PYDANTIC_DEFERRED_LINK_INVALID",
                str(exc),
            ) from None
        return int(row["history_version"] or 0)

    def repair_to_ready(
        self,
        link_id: str,
        *,
        history_version: int,
    ) -> DeferredTaskLink | None:
        """崩溃恢复：已存在匹配 Deferred history 的 provisional link 修复为 ready。"""

        row = self.db.mark_deferred_link_ready_from_history(
            _required("link_id", link_id),
            now=_now().isoformat(),
            history_version=int(history_version),
        )
        return _link_from_row(row) if row is not None else None

    def abandon_expired(
        self,
        link_id: str,
        *,
        cancel_assistant_message: str,
    ) -> DeferredTaskLink | None:
        """无法形成首次 history 的 provisional link：abandon 并取消任务。"""

        row = self.db.abandon_deferred_link_and_cancel_task(
            _required("link_id", link_id),
            now=_now().isoformat(),
            cancel_assistant_message=str(cancel_assistant_message or ""),
        )
        return _link_from_row(row) if row is not None else None

    def list_expired_provisional(
        self,
        *,
        ttl_seconds: float,
        limit: int = 50,
    ) -> list[DeferredTaskLink]:
        cutoff = _now() - timedelta(seconds=max(1.0, float(ttl_seconds)))
        rows = self.db.list_expired_provisional_deferred_links(
            cutoff_iso=cutoff.isoformat(),
            limit=limit,
        )
        return [_link_from_row(row) for row in rows]

    # -- continuation claim 与最终提交 --------------------------------------

    def list_continuable(self, *, limit: int = 50) -> list[DeferredTaskLink]:
        rows = self.db.list_continuable_deferred_task_links(limit=limit)
        return [_link_from_row(row) for row in rows]

    def claim(
        self,
        link_id: str,
        *,
        lease_seconds: float = DEFAULT_CONTINUATION_LEASE_SECONDS,
    ) -> tuple[DeferredTaskLink, str] | None:
        """原子领取 continuation claim；返回 link 与本进程 lease_id。

        租约必须覆盖模型运行超时窗口（默认 600 秒 > global.chat 的 180 秒），
        租约存续期间第二个 worker 不能再次领取，避免并行调用模型。
        """

        lease_id = f"dlease_{uuid4().hex}"
        row = self.db.claim_deferred_task_link(
            _required("link_id", link_id),
            lease_id=lease_id,
            lease_seconds=max(1.0, float(lease_seconds)),
        )
        if row is None:
            return None
        return _link_from_row(row), lease_id

    def release_claim(self, link_id: str, lease_id: str) -> bool:
        return self.db.release_deferred_task_link_claim(
            _required("link_id", link_id),
            lease_id=str(lease_id or "").strip(),
        )

    def set_last_error(self, link_id: str, *, error_code: str) -> None:
        self.db.update_deferred_task_link_last_error(
            _required("link_id", link_id),
            error_code=str(error_code or "").strip(),
        )

    def commit_continuation_history(
        self,
        conversation_id: str,
        messages: Sequence[ModelMessage],
        *,
        link_id: str,
        expected_version: int,
        continuation_run_id: str,
        lease_id: str,
        encoded_chunks: Sequence[str],
    ) -> int | None:
        """同一事务 CAS 保存 continuation history、resolved link 与 outbox。

        CAS 失败返回 None；调用方必须重新读取对账，不得盲目追加消息。link
        更新同时校验 ``lease_id``：租约被第二个 worker 接管后，原执行者的最终
        提交必须失败。outbox 批次与首次握手一致，只持久化有界终态段。
        成功时返回提交后的 history version。
        """

        normalized_id = _required("conversation_id", conversation_id)
        normalized_link_id = _required("link_id", link_id)
        normalized_run_id = _required("continuation_run_id", continuation_run_id)
        normalized_lease_id = _required("lease_id", lease_id)
        messages_json = canonical_model_messages_json(messages, stored=False)
        # 与首次握手一致：outbox 只持久化有界终态段。
        events_json = _bounded_events_json(
            _terminal_event_segment(encoded_chunks)
        )
        try:
            row = self.db.commit_continuation_history_resolved(
                normalized_id,
                messages_json,
                now=_now().isoformat(),
                link_id=normalized_link_id,
                expected_version=int(expected_version),
                continuation_run_id=normalized_run_id,
                lease_id=normalized_lease_id,
                outbox_kind=CONTINUATION_OUTBOX_KIND,
                outbox_events_json=events_json,
            )
        except ValueError as exc:
            raise PydanticDeferredLinkError(
                "PYDANTIC_DEFERRED_LINK_INVALID",
                str(exc),
            ) from None
        if row is None:
            return None
        return int(row["history_version"] or 0)


__all__ = [
    "ABANDONED",
    "ACTIVE_LINK_STATUSES",
    "AWAITING_HISTORY",
    "CONTINUATION_OUTBOX_KIND",
    "DEFAULT_CONTINUATION_LEASE_SECONDS",
    "DEFERRED_HANDSHAKE_OUTBOX_KIND",
    "DeferredTaskLink",
    "MAX_OUTBOX_EVENT_CHUNKS",
    "MAX_OUTBOX_EVENT_CHUNK_BYTES",
    "MAX_OUTBOX_EVENT_TOTAL_BYTES",
    "MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS",
    "PydanticDeferredLinkError",
    "PydanticDeferredTaskLinkStore",
    "READY",
    "RESOLVED",
]
