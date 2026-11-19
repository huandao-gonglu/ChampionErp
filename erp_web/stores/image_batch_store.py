"""独立图片批次库，保存业务状态，不复制原生模型历史。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock

from erp_web.schemas.image_capabilities import DraftImagesPrepareResult
from erp_web.schemas.image_batches import ImageBatch
from erp_web.schemas.requests import RequestValidationError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImageBatchStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("图片批次数据库版本不受支持。")
            conn.execute("CREATE TABLE IF NOT EXISTS image_batches (conversation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            # 准备任务覆盖规划、逐页生成和最终采用。普通工作台批次只记录逐页结果。
            conn.execute("""CREATE TABLE IF NOT EXISTS draft_image_preparations (
                batch_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_image_preparations_draft ON draft_image_preparations(draft_id)")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_image_preparations_active
                            ON draft_image_preparations(draft_id) WHERE json_extract(result, '$.status')='queued'""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_image_batches_draft_time ON image_batches(
                json_extract(payload, '$.draft_id'), json_extract(payload, '$.created_at') DESC)""")
            conn.execute("PRAGMA user_version=1")
            # 启动时只结算中断，不重放可能已计费的图片请求。
            for conversation_id, payload in conn.execute("SELECT conversation_id, payload FROM image_batches").fetchall():
                batch = ImageBatch.model_validate_json(payload)
                changed = False
                for item in batch.items:
                    if item.status in ("queued", "running"):
                        item.outcome_unknown = item.status == "running"
                        item.error = ("服务已重启，处理结果未确认，请核对原结果。" if item.outcome_unknown
                                      else "服务已重启，此图片尚未执行。")
                        item.status = "failed"
                        item.finished_at = utc_now()
                        changed = True
                if changed:
                    batch.updated_at = utc_now()
                    conn.execute("UPDATE image_batches SET payload=? WHERE conversation_id=?", (batch.model_dump_json(), conversation_id))

            # 满批生成后仍可能尚未采用。中断时保留真实页数，不能仅凭全批成功声明已采用。
            for batch_id, result, payload in conn.execute("""
                SELECT p.batch_id, p.result, b.payload FROM draft_image_preparations p
                LEFT JOIN image_batches b ON b.conversation_id=p.batch_id
                WHERE json_extract(p.result, '$.status')='queued'
            """).fetchall():
                prior = DraftImagesPrepareResult.model_validate_json(result)
                items = ImageBatch.model_validate_json(payload).items if payload else []
                stopped = prior.model_copy(update={
                    "status": "failed", "generated_count": sum(item.status == "success" for item in items),
                    "failed_count": sum(item.status == "failed" for item in items),
                    "message": "服务已重启，套图采用结果待核对，请查看原批次和草稿图片。",
                })
                conn.execute("UPDATE draft_image_preparations SET result=? WHERE batch_id=?", (stopped.model_dump_json(), batch_id))

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, batch: ImageBatch) -> None:
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if batch.draft_id:
                rows = conn.execute("SELECT payload FROM image_batches").fetchall()
                for row in rows:
                    existing = ImageBatch.model_validate_json(row[0])
                    if existing.draft_id == batch.draft_id and self.blocks_changes(existing):
                        raise RequestValidationError("当前草稿有图片正在处理，请等任务结束后再操作。", status_code=409, error_code="DRAFT_IMAGES_BUSY")
            conn.execute("INSERT INTO image_batches VALUES (?, ?)", (batch.conversation_id, batch.model_dump_json()))

    def get(self, conversation_id: str) -> ImageBatch | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM image_batches WHERE conversation_id=?", (conversation_id,)).fetchone()
        return ImageBatch.model_validate_json(row[0]) if row else None

    @contextmanager
    def _edit_batch(self, conversation_id: str):
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM image_batches WHERE conversation_id=?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError("图片批次不存在。")
            batch = ImageBatch.model_validate_json(row[0])
            batch.updated_at = utc_now()
            yield batch
            conn.execute("UPDATE image_batches SET payload=? WHERE conversation_id=?", (batch.model_dump_json(), conversation_id))

    def update_item(self, conversation_id: str, index: int, **changes) -> None:
        with self._edit_batch(conversation_id) as batch:
            item = batch.items[index]
            batch.items[index] = type(item).model_validate({**item.model_dump(), **changes})

    def fail_queued(self, conversation_id: str, reason: str) -> None:
        """在同一事务中结束未派发项，保留运行中及已有回执的图片。"""
        with self._edit_batch(conversation_id) as batch:
            for item in batch.items:
                if item.status == "queued":
                    item.status = "failed"
                    item.error = reason
                    item.finished_at = batch.updated_at

    def preparation(self, batch_id: str, *, arguments: str | None = None) -> DraftImagesPrepareResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT result, arguments FROM draft_image_preparations WHERE batch_id=?", (batch_id,)).fetchone()
        if row and arguments is not None and row[1] != arguments:
            raise RequestValidationError("套图任务编号已用于其他参数。", status_code=409, error_code="IMAGE_JOB_CONFLICT")
        return DraftImagesPrepareResult.model_validate_json(row[0]) if row else None

    def latest_preparation(self, draft_id: str) -> DraftImagesPrepareResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT result FROM draft_image_preparations WHERE draft_id=? ORDER BY rowid DESC LIMIT 1",
                               (draft_id,)).fetchone()
        return DraftImagesPrepareResult.model_validate_json(row[0]) if row else None

    def start_preparation(self, result: DraftImagesPrepareResult, arguments: str) -> None:
        with self.lock, self._connect() as conn:
            conn.execute("INSERT INTO draft_image_preparations VALUES (?, ?, ?, ?)",
                         (result.batch_id, result.draft_id, arguments, result.model_dump_json()))

    def finish_preparation(self, result: DraftImagesPrepareResult) -> None:
        with self.lock, self._connect() as conn:
            conn.execute("""UPDATE draft_image_preparations SET result=?
                            WHERE batch_id=? AND json_extract(result, '$.status')='queued'""",
                         (result.model_dump_json(), result.batch_id))

    @staticmethod
    def blocks_changes(batch: ImageBatch) -> bool:
        return any(item.status in ("queued", "running") for item in batch.items)

    def latest_for_draft(self, draft_id: str) -> ImageBatch | None:
        with self._connect() as conn:
            row = conn.execute("""SELECT payload FROM image_batches
                WHERE json_extract(payload, '$.draft_id') = ?
                ORDER BY json_extract(payload, '$.created_at') DESC, rowid LIMIT 1""", (draft_id,)).fetchone()
        return ImageBatch.model_validate_json(row[0]) if row else None

    def assert_draft_idle(self, draft_id: str) -> None:
        batch = self.latest_for_draft(draft_id)
        if batch and self.blocks_changes(batch):
            raise RequestValidationError("当前草稿有图片正在处理，请等任务结束后再操作。", status_code=409, error_code="DRAFT_IMAGES_BUSY")
