"""LocalGlobalTaskState 与 DraftQuerySnapshot 的唯一持久化 owner。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from typing import Iterator
from uuid import uuid4

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
    """SQLite 状态 CAS、跨进程执行 lease 与单进程 task_id 互斥。"""

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db
        self._locks_guard = threading.Lock()
        self._task_locks: dict[str, threading.RLock] = {}
        self._execution_owner = f"global-task-owner:{uuid4().hex}"
        self._execution_context = threading.local()

    @contextmanager
    def task_lock(
        self,
        task_id: str,
        *,
        blocking: bool = True,
    ) -> Iterator[bool]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise GlobalTaskStoreError("GLOBAL_TASK_ID_REQUIRED", "任务 ID 不能为空。")
        with self._locks_guard:
            lock = self._task_locks.setdefault(normalized, threading.RLock())
        acquired = lock.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def create_task(
        self,
        state: LocalGlobalTaskState,
    ) -> LocalGlobalTaskState:
        validated = LocalGlobalTaskState.model_validate(state)
        try:
            payload = self._db.create_global_task(
                validated.model_dump(mode="json")
            )
        except ValueError as exc:
            raise GlobalTaskStoreError(
                "GLOBAL_TASK_CREATE_INVALID",
                str(exc),
            ) from None
        return LocalGlobalTaskState.model_validate(payload)

    @contextmanager
    def create_task_claimed(
        self,
        state: LocalGlobalTaskState,
        *,
        lease_seconds: float = 30.0,
    ) -> Iterator[LocalGlobalTaskState]:
        """在 INSERT 事务内领取首次执行权，并在上下文内维持续租。"""

        validated = LocalGlobalTaskState.model_validate(state)
        execution_id = f"gexec_{uuid4().hex}"
        try:
            payload = self._db.create_global_task(
                validated.model_dump(mode="json"),
                execution_owner=self._execution_owner,
                execution_id=execution_id,
                lease_seconds=lease_seconds,
            )
        except ValueError as exc:
            raise GlobalTaskStoreError(
                "GLOBAL_TASK_CREATE_INVALID",
                str(exc),
            ) from None
        with self._claimed_execution(
            validated.task_id,
            execution_id=execution_id,
            payload=payload,
            lease_seconds=lease_seconds,
        ) as claimed:
            yield claimed

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
            payload = self._db.save_global_task(
                validated.model_dump(mode="json"),
                expected_revision=validated.revision,
                execution_owner=str(
                    getattr(self._execution_context, "owner", "") or ""
                ),
                execution_id=str(
                    getattr(self._execution_context, "execution_id", "") or ""
                ),
            )
        except FileNotFoundError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_NOT_FOUND", str(exc)) from None
        except ValueError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_SAVE_INVALID", str(exc)) from None
        except RuntimeError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_REVISION_CONFLICT", str(exc)) from None
        return LocalGlobalTaskState.model_validate(payload)

    @contextmanager
    def execution_claim(
        self,
        task_id: str,
        *,
        lease_seconds: float = 30.0,
        allowed_statuses: frozenset[str] | None = None,
    ) -> Iterator[LocalGlobalTaskState | None]:
        """领取执行权并在模型/Capability 长调用期间后台续租。"""

        normalized = str(task_id or "").strip()
        execution_id = f"gexec_{uuid4().hex}"
        try:
            payload = self._db.claim_global_task_execution(
                normalized,
                owner=self._execution_owner,
                execution_id=execution_id,
                lease_seconds=lease_seconds,
                allowed_statuses=allowed_statuses,
            )
        except FileNotFoundError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_NOT_FOUND", str(exc)) from None
        except ValueError as exc:
            raise GlobalTaskStoreError("GLOBAL_TASK_CLAIM_INVALID", str(exc)) from None
        if not payload:
            yield None
            return

        with self._claimed_execution(
            normalized,
            execution_id=execution_id,
            payload=payload,
            lease_seconds=lease_seconds,
        ) as claimed:
            yield claimed

    @contextmanager
    def _claimed_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        payload: dict[str, object],
        lease_seconds: float,
    ) -> Iterator[LocalGlobalTaskState]:
        """管理已经由 SQLite 原子领取的 execution token 生命周期。"""

        stopped = threading.Event()

        def renew() -> None:
            interval = max(0.5, float(lease_seconds) / 3)
            while not stopped.wait(interval):
                if not self._db.renew_global_task_execution(
                    task_id,
                    owner=self._execution_owner,
                    execution_id=execution_id,
                    lease_seconds=lease_seconds,
                ):
                    return

        heartbeat = threading.Thread(
            target=renew,
            name=f"global-task-lease-{task_id}",
            daemon=True,
        )
        heartbeat.start()
        previous_owner = getattr(self._execution_context, "owner", "")
        previous_execution_id = getattr(
            self._execution_context,
            "execution_id",
            "",
        )
        self._execution_context.owner = self._execution_owner
        self._execution_context.execution_id = execution_id
        try:
            yield LocalGlobalTaskState.model_validate(payload)
        finally:
            stopped.set()
            heartbeat.join(timeout=max(0.5, float(lease_seconds) / 3 + 0.5))
            self._execution_context.owner = previous_owner
            self._execution_context.execution_id = previous_execution_id
            self._db.release_global_task_execution(
                task_id,
                owner=self._execution_owner,
                execution_id=execution_id,
            )

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

    def list_unfinished_tasks(self) -> list[LocalGlobalTaskState]:
        return [
            LocalGlobalTaskState.model_validate(payload)
            for payload in self._db.list_unfinished_global_tasks()
        ]

    def list_recoverable_tasks(
        self,
        *,
        limit: int = 100,
    ) -> list[LocalGlobalTaskState]:
        return [
            LocalGlobalTaskState.model_validate(payload)
            for payload in self._db.list_recoverable_global_tasks(limit=limit)
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
