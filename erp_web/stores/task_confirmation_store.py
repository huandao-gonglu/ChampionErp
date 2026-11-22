"""只保存确认计划和预算；业务结果由原发布/在线商品任务持有。"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure
from erp_web.schemas.task_queue import ConfirmationPolicy
from erp_web.product_model.task_queue_model import task_account_key


AUTOMATIC_CONFIRMATION_ENDED = "自动确认已结束，平台结果尚未确认；可手动查询平台结果或核对平台后台"


class TaskConfirmationStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS confirmations (
                id TEXT PRIMARY KEY, account_key TEXT NOT NULL, policy TEXT NOT NULL,
                state TEXT NOT NULL, checks_used INTEGER NOT NULL DEFAULT 0,
                requests_used INTEGER NOT NULL DEFAULT 0, next_check_at REAL NOT NULL,
                deadline_at REAL NOT NULL, last_checked_at REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',
                lease_until REAL NOT NULL DEFAULT 0)""")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure(self, task, *, submitted_at: float, policy: ConfirmationPolicy, legacy_checks: int = 0):
        with self.connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO confirmations
                (id,account_key,policy,state,checks_used,next_check_at,deadline_at,error)
                VALUES(?,?,?,?,?,?,?,?)""", (task["id"], task_account_key(task["source"], task["platform"], task["account_id"]),
                    json.dumps(policy.to_dict()), "waiting" if task["origin"] != "unknown" else "needs_attention",
                    legacy_checks, submitted_at + policy.first_delay, submitted_at + policy.lifetime,
                    "" if task["origin"] != "unknown" else "历史任务未启用新的自动查询；请核对平台结果"))
        return self.get(task["id"])

    def get(self, task_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM confirmations WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def stop(self, task_id, error):
        with self.connect() as conn:
            conn.execute("UPDATE confirmations SET state='needs_attention',next_check_at=0,error=? WHERE id=? AND lease_until<=?",
                         (error, task_id, time.time()))

    def submission_retried(self, task_id):
        """显式重试提交后恢复剩余查询额度，不重置用量或期限。"""
        now = time.time()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT lease_until FROM confirmations WHERE id=?", (task_id,)).fetchone()
            if row and row["lease_until"] > now:
                raise ValueError("正在查询发布结果，请查询结束后再重试提交。")
            conn.execute("""UPDATE confirmations SET state='waiting',error='',
                next_check_at=MIN(deadline_at,?+json_extract(policy,'$.first_delay'))
                WHERE id=? AND state='needs_attention' AND lease_until<=? AND deadline_at>?
                AND checks_used<json_extract(policy,'$.max_checks')
                AND requests_used<json_extract(policy,'$.max_requests')""", (now, task_id, now, now))

    @staticmethod
    def _exhausted(row, policy, now):
        return row["checks_used"] >= policy.max_checks or row["requests_used"] >= policy.max_requests or now >= row["deadline_at"]

    def claim(self, task_id, *, manual=False):
        now, token = time.time(), uuid4().hex
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM confirmations WHERE id=?", (task_id,)).fetchone()
            policy = ConfirmationPolicy.from_dict(json.loads(row["policy"]))
            if row["lease_until"] > now:
                return None, "checking"
            exhausted = self._exhausted(row, policy, now)
            if exhausted and not manual:
                conn.execute("UPDATE confirmations SET state='needs_attention',next_check_at=0,owner='',lease_until=0,error=? WHERE id=?",
                             (AUTOMATIC_CONFIRMATION_ENDED, task_id))
                return None, "budget_exhausted"
            if row["last_checked_at"] and now < row["last_checked_at"] + policy.cooldown:
                return None, "cooldown"
            # 已过期的 checking 租约可以重新领取，不能让进程中断永久停止自动确认。
            if not manual and (row["state"] not in {"waiting", "checking"} or now < row["next_check_at"]):
                return None, "not_due"
            # 同一账号的自动、手动确认共用一个查询占位。
            if conn.execute("SELECT 1 FROM confirmations WHERE account_key=? AND lease_until>? AND id<>?",
                            (row["account_key"], now, task_id)).fetchone():
                return None, "checking"
            # 自动确认停止后，每次显式人工查询仅授单轮请求额度；不重置累计用量或自动期限。
            stopped = manual and (exhausted or row["state"] == "needs_attention")
            deadline = now + policy.round_timeout if stopped else min(row["deadline_at"], now + policy.round_timeout)
            request_limit = row["requests_used"] + policy.manual_round_requests() if stopped else policy.max_requests
            # 停止状态继续持久化，实际占位由租约表示；人工查询中断也不能重启自动计划。
            conn.execute("UPDATE confirmations SET state=?,owner=?,lease_until=?,next_check_at=?,checks_used=checks_used+1,last_checked_at=? WHERE id=?",
                         ("needs_attention" if stopped else "checking", token, deadline + 30,
                          0 if stopped else row["next_check_at"], now, task_id))
        return {"token": token, "deadline": deadline, "request_limit": request_limit, "policy": policy}, "checking"

    def consume_request(self, task_id, lease, context):
        """在真实外发之前扣预算，包含 SDK/HTTP 层的每次尝试，不持锁等待网络。"""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM confirmations WHERE id=?", (task_id,)).fetchone()
            now = time.time()
            if (row["owner"] != lease["token"] or row["lease_until"] <= now
                    or now >= lease["deadline"] or row["requests_used"] >= lease["request_limit"]):
                raise ExternalRequestBlocked(RequestFailure("TASK_CONFIRMATION_BUDGET", "本次确认请求预算已用尽，未发送请求"))
            conn.execute("UPDATE confirmations SET requests_used=requests_used+1 WHERE id=?", (task_id,))

    def finish(self, task_id, token, *, done=False, attention=False, error=""):
        now = time.time()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM confirmations WHERE id=? AND owner=?", (task_id, token)).fetchone()
            if not row:
                return
            policy = ConfirmationPolicy.from_dict(json.loads(row["policy"]))
            exhausted = self._exhausted(row, policy, now)
            stopped = row["state"] == "needs_attention"
            state = "done" if done else "needs_attention" if attention or exhausted or stopped else "waiting"
            if exhausted and not done and not error:
                error = AUTOMATIC_CONFIRMATION_ENDED
            elif stopped and not done and not error:
                error = row["error"]
            next_at = min(now + policy.next_interval(row["checks_used"]), row["deadline_at"]) if state == "waiting" else 0
            conn.execute("UPDATE confirmations SET state=?,next_check_at=?,error=?,owner='',lease_until=0 WHERE id=? AND owner=?",
                         (state, next_at, error, task_id, token))

    @staticmethod
    def summary(row):
        if not row:
            return None
        now = time.time()
        policy = ConfirmationPolicy.from_dict(json.loads(row["policy"]))
        summary = {key: row[key] for key in ("state", "checks_used", "requests_used", "last_checked_at", "next_check_at", "deadline_at", "error")}
        if row["lease_until"] > now:
            summary["state"] = "checking"
        elif row["state"] == "checking":
            summary["state"] = "needs_attention" if TaskConfirmationStore._exhausted(row, policy, now) else "waiting"
        return summary | {"checks_remaining": max(0, policy.max_checks - row["checks_used"]),
                          "requests_remaining": max(0, policy.max_requests - row["requests_used"])}


__all__ = ["TaskConfirmationStore"]
