"""独立图片批次库，保存业务状态，不复制原生模型历史。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock

from erp_web.schemas.image_batches import ImageBatch


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
            conn.execute("INSERT INTO image_batches VALUES (?, ?)", (batch.conversation_id, batch.model_dump_json()))

    def get(self, conversation_id: str) -> ImageBatch | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM image_batches WHERE conversation_id=?", (conversation_id,)).fetchone()
        return ImageBatch.model_validate_json(row[0]) if row else None

    def update_item(self, conversation_id: str, index: int, **changes) -> None:
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM image_batches WHERE conversation_id=?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError("图片批次不存在。")
            batch = ImageBatch.model_validate_json(row[0])
            batch.items[index] = type(batch.items[index]).model_validate({**batch.items[index].model_dump(), **changes})
            batch.updated_at = utc_now()
            conn.execute("UPDATE image_batches SET payload=? WHERE conversation_id=?", (batch.model_dump_json(), conversation_id))
