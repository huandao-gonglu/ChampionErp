"""外部请求的纯观测审计与写入防重放存储（schema v3，不参与并发/限流轮询）。"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from erp_web.schemas.external_requests import RequestContext

EXTERNAL_REQUEST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS external_attempts (
    id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    source TEXT NOT NULL,
    trigger TEXT NOT NULL,
    quota_key TEXT NOT NULL,
    method TEXT NOT NULL,
    semantics TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    released REAL,
    completed REAL,
    sent INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS external_attempt_filter ON external_attempts(platform, account_id, created);
CREATE INDEX IF NOT EXISTS external_attempt_operation ON external_attempts(operation_id);
CREATE INDEX IF NOT EXISTS external_attempt_parent ON external_attempts(parent_id);
"""

_CLEANUP_MIN_INTERVAL = 300.0
_CLEANUP_WRITE_BATCH = 200


class ExternalRequestStore:
    """只保存脱敏后的请求观测记录与写入未知核查，不维护持久阻断状态机。"""

    def __init__(self, path, *, retention_days=30, max_records=100_000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = retention_days * 86400
        self.max_records = max_records
        self.migrated_account_blocks: list[dict] = []
        self._cleanup_lock = threading.Lock()
        self._writes_since_cleanup = 0
        self._last_cleanup = 0.0
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError("外部请求审计库版本不受支持，禁止覆盖")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA journal_size_limit=8388608")
            conn.executescript(EXTERNAL_REQUEST_SCHEMA_SQL)
            if version in (0, 1, 2):
                conn.execute("BEGIN IMMEDIATE")
                self._migrate_to_v3(conn)
                conn.commit()
        self.path.chmod(0o600)

    def _migrate_to_v3(self, conn: sqlite3.Connection) -> None:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(external_attempts)")}
        if "fingerprint" not in columns:
            conn.execute("ALTER TABLE external_attempts ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        if "external_blocks" in tables:
            for row in conn.execute("SELECT platform, account_id, scope, scope_key, failure FROM external_blocks WHERE scope='account'"):
                try:
                    failure = json.loads(row["failure"])
                except (TypeError, ValueError):
                    continue
                if isinstance(failure, dict) and failure.get("resume_at") is None and str(failure.get("code") or "").endswith("_ACCOUNT_DISABLED"):
                    self.migrated_account_blocks.append({
                        "platform": row["platform"],
                        "account_id": row["account_id"],
                        "code": str(failure.get("code") or ""),
                        "message": str(failure.get("message") or ""),
                    })
        for retired in ("external_blocks", "external_policies", "external_recoveries"):
            if retired in tables:
                conn.execute(f'DROP TABLE "{retired}"')
        conn.execute("PRAGMA user_version=3")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _maybe_cleanup(self, conn: sqlite3.Connection, now: float) -> None:
        with self._cleanup_lock:
            self._writes_since_cleanup += 1
            if self._writes_since_cleanup < _CLEANUP_WRITE_BATCH and (now - self._last_cleanup) < _CLEANUP_MIN_INTERVAL:
                return
            self._writes_since_cleanup = 0
            self._last_cleanup = now
        cutoff = now - self.retention_seconds
        conn.execute(
            """UPDATE external_attempts SET completed=?,
               result=json_object('outcome', CASE WHEN sent=1 AND semantics='write' THEN 'outcome_unknown' ELSE 'interrupted' END)
               WHERE completed IS NULL AND created<?""",
            (now, cutoff),
        )
        conn.execute("DELETE FROM external_attempts WHERE completed IS NOT NULL AND created<?", (cutoff,))
        conn.execute(
            """DELETE FROM external_attempts WHERE id IN
               (SELECT id FROM external_attempts WHERE completed IS NOT NULL ORDER BY created DESC LIMIT -1 OFFSET ?)""",
            (self.max_records,),
        )

    def begin(self, ctx: RequestContext, method: str, *, parent_id="", attempt=1) -> str:
        request_id = uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO external_attempts
                   (id, operation_id, parent_id, attempt, platform, account_id, credential_id,
                    interface, source, trigger, quota_key, method, semantics, fingerprint, created, decision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting')""",
                (
                    request_id, ctx.operation_id, parent_id, attempt, ctx.platform, ctx.account_id,
                    ctx.credential_id, ctx.interface, ctx.source, "retry" if attempt > 1 else ctx.trigger,
                    ctx.quota_key, method, ctx.semantics, ctx.fingerprint, now,
                ),
            )
            self._maybe_cleanup(conn, now)
            conn.commit()
        return request_id

    def mark_sent(self, request_id: str, released_at: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE external_attempts SET decision='allowed', sent=1, released=? WHERE id=?",
                (released_at, request_id),
            )
            conn.commit()

    def finish(self, request_id: str, *, decision=None, result=None, not_sent=False) -> None:
        # result 仅接收管理器构造的白名单摘要，禁止传平台响应对象。
        with self._connect() as conn:
            conn.execute(
                """UPDATE external_attempts
                   SET completed=?,
                       decision=COALESCE(?, decision),
                       result=json_patch(result, ?),
                       sent=CASE WHEN ? THEN 0 ELSE sent END,
                       released=CASE WHEN ? THEN NULL ELSE released END
                   WHERE id=?""",
                (time.time(), decision, json.dumps(result or {}, ensure_ascii=False), not_sent, not_sent, request_id),
            )
            conn.commit()

    def has_unknown_write(self, ctx: RequestContext, *, window_seconds: float = 86400) -> bool:
        """同操作同指纹的写入若已发生结果未知，跨进程重启也禁止直接重放。"""
        if not ctx.fingerprint:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM external_attempts
                   WHERE platform=? AND account_id=? AND operation_id=? AND interface=?
                     AND fingerprint=? AND semantics='write' AND sent=1
                     AND json_extract(result, '$.outcome')='outcome_unknown'
                     AND created>?
                   LIMIT 1""",
                (ctx.platform, ctx.account_id, ctx.operation_id, ctx.interface, ctx.fingerprint, time.time() - window_seconds),
            ).fetchone()
        return row is not None

    def has_credential_read_success(self, platform: str, account_id: str, credential_id: str, interface: str) -> bool:
        """核对同一凭据此前是否在该只读接口成功过（辅助识别 Ozon 凭据突遭停用）。"""
        if not credential_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM external_attempts
                   WHERE platform=? AND account_id=? AND credential_id=? AND interface=?
                     AND semantics='read' AND sent=1 AND json_extract(result, '$.outcome')='success'
                   LIMIT 1""",
                (platform, account_id, credential_id, interface),
            ).fetchone()
        return row is not None

    def query(self, *, limit=100, since=0, until=None, **filters):
        allowed = {key: key for key in ("platform", "account_id", "interface", "operation_id", "decision")}
        allowed.update(outcome="json_extract(result,'$.outcome')", http_status="json_extract(result,'$.http_status')")
        if filters.keys() - allowed.keys():
            raise ValueError("不支持的审计查询条件")
        where = "created>=? AND created<=?" + "".join(f" AND {allowed[k]}=?" for k in filters)
        args = [since, until or time.time(), *filters.values()]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM external_attempts WHERE " + where + " ORDER BY created DESC LIMIT ?",
                (*args, max(1, min(int(limit), 1000))),
            ).fetchall()
            stats = conn.execute(
                """SELECT COUNT(*) AS attempts,
                          COUNT(DISTINCT operation_id) AS operations,
                          COALESCE(SUM(sent), 0) AS network_attempts,
                          COALESCE(SUM(decision='rejected'), 0) AS local_rejections
                   FROM external_attempts WHERE """ + where,
                args,
            ).fetchone()
        return {
            "items": [
                {
                    **dict(r),
                    "result": json.loads(r["result"]),
                    "queue_seconds": (r["released"] or r["completed"] or time.time()) - r["created"],
                    "network_seconds": (r["completed"] - r["released"]) if r["completed"] and r["released"] else None,
                }
                for r in rows
            ],
            "stats": dict(stats),
        }
