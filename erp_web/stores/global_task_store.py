"""LocalGlobalTaskState 与 DraftQuerySnapshot 的唯一持久化 owner。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from typing import Iterator

from erp_web.db import ErpDatabase
from erp_web.schemas.draft_capabilities import DraftQuerySnapshot
from erp_web.schemas.global_tasks import LocalGlobalTaskState


class GlobalTaskStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "GLOBAL_TASK_STORE_ERROR")
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LocalGlobalTaskStore:
    """SQLite 整体状态保存 + 单进程 task_id 级互斥。"""

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db
        self._locks_guard = threading.Lock()
        self._task_locks: dict[str, threading.RLock] = {}

    @contextmanager
    def task_lock(self, task_id: str) -> Iterator[None]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise GlobalTaskStoreError("GLOBAL_TASK_ID_REQUIRED", "任务 ID 不能为空。")
        with self._locks_guard:
            lock = self._task_locks.setdefault(normalized, threading.RLock())
        with lock:
            yield

    def create_task(
        self,
        state: LocalGlobalTaskState,
    ) -> LocalGlobalTaskState:
        validated = LocalGlobalTaskState.model_validate(state)
        try:
            self._db.create_global_task(validated.model_dump(mode="json"))
        except ValueError as exc:
            message = str(exc)
            code = (
                "GLOBAL_TASK_CONVERSATION_BUSY"
                if "已有未完成任务" in message
                else "GLOBAL_TASK_CREATE_INVALID"
            )
            raise GlobalTaskStoreError(code, message) from None
        return validated

    def save_task(
        self,
        state: LocalGlobalTaskState,
        *,
        touch: bool = True,
    ) -> LocalGlobalTaskState:
        validated = LocalGlobalTaskState.model_validate(state)
        if touch:
            validated = validated.model_copy(update={"updated_at": _now()})
        try:
            self._db.save_global_task(validated.model_dump(mode="json"))
        except FileNotFoundError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_NOT_FOUND", str(exc)) from None
        except ValueError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_SAVE_INVALID", str(exc)) from None
        return validated

    def load_task(self, task_id: str) -> LocalGlobalTaskState | None:
        payload = self._db.load_global_task(task_id)
        return LocalGlobalTaskState.model_validate(payload) if payload else None

    def require_task(self, task_id: str) -> LocalGlobalTaskState:
        task = self.load_task(task_id)
        if task is None:
            raise GlobalTaskStoreError(
                "GLOBAL_TASK_NOT_FOUND",
                f"全局任务不存在：{str(task_id or '').strip()}",
            )
        return task

    def find_active_task(
        self,
        ai_work_conversation_id: str,
    ) -> LocalGlobalTaskState | None:
        payload = self._db.find_active_global_task(ai_work_conversation_id)
        return LocalGlobalTaskState.model_validate(payload) if payload else None

    def list_unfinished_tasks(self) -> list[LocalGlobalTaskState]:
        return [
            LocalGlobalTaskState.model_validate(payload)
            for payload in self._db.list_unfinished_global_tasks()
        ]

    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        validated = DraftQuerySnapshot.model_validate(snapshot)
        try:
            self._db.save_draft_query_snapshot(validated.model_dump(mode="json"))
        except ValueError as exc:
            raise GlobalTaskStoreError(
                "DRAFT_QUERY_SNAPSHOT_CONFLICT",
                str(exc),
            ) from None
        return validated

    def load_draft_query_snapshot(
        self,
        snapshot_id: str,
    ) -> DraftQuerySnapshot | None:
        payload = self._db.load_draft_query_snapshot(snapshot_id)
        return DraftQuerySnapshot.model_validate(payload) if payload else None


__all__ = ["GlobalTaskStoreError", "LocalGlobalTaskStore"]
