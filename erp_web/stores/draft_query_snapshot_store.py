"""草稿查询快照的独立持久化边界。"""

from __future__ import annotations

from erp_web.db import ErpDatabase
from erp_web.schemas.draft_capabilities import DraftQuerySnapshot


class DraftQuerySnapshotStoreError(RuntimeError):
    """草稿查询快照无法按当前契约保存或读取。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "DRAFT_QUERY_SNAPSHOT_STORE_ERROR")
        super().__init__(message)


class DraftQuerySnapshotStore:
    """验证并持久化 ``DraftQuerySnapshot``，不依赖 Global Task。"""

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db

    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        validated = DraftQuerySnapshot.model_validate(snapshot)
        try:
            self._db.save_draft_query_snapshot(
                validated.model_dump(mode="json")
            )
        except ValueError as exc:
            raise DraftQuerySnapshotStoreError(
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


__all__ = [
    "DraftQuerySnapshotStore",
    "DraftQuerySnapshotStoreError",
]
