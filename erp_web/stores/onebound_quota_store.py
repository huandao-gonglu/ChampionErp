"""万邦 1688 企业共享额度账本：原子准入扣减、Key 快照与持久停用状态。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure
from erp_web.schemas.onebound_quota import (
    DAILY_RESERVE_MIN,
    DEFAULT_INITIAL_GRANT,
    MAX_TARGET_BALANCE,
)

_CST = timezone(timedelta(hours=8))


class QuotaBalanceConflictError(ValueError):
    """管理员调整目标余额时，买家真实余额已发生变化。"""

    def __init__(self, current_balance: int) -> None:
        self.current_balance = current_balance
        super().__init__(f"该买家当前余额已变更为 {current_balance} 次，请刷新后重新确认目标余额。")


def cst_date(timestamp: float) -> date:
    return datetime.fromtimestamp(timestamp, _CST).date()


def cst_day_start(timestamp: float) -> float:
    day = cst_date(timestamp)
    return datetime.combine(day, datetime.min.time(), _CST).timestamp()


def next_cst_midnight(timestamp: float) -> float:
    day = cst_date(timestamp) + timedelta(days=1)
    return datetime.combine(day, datetime.min.time(), _CST).timestamp()


def reserve_quota(daily_max: int | None) -> int:
    if not daily_max or daily_max <= 0:
        return DAILY_RESERVE_MIN
    return max(DAILY_RESERVE_MIN, daily_max * 5 // 100)


class OneBoundQuotaStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("万邦额度库版本不受支持")
            conn.execute("PRAGMA journal_mode=WAL")
            if version == 1:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ALTER TABLE quota_entries RENAME TO quota_entries_v1")
                self._create_quota_entries_table(conn)
                conn.execute(
                    "INSERT INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, note, created) "
                    "SELECT id, key_fp, member_id, kind, category, amount, ref, '', created FROM quota_entries_v1"
                )
                conn.execute("DROP TABLE quota_entries_v1")
            else:
                self._create_quota_entries_table(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_onebound_entries_buyer "
                "ON quota_entries(key_fp, member_id, created DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_onebound_entries_key_created "
                "ON quota_entries(key_fp, kind, created DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS key_snapshots (
                    key_fp TEXT PRIMARY KEY,
                    today INTEGER,
                    max INTEGER,
                    expires TEXT NOT NULL DEFAULT '',
                    raw_all TEXT NOT NULL DEFAULT '',
                    observed_at REAL NOT NULL,
                    observed_rowid INTEGER NOT NULL DEFAULT 0,
                    request_id TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS key_state (
                    key_fp TEXT PRIMARY KEY,
                    disabled_code TEXT NOT NULL DEFAULT '',
                    disabled_message TEXT NOT NULL DEFAULT '',
                    disabled_at REAL,
                    exhausted_until REAL
                )"""
            )
            conn.execute("PRAGMA user_version=2")
            conn.commit()

    @staticmethod
    def _create_quota_entries_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS quota_entries (
                id TEXT PRIMARY KEY,
                key_fp TEXT NOT NULL,
                member_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('grant','earn','spend','adjust')),
                category TEXT NOT NULL DEFAULT '',
                amount INTEGER NOT NULL,
                ref TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL,
                UNIQUE(key_fp, kind, ref)
            )"""
        )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    def _today_estimated_unlocked(self, conn: sqlite3.Connection, key_fp: str, snapshot, now: float) -> int:
        day_start = cst_day_start(now)
        if snapshot is None or snapshot["today"] is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM quota_entries WHERE key_fp=? AND kind='spend' AND created>=?",
                (key_fp, day_start),
            ).fetchone()
            return int(row[0] or 0)
        observed_at = float(snapshot["observed_at"] or 0.0)
        if cst_date(observed_at) < cst_date(now):
            # 快照来自上一个北京时间自然日，今日估算仅计入今日已发出的请求。
            row = conn.execute(
                "SELECT COUNT(*) FROM quota_entries WHERE key_fp=? AND kind='spend' AND created>=?",
                (key_fp, day_start),
            ).fetchone()
            return int(row[0] or 0)
        observed_rowid = int(snapshot["observed_rowid"] or 0)
        row = conn.execute(
            "SELECT COUNT(*) FROM quota_entries WHERE key_fp=? AND kind='spend' AND rowid>?",
            (key_fp, observed_rowid),
        ).fetchone()
        return int(snapshot["today"]) + int(row[0] or 0)

    @staticmethod
    def _expiry_state(expires: str, now: float) -> tuple[int | None, bool, bool]:
        if not expires:
            return None, False, False
        try:
            expiry_day = date.fromisoformat(expires)
        except ValueError:
            return None, False, False
        days_left = (expiry_day - cst_date(now)).days
        return days_left, 0 <= days_left <= 7, days_left < 0

    def ensure_initial_grant(
        self,
        key_fp: str,
        member_id: str,
        *,
        initial_grant: int = DEFAULT_INITIAL_GRANT,
        now: float | None = None,
    ) -> bool:
        if not key_fp or not member_id:
            return False
        now = time.time() if now is None else now
        grant_amount = max(0, int(initial_grant))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                "VALUES (?, ?, ?, 'grant', '', ?, ?, ?)",
                (uuid4().hex, key_fp, member_id, grant_amount, f"grant:{member_id}", now),
            )
            conn.commit()
            return cursor.rowcount > 0

    def admit_and_spend(
        self,
        key_fp: str,
        member_id: str,
        category: str,
        *,
        ref: str = "",
        initial_grant: int = DEFAULT_INITIAL_GRANT,
        now: float | None = None,
    ) -> str:
        """在同一写事务内完成平台层/买家层准入检查并先扣后发。"""
        if not key_fp:
            raise ValueError("缺少万邦企业 Key 指纹")
        if category not in {"trade", "auth", "collect"}:
            category = "collect"
        now = time.time() if now is None else now
        spend_ref = ref or uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute("SELECT * FROM key_state WHERE key_fp=?", (key_fp,)).fetchone()
            if state is not None and state["disabled_code"]:
                conn.rollback()
                message = state["disabled_message"] or "万邦企业 Key 已暂停使用，请管理员核对后在设置中恢复"
                raise ExternalRequestBlocked(RequestFailure(state["disabled_code"], message, "account"))

            snapshot = conn.execute("SELECT * FROM key_snapshots WHERE key_fp=?", (key_fp,)).fetchone()
            expires = str(snapshot["expires"] or "") if snapshot is not None else ""
            _, _, expired = self._expiry_state(expires, now)
            if expired:
                conn.rollback()
                raise ExternalRequestBlocked(
                    RequestFailure(
                        "ONEBOUND_KEY_EXPIRED",
                        f"万邦企业 Key 已于 {expires} 到期，请联系管理员续费或更换 Key",
                        "account",
                    )
                )

            if state is not None and state["exhausted_until"] is not None and float(state["exhausted_until"]) > now:
                until = float(state["exhausted_until"])
                conn.rollback()
                raise ExternalRequestBlocked(
                    RequestFailure(
                        "ONEBOUND_DAILY_RATE_LIMITED",
                        "万邦今日调用次数已达上限，将于次日 00:00（北京时间）自动恢复",
                        "account",
                        resume_at=until,
                    )
                )

            daily_max = int(snapshot["max"]) if snapshot is not None and snapshot["max"] is not None else None
            if daily_max is not None and daily_max > 0:
                today_est = self._today_estimated_unlocked(conn, key_fp, snapshot, now)
                if today_est >= daily_max:
                    until = next_cst_midnight(now)
                    conn.rollback()
                    raise ExternalRequestBlocked(
                        RequestFailure(
                            "ONEBOUND_DAILY_RATE_LIMITED",
                            f"万邦今日调用量已达上限（{today_est}/{daily_max}），将于次日 00:00（北京时间）自动恢复",
                            "account",
                            resume_at=until,
                        )
                    )
                reserve = reserve_quota(daily_max)
                if category == "collect" and today_est >= max(0, daily_max - reserve):
                    conn.rollback()
                    raise ExternalRequestBlocked(
                        RequestFailure(
                            "ONEBOUND_DAILY_RESERVE_PROTECTED",
                            f"万邦今日调用量接近上限（{today_est}/{daily_max}），剩余 {reserve} 次保留给交易与授权调用；请改用浏览器采集或明日再试",
                            "request",
                        )
                    )

            if member_id:
                grant_amount = max(0, int(initial_grant))
                conn.execute(
                    "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                    "VALUES (?, ?, ?, 'grant', '', ?, ?, ?)",
                    (uuid4().hex, key_fp, member_id, grant_amount, f"grant:{member_id}", now),
                )
                if category == "collect":
                    balance = int(
                        conn.execute(
                            "SELECT COALESCE(SUM(amount), 0) FROM quota_entries WHERE key_fp=? AND member_id=?",
                            (key_fp, member_id),
                        ).fetchone()[0]
                        or 0
                    )
                    if balance <= 0:
                        conn.rollback()
                        raise ExternalRequestBlocked(
                            RequestFailure(
                                "ONEBOUND_QUOTA_EXHAUSTED",
                                f"万邦采集额度不足（余额 {balance} 次）。可以改用浏览器采集，或经万邦下单付款后获得额度",
                                "request",
                            )
                        )

            conn.execute(
                "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                "VALUES (?, ?, ?, 'spend', ?, -1, ?, ?)",
                (uuid4().hex, key_fp, member_id, category, spend_ref, now),
            )
            conn.commit()
            return spend_ref

    def credit_earn(
        self,
        key_fp: str,
        member_id: str,
        order_number: str,
        amount: int,
        *,
        initial_grant: int = DEFAULT_INITIAL_GRANT,
        now: float | None = None,
    ) -> bool:
        if not key_fp or not member_id or not order_number or amount <= 0:
            return False
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                "VALUES (?, ?, ?, 'grant', '', ?, ?, ?)",
                (uuid4().hex, key_fp, member_id, max(0, int(initial_grant)), f"grant:{member_id}", now),
            )
            cursor = conn.execute(
                "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                "VALUES (?, ?, ?, 'earn', '', ?, ?, ?)",
                (uuid4().hex, key_fp, member_id, int(amount), str(order_number), now),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_target_balance(
        self,
        key_fp: str,
        member_id: str,
        target_balance: int,
        *,
        expected_balance: int | None = None,
        note: str = "",
        initial_grant: int = DEFAULT_INITIAL_GRANT,
        now: float | None = None,
    ) -> dict:
        """在同一事务内将买家余额调整至指定目标值，差值记为 adjust 流水。"""
        if not key_fp or not member_id:
            raise ValueError("缺少万邦企业 Key 或 1688 买家标识")
        if isinstance(target_balance, bool) or not isinstance(target_balance, int) or not 0 <= target_balance <= MAX_TARGET_BALANCE:
            raise ValueError(f"目标余额须为 0 至 {MAX_TARGET_BALANCE} 的整数")
        if expected_balance is not None and (isinstance(expected_balance, bool) or not isinstance(expected_balance, int)):
            raise ValueError("预期余额必须是整数")
        clean_note = str(note or "").strip()[:200]
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, created) "
                "VALUES (?, ?, ?, 'grant', '', ?, ?, ?)",
                (uuid4().hex, key_fp, member_id, max(0, int(initial_grant)), f"grant:{member_id}", now),
            )
            current_balance = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM quota_entries WHERE key_fp=? AND member_id=?",
                    (key_fp, member_id),
                ).fetchone()[0]
                or 0
            )
            if expected_balance is not None and current_balance != expected_balance:
                conn.rollback()
                raise QuotaBalanceConflictError(current_balance)
            delta = target_balance - current_balance
            if delta != 0:
                entry_id = uuid4().hex
                conn.execute(
                    "INSERT INTO quota_entries(id, key_fp, member_id, kind, category, amount, ref, note, created) "
                    "VALUES (?, ?, ?, 'adjust', '', ?, ?, ?, ?)",
                    (entry_id, key_fp, member_id, delta, f"adjust:{entry_id}", clean_note, now),
                )
            conn.commit()
            return {
                "changed": delta != 0,
                "previous_balance": current_balance,
                "delta": delta,
                "balance": target_balance,
            }

    def observe_snapshot(
        self,
        key_fp: str,
        *,
        today: int | None,
        daily_max: int | None,
        expires: str,
        raw_all: str = "",
        request_id: str = "",
        now: float | None = None,
    ) -> None:
        if not key_fp or (today is None and daily_max is None and not expires):
            return
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM key_snapshots WHERE key_fp=?", (key_fp,)).fetchone()
            max_rowid = int(
                conn.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM quota_entries WHERE key_fp=?",
                    (key_fp,),
                ).fetchone()[0]
                or 0
            )
            merged_today = today if today is not None else (existing["today"] if existing is not None else None)
            merged_max = daily_max if daily_max is not None else (existing["max"] if existing is not None else None)
            merged_expires = expires or (str(existing["expires"] or "") if existing is not None else "")
            merged_raw_all = raw_all or (str(existing["raw_all"] or "") if existing is not None else "")
            conn.execute(
                """INSERT INTO key_snapshots(key_fp, today, max, expires, raw_all, observed_at, observed_rowid, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_fp) DO UPDATE SET
                    today=excluded.today,
                    max=excluded.max,
                    expires=excluded.expires,
                    raw_all=excluded.raw_all,
                    observed_at=excluded.observed_at,
                    observed_rowid=excluded.observed_rowid,
                    request_id=excluded.request_id""",
                (key_fp, merged_today, merged_max, merged_expires, merged_raw_all[:200], now, max_rowid, request_id[:128]),
            )
            # 若新快照显示 today 已重置且小于 max，自动清除旧的当日耗尽标记。
            if (
                existing is not None
                and today is not None
                and existing["today"] is not None
                and today < int(existing["today"])
                and (merged_max is None or today < int(merged_max))
            ):
                conn.execute("UPDATE key_state SET exhausted_until=NULL WHERE key_fp=?", (key_fp,))
            conn.commit()

    def disable_key(self, key_fp: str, *, code: str, message: str, now: float | None = None) -> None:
        if not key_fp:
            return
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO key_state(key_fp, disabled_code, disabled_message, disabled_at, exhausted_until)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(key_fp) DO UPDATE SET
                    disabled_code=excluded.disabled_code,
                    disabled_message=excluded.disabled_message,
                    disabled_at=excluded.disabled_at""",
                (key_fp, code[:80], message[:400], now),
            )
            conn.commit()

    def mark_daily_exhausted(self, key_fp: str, *, until: float) -> None:
        if not key_fp:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO key_state(key_fp, disabled_code, disabled_message, disabled_at, exhausted_until)
                VALUES (?, '', '', NULL, ?)
                ON CONFLICT(key_fp) DO UPDATE SET
                    exhausted_until=CASE
                        WHEN key_state.exhausted_until IS NULL OR excluded.exhausted_until > key_state.exhausted_until
                        THEN excluded.exhausted_until
                        ELSE key_state.exhausted_until
                    END""",
                (key_fp, float(until)),
            )
            conn.commit()

    def restore_key(self, key_fp: str) -> bool:
        if not key_fp:
            return False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT disabled_code, exhausted_until FROM key_state WHERE key_fp=?", (key_fp,)).fetchone()
            had_state = bool(existing is not None and (existing["disabled_code"] or existing["exhausted_until"] is not None))
            conn.execute(
                "UPDATE key_state SET disabled_code='', disabled_message='', disabled_at=NULL, exhausted_until=NULL WHERE key_fp=?",
                (key_fp,),
            )
            conn.commit()
            return had_state

    def read_buyer_summary(
        self,
        key_fp: str,
        member_id: str,
        *,
        account_name: str = "",
        initial_grant: int = DEFAULT_INITIAL_GRANT,
    ) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, category, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt "
                "FROM quota_entries WHERE key_fp=? AND member_id=? GROUP BY kind, category",
                (key_fp, member_id),
            ).fetchall()
        return self._aggregate_buyer_rows(member_id, rows, account_name=account_name, initial_grant=initial_grant)

    def read_all_buyers(
        self,
        key_fp: str,
        *,
        current_member_id: str = "",
        current_account_name: str = "",
        initial_grant: int = DEFAULT_INITIAL_GRANT,
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT member_id, kind, category, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt "
                "FROM quota_entries WHERE key_fp=? AND member_id<>'' GROUP BY member_id, kind, category",
                (key_fp,),
            ).fetchall()
        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(str(row["member_id"]), []).append(row)
        if current_member_id and current_member_id not in grouped:
            grouped[current_member_id] = []
        result = [
            self._aggregate_buyer_rows(
                mid,
                member_rows,
                account_name=current_account_name if mid == current_member_id else "",
                initial_grant=initial_grant,
            )
            for mid, member_rows in sorted(grouped.items())
        ]
        return result

    @staticmethod
    def _aggregate_buyer_rows(
        member_id: str,
        rows,
        *,
        account_name: str = "",
        initial_grant: int = DEFAULT_INITIAL_GRANT,
    ) -> dict:
        granted = 0
        has_grant = False
        earned = 0
        adjusted = 0
        spent_trade = 0
        spent_auth = 0
        spent_collect = 0
        spent_total = 0
        for row in rows:
            kind = str(row["kind"])
            category = str(row["category"] or "")
            total = int(row["total"] or 0)
            if kind == "grant":
                granted += total
                has_grant = True
            elif kind == "earn":
                earned += total
            elif kind == "adjust":
                adjusted += total
            elif kind == "spend":
                spent = -total
                spent_total += spent
                if category == "trade":
                    spent_trade += spent
                elif category == "auth":
                    spent_auth += spent
                else:
                    spent_collect += spent
        if not has_grant and member_id:
            # 尚未写入 grant 记录时，只读视图按当前默认基础额度投影，不产生写副作用。
            granted = max(0, int(initial_grant))
        balance = granted + earned + adjusted - spent_total
        return {
            "member_id": member_id,
            "account_name": account_name or member_id,
            "balance": balance,
            "granted": granted,
            "earned": earned,
            "adjusted": adjusted,
            "spent_total": spent_total,
            "spent_trade": spent_trade,
            "spent_auth": spent_auth,
            "spent_collect": spent_collect,
        }

    def read_key_status(
        self,
        key_fp: str,
        *,
        configured: bool,
        initial_grant: int = DEFAULT_INITIAL_GRANT,
        now: float | None = None,
    ) -> dict:
        now = time.time() if now is None else now
        if not configured or not key_fp:
            return {
                "configured": False,
                "today_snapshot": None,
                "today_estimated": 0,
                "daily_max": None,
                "reserve": DAILY_RESERVE_MIN,
                "expires": "",
                "days_until_expiry": None,
                "expiring_soon": False,
                "expired": False,
                "observed_at": None,
                "disabled": False,
                "disabled_code": "",
                "disabled_message": "",
                "disabled_at": None,
                "exhausted_until": None,
                "initial_grant": max(0, int(initial_grant)),
            }
        with self._connect() as conn:
            snapshot = conn.execute("SELECT * FROM key_snapshots WHERE key_fp=?", (key_fp,)).fetchone()
            state = conn.execute("SELECT * FROM key_state WHERE key_fp=?", (key_fp,)).fetchone()
            today_estimated = self._today_estimated_unlocked(conn, key_fp, snapshot, now)
        today_snapshot = None
        daily_max = None
        expires = ""
        observed_at = None
        if snapshot is not None:
            observed_at = float(snapshot["observed_at"]) if snapshot["observed_at"] is not None else None
            if snapshot["today"] is not None and observed_at is not None and cst_date(observed_at) == cst_date(now):
                today_snapshot = int(snapshot["today"])
            if snapshot["max"] is not None:
                daily_max = int(snapshot["max"])
            expires = str(snapshot["expires"] or "")
        days_left, expiring_soon, expired = self._expiry_state(expires, now)
        disabled_code = str(state["disabled_code"] or "") if state is not None else ""
        disabled_message = str(state["disabled_message"] or "") if state is not None else ""
        disabled_at = float(state["disabled_at"]) if state is not None and state["disabled_at"] is not None else None
        exhausted_until = (
            float(state["exhausted_until"])
            if state is not None and state["exhausted_until"] is not None and float(state["exhausted_until"]) > now
            else None
        )
        return {
            "configured": True,
            "today_snapshot": today_snapshot,
            "today_estimated": today_estimated,
            "daily_max": daily_max,
            "reserve": reserve_quota(daily_max),
            "expires": expires,
            "days_until_expiry": days_left,
            "expiring_soon": expiring_soon,
            "expired": expired,
            "observed_at": observed_at,
            "disabled": bool(disabled_code),
            "disabled_code": disabled_code,
            "disabled_message": disabled_message,
            "disabled_at": disabled_at,
            "exhausted_until": exhausted_until,
            "initial_grant": max(0, int(initial_grant)),
        }

    def read_entries(self, key_fp: str, *, member_id: str | None = None, limit: int = 500) -> list[dict]:
        if not key_fp:
            return []
        limit = max(1, min(2000, int(limit)))
        with self._connect() as conn:
            if member_id is None:
                rows = conn.execute(
                    "SELECT id, member_id, kind, category, amount, ref, note, created "
                    "FROM quota_entries WHERE key_fp=? ORDER BY created DESC, rowid DESC LIMIT ?",
                    (key_fp, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, member_id, kind, category, amount, ref, note, created "
                    "FROM quota_entries WHERE key_fp=? AND member_id=? ORDER BY created DESC, rowid DESC LIMIT ?",
                    (key_fp, member_id, limit),
                ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "member_id": str(row["member_id"]),
                "kind": str(row["kind"]),
                "category": str(row["category"] or ""),
                "amount": int(row["amount"]),
                "ref": str(row["ref"]),
                "note": str(row["note"] or ""),
                "created": float(row["created"]),
            }
            for row in rows
        ]

    def read_snapshot_raw(self, key_fp: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM key_snapshots WHERE key_fp=?", (key_fp,)).fetchone()
        return dict(row) if row is not None else None


__all__ = [
    "OneBoundQuotaStore",
    "QuotaBalanceConflictError",
    "cst_date",
    "cst_day_start",
    "next_cst_midnight",
    "reserve_quota",
]
