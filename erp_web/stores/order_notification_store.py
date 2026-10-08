"""通知收件箱、订单最新快照和重试租约的唯一持久化入口。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from erp_web.schemas.orders import (
    PLATFORMS,
    OrderEvent,
    OrderSnapshot,
    OrdersPage,
    timestamp,
    utc_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL UNIQUE,
 platform TEXT NOT NULL, account_id TEXT NOT NULL, topic TEXT NOT NULL,
 event_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, claim TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS inbox_due ON inbox(status,next_attempt,lease_until);
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, platform TEXT NOT NULL, account_id TEXT NOT NULL, state TEXT NOT NULL,
 snapshot TEXT NOT NULL, remote_time REAL NOT NULL, checked_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_scope ON orders(platform,account_id,state);
CREATE TABLE IF NOT EXISTS alerts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, platform TEXT NOT NULL,
 account_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS order_address_notes (
 platform TEXT NOT NULL, account_id TEXT NOT NULL, address_key TEXT NOT NULL,
 address TEXT NOT NULL, note TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(platform,account_id,address_key));
"""


class OrderNotificationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("订单通知库版本不受支持")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for platform in PLATFORMS:
                conn.execute(
                    "INSERT OR IGNORE INTO settings VALUES (?,?)",
                    ("token:" + platform, secrets.token_urlsafe(32)),
                )
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, timeout: float = 5):
        conn = sqlite3.connect(self.path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def setting(self, key: str) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else ""

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
            conn.commit()

    @staticmethod
    def _insert(conn, event: OrderEvent) -> int:
        conn.execute(
            """INSERT OR IGNORE INTO inbox
            (dedup_key,platform,account_id,topic,event_json,received_at) VALUES (?,?,?,?,?,?)""",
            (
                event.dedup_key,
                event.platform,
                event.account_id,
                event.topic,
                event.model_dump_json(),
                utc_iso(),
            ),
        )
        return conn.execute(
            "SELECT id FROM inbox WHERE dedup_key=?", (event.dedup_key,)
        ).fetchone()[0]

    def enqueue(self, event: OrderEvent) -> int:
        with self.connect() as conn:
            result = self._insert(conn, event)
            conn.commit()
            return result

    def receive(self, platform: str, event: OrderEvent | None) -> None:
        # 回调有严格应答时限；写锁拥堵时迅速失败，让平台重投，不能假报接收成功。
        with self.connect(timeout=0.2) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event is not None:
                self._insert(conn, event)
            conn.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                ("last_received:" + platform, utc_iso()),
            )
            conn.commit()

    def schedule(
        self, accounts: dict[str, str], *, now: float, automatic: bool = False, automatic_interval_seconds: int = 5 * 3600
    ) -> list[str]:
        operation_ids = []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for platform, account in accounts.items():
                if automatic:
                    key = f"auto_sync_at:{platform}:{account}"
                    previous = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                    if previous and now - float(previous[0]) < automatic_interval_seconds:
                        continue
                    # 与任务入队同事务记录尝试时间，失败也计入冷却，跨窗口不重复提交。
                    conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(now)))
                active = conn.execute(
                    "SELECT id,status,lease_until FROM inbox WHERE platform=? AND account_id=? AND topic='sync' AND status IN ('queued','running','retry')",
                    (platform, account),
                ).fetchone()
                if active:
                    operation_ids.append(f"orders:{active['id']}")
                    if active["status"] == "retry" or (active["status"] == "running" and active["lease_until"] <= now):
                        conn.execute("""UPDATE inbox SET status='queued',attempts=0,next_attempt=0,
                            error='',lease_until=0,claim='' WHERE id=?""", (active["id"],))
                    continue
                event_id = self._insert(
                    conn,
                    OrderEvent(
                        platform=platform,
                        account_id=account,
                        topic="sync",
                        resource="",
                        payload={"run": uuid4().hex},
                    ),
                )
                operation_ids.append(f"orders:{event_id}")
            conn.commit()
        return operation_ids

    def claim(self, accounts: dict[str, str], *, now: float):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # 历史主动同步重试和中断的查询不再自动恢复，等待页面或按钮重新发起。
            conn.execute("""UPDATE inbox SET status='failed',next_attempt=0,lease_until=0,
                error='上次同步未完成，请点击同步订单重新获取。'
                WHERE topic='sync' AND (status='retry' OR (status='running' AND lease_until<=?))""", (now,))
            # 每个账号同一时刻只处理一个任务；回调失效租约可重领，claim fencing 拒绝迟到提交。
            scope = (
                " OR ".join("(platform=? AND account_id=?)" for _ in accounts) or "0"
            )
            args = tuple(value for pair in accounts.items() for value in pair)
            rows = conn.execute(
                f"""SELECT * FROM inbox WHERE ({scope}) AND (
                (status IN ('queued','retry') AND next_attempt<=?) OR (status='running' AND lease_until<=?))
                ORDER BY CASE WHEN topic='sync' THEN 1 ELSE 0 END,id LIMIT 100""",
                (*args, now, now),
            ).fetchall()
            for row in rows:
                if accounts.get(row["platform"]) != row["account_id"]:
                    continue
                busy = conn.execute(
                    "SELECT 1 FROM inbox WHERE platform=? AND account_id=? AND status='running' AND lease_until>?",
                    (row["platform"], row["account_id"], now),
                ).fetchone()
                if busy:
                    continue
                claim = uuid4().hex
                conn.execute(
                    "UPDATE inbox SET status='running',attempts=attempts+1,lease_until=?,claim=? WHERE id=?",
                    (now + 180, claim, row["id"]),
                )
                conn.commit()
                return {**dict(row), "claim": claim, "attempts": row["attempts"] + 1}
            conn.commit()
        return None

    def renew_claim(self, job, *, now: float) -> bool:
        """在逐笔采购和仓库查询之间续租，不恢复已失效或被替换的任务。"""
        with self.connect() as conn:
            changed = conn.execute("""UPDATE inbox SET lease_until=?
                WHERE id=? AND claim=? AND status='running' AND lease_until>?""",
                (now + 180, job["id"], job["claim"], now)).rowcount
            conn.commit()
        return changed == 1

    def save_snapshot(self, job, snapshot: OrderSnapshot, *, now: float) -> bool:
        remote_time = timestamp(snapshot.updated_at)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM inbox WHERE id=? AND claim=? AND status='running' AND lease_until>?",
                (job["id"], job["claim"], now),
            ).fetchone():
                return False
            conn.execute(
                "UPDATE inbox SET lease_until=? WHERE id=? AND claim=?",
                (now + 180, job["id"], job["claim"]),
            )
            previous = conn.execute(
                "SELECT * FROM orders WHERE id=?", (snapshot.identity,)
            ).fetchone()
            if previous and remote_time and remote_time < previous["remote_time"]:
                conn.commit()
                return True
            # 缺少平台版本的快照不能覆盖已有带版本的事实。
            if previous and previous["remote_time"] and not remote_time:
                conn.commit()
                return True
            checked = utc_iso()
            conn.execute(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot.identity,
                    snapshot.platform,
                    snapshot.account_id,
                    snapshot.state,
                    snapshot.model_dump_json(),
                    remote_time,
                    checked,
                ),
            )
            if snapshot.state == "pending_shipment" and (
                not previous or previous["state"] != "pending_shipment"
            ):
                conn.execute(
                    "INSERT INTO alerts(order_id,platform,account_id,title,created_at) VALUES (?,?,?,?,?)",
                    (
                        snapshot.identity,
                        snapshot.platform,
                        snapshot.account_id,
                        snapshot.title or snapshot.order_id,
                        checked,
                    ),
                )
            if snapshot.state != "pending_shipment":
                conn.execute(
                    "UPDATE alerts SET read_at=? WHERE order_id=? AND read_at=''",
                    (checked, snapshot.identity),
                )
            conn.commit()
            return True

    def finish(self, job, *, error: str = "", retryable: bool = False, now: float, next_attempt: float | None = None):
        status = (
            "done"
            if not error
            else "retry"
            if retryable and (job["attempts"] < 8 or next_attempt is not None)
            else "failed"
        )
        with self.connect() as conn:
            conn.execute(
                """UPDATE inbox SET status=?,error=?,next_attempt=?,lease_until=0,completed_at=?
                WHERE id=? AND claim=? AND status='running' AND lease_until>? """,
                (
                    status,
                    error[:500],
                    next_attempt if next_attempt is not None else now + min(1800, 5 * 2 ** min(job["attempts"], 9)),
                    utc_iso(),
                    job["id"],
                    job["claim"],
                    now,
                ),
            )
            conn.commit()

    def retry(self, event_id: int, accounts: dict[str, str]) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT platform,account_id,status FROM inbox WHERE id=?", (event_id,)
            ).fetchone()
            if not row or accounts.get(row["platform"]) != row["account_id"]:
                raise ValueError("通知不存在或不属于当前账号")
            if row["status"] not in ("retry", "failed"):
                raise ValueError("只有失败或等待重试的通知可以重试")
            conn.execute(
                "UPDATE inbox SET status='queued',attempts=0,next_attempt=0,error='' WHERE id=?",
                (event_id,),
            )
            conn.commit()

    def read(
        self,
        accounts: dict[str, str],
        *,
        platform: str = "",
        state: str = "",
        offset: int = 0,
        limit: int = 50,
        query: str = "",
    ):
        scopes = list(accounts.items())
        where = " OR ".join("(platform=? AND account_id=?)" for _ in scopes) or "0"
        args = tuple(value for pair in scopes for value in pair)
        with self.connect() as conn:
            conn.execute("BEGIN")
            counts = {
                row["state"]: row["n"]
                for row in conn.execute(
                    f"SELECT state,COUNT(*) n FROM orders WHERE {where} GROUP BY state",
                    args,
                )
            }
            filtered = (
                f"({where})"
                + (" AND state=?" if state else "")
                + (" AND platform=?" if platform else "")
                + (" AND (instr(lower(snapshot),lower(?))>0)" if query else "")
            )
            filtered_args = (
                args
                + ((state,) if state else ())
                + ((platform,) if platform else ())
                + ((query,) if query else ())
            )
            total = conn.execute(
                f"SELECT COUNT(*) FROM orders WHERE {filtered}", filtered_args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM orders WHERE {filtered} ORDER BY checked_at DESC,id LIMIT ? OFFSET ?",
                (*filtered_args, limit, offset),
            ).fetchall()
            events = conn.execute(
                f"SELECT id,platform,topic,status,attempts,error,received_at,next_attempt FROM inbox WHERE ({where}) ORDER BY id DESC LIMIT 50",
                args,
            ).fetchall()
            alerts = conn.execute(
                f"SELECT * FROM alerts WHERE ({where}) AND read_at='' ORDER BY id DESC LIMIT 50",
                args,
            ).fetchall()
            unread = conn.execute(
                f"SELECT COUNT(*) FROM alerts WHERE ({where}) AND read_at=''", args
            ).fetchone()[0]
            latest = conn.execute(
                f"SELECT COALESCE(MAX(id),0) FROM alerts WHERE {where}", args
            ).fetchone()[0]
        return OrdersPage.model_validate(
            {
                "ok": True,
                "items": [
                    {
                        **json.loads(row["snapshot"]),
                        "id": row["id"],
                        "checked_at": row["checked_at"],
                    }
                    for row in rows
                ],
                "total": total,
                "counts": counts,
                "notifications": [dict(row) for row in events],
                "alerts": [dict(row) for row in alerts],
                "unread": unread,
                "latest_alert_id": latest,
            }
        ).model_dump(mode="json")

    def sync_status(self, accounts):
        result = []
        with self.connect() as conn:
            for platform, account in accounts.items():
                latest = conn.execute("SELECT id,status,error,next_attempt,completed_at FROM inbox WHERE platform=? AND account_id=? AND topic='sync' ORDER BY id DESC LIMIT 1", (platform,account)).fetchone()
                success = conn.execute("SELECT completed_at FROM inbox WHERE platform=? AND account_id=? AND topic='sync' AND status='done' ORDER BY completed_at DESC LIMIT 1", (platform,account)).fetchone()
                result.append({"platform": platform, "status": latest["status"] if latest else "idle",
                               "error": latest["error"] if latest else "",
                               "last_success_at": success[0] if success else "",
                               "next_attempt": 0})
        return result

    def summary(self, accounts):
        page = self.read(accounts, limit=5)
        where = " OR ".join("(platform=? AND account_id=?)" for _ in accounts) or "0"
        args = tuple(value for pair in accounts.items() for value in pair)
        with self.connect() as conn:
            attention = conn.execute(
                f"SELECT COUNT(*) FROM inbox WHERE ({where}) AND status IN ('failed','retry')",
                args,
            ).fetchone()[0]
        return {
            "ok": True,
            "counts": page["counts"],
            "unread": page["unread"],
            "alerts": page["alerts"][:5],
            "latest_alert_id": page["latest_alert_id"],
            "recent": [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "platform",
                        "order_id",
                        "title",
                        "state",
                        "amount",
                        "currency",
                    )
                }
                for row in page["items"]
            ],
            "attention_count": attention,
        }

    def tracked(self, platform: str, account: str) -> list[OrderSnapshot]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT snapshot FROM orders WHERE platform=? AND account_id=? AND state NOT IN ('delivered','cancelled')",
                (platform, account),
            ).fetchall()
        return [OrderSnapshot.model_validate_json(row[0]) for row in rows]

    def acknowledge(self, through_id: int, accounts: dict[str, str]):
        with self.connect() as conn:
            for platform, account in accounts.items():
                conn.execute(
                    "UPDATE alerts SET read_at=? WHERE platform=? AND account_id=? AND id<=? AND read_at=''",
                    (utc_iso(), platform, account, through_id),
                )
            conn.commit()
