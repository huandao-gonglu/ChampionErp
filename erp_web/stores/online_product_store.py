"""远端快照、同步进度和修改回执的持久化；不修改商品库或草稿。"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from erp_web.db import ErpDatabase, utc_now
from erp_web.schemas.online_products import BuyerLink, OnlineListing, snapshot_version

ACTIVE = ("queued", "running", "submitted", "waiting_confirmation", "outcome_unknown")


class OnlineConflict(ValueError):
    pass


class OnlineProductStore:
    def __init__(self, db: ErpDatabase):
        self.db = db

    def save(self, listing: OnlineListing, *, lease: dict[str, Any] | None = None) -> OnlineListing:
        listing.version = snapshot_version(listing)
        listing.synced_at = utc_now()
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if lease:
                self._assert_lease(conn, lease)
            conn.execute("""INSERT INTO online_listings
                (id,platform,account_id,remote_id,version,snapshot_json,synced_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version,
                snapshot_json=excluded.snapshot_json,synced_at=excluded.synced_at""",
                (listing.id, listing.platform, listing.account_id, listing.remote_id,
                 listing.version, listing.model_dump_json(), listing.synced_at))
            conn.commit()
        return self.get(listing.id)

    def get(self, listing_id: str) -> OnlineListing:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_listings WHERE id=?", (listing_id,)).fetchone()
        if row is None:
            raise ValueError("在线商品不存在，请先同步店铺")
        result = OnlineListing.model_validate_json(row["snapshot_json"])
        result.desired_sale_state = row["desired_sale_state"]
        return result

    def update_buyer_links(self, listing_id: str, links: list[BuyerLink], *, expected_version: str) -> bool:
        """补齐导航元数据，保留业务版本和原同步时间，拒绝覆盖已变化的商品。"""
        encoded = json.dumps([link.model_dump(mode="json") for link in links], ensure_ascii=False)
        with self.db._connect() as conn:
            cursor = conn.execute(
                "UPDATE online_listings SET snapshot_json=json_set(snapshot_json,'$.buyer_links',json(?)) WHERE id=? AND version=?",
                (encoded, listing_id, expected_version),
            )
            conn.commit()
        return cursor.rowcount == 1

    def listings(self, platform: str, account: str) -> list[OnlineListing]:
        with self.db._connect() as conn:
            rows = conn.execute("SELECT snapshot_json,desired_sale_state FROM online_listings WHERE platform=? AND account_id=? ORDER BY synced_at DESC,id", (platform, account)).fetchall()
        return [OnlineListing.model_validate({**json.loads(row["snapshot_json"]), "desired_sale_state": row["desired_sale_state"]}) for row in rows]

    def sale_intent(self, listing_id: str, state: str, *, lease: dict[str, Any]) -> None:
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            conn.execute("UPDATE online_listings SET desired_sale_state=? WHERE id=?", (state, listing_id))
            conn.commit()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["result"] = json.loads(result.pop("result_json"))
        result.pop("owner", None)
        result.pop("lease_until", None)
        return result

    def job(self, job_id: str) -> dict[str, Any]:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("操作记录不存在")
        return self._job(row)

    def jobs(self, platform: str, account: str) -> list[dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM online_jobs WHERE platform=? AND account_id=? ORDER BY created_at DESC LIMIT 100", (platform, account)).fetchall()
        return [self._job(row) for row in rows]

    def idempotent_job(self, key: str) -> dict[str, Any] | None:
        with self.db._connect() as conn:
            row = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key=?", (key,)).fetchone()
        return self._job(row) if row else None

    def enqueue(self, platform: str, account: str, operation: str, target: str, request: dict[str, Any], key: str) -> dict[str, Any]:
        facts = json.dumps(request, ensure_ascii=False, sort_keys=True)
        now, job_id = utc_now(), uuid4().hex
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM online_jobs WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if (old["platform"], old["account_id"], old["operation"], old["target_id"], old["request_json"]) != (platform, account, operation, target, facts):
                    raise OnlineConflict("重复提交键对应不同操作，请重新确认")
                return self._job(old)
            # 全量同步与任何修改互斥；两个不同商品的修改允许分别排队。
            rows = conn.execute("SELECT target_id FROM online_jobs WHERE platform=? AND account_id=? AND status IN ('queued','running','submitted','waiting_confirmation','outcome_unknown')", (platform, account)).fetchall()
            if any(target == "*" or row["target_id"] in ("*", target) for row in rows):
                raise OnlineConflict("该范围已有未完成操作，请先查看记录或查询平台结果")
            conn.execute("INSERT INTO online_jobs(id,platform,account_id,operation,target_id,status,idempotency_key,request_json,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                         (job_id, platform, account, operation, target, key, facts, now, now))
            conn.commit()
        return self.job(job_id)

    def claim(self, owner: str) -> dict[str, Any] | None:
        token = owner + ":" + uuid4().hex
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # 未发出的任务可安全恢复；已发出的修改永不自动重放。
            conn.execute("""UPDATE online_jobs SET status=CASE WHEN dispatched=1 THEN 'outcome_unknown' ELSE 'queued' END,
                owner='', lease_until=0,updated_at=? WHERE status='running' AND lease_until<?""", (utc_now(), time.time()))
            row = conn.execute("""SELECT * FROM online_jobs WHERE status='queued' OR
                (status IN ('submitted','waiting_confirmation') AND lease_until<?
                 AND COALESCE(json_extract(result_json,'$.polls'),0)<20)
                ORDER BY created_at LIMIT 1""", (time.time(),)).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute("UPDATE online_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?", (token, time.time()+900, utc_now(), row["id"]))
            conn.commit()
        return {**self._job(row), "lease_token": token}

    @staticmethod
    def _assert_lease(conn, job):
        row = conn.execute("SELECT owner,lease_until FROM online_jobs WHERE id=?", (job["id"],)).fetchone()
        if not row or row["owner"] != job["lease_token"] or row["lease_until"] <= time.time():
            raise OnlineConflict("任务执行租约已失效，禁止过期执行者提交结果")

    def update_job(self, job_id: str, status: str, result: dict[str, Any], *, owner: str, dispatched: bool = False) -> None:
        with self.db._connect() as conn:
            cursor = conn.execute("UPDATE online_jobs SET status=?,result_json=?,dispatched=MAX(dispatched,?),lease_until=?,updated_at=? WHERE id=? AND owner=? AND lease_until>?",
                         (status, json.dumps(result, ensure_ascii=False), int(dispatched), time.time()+(15 if status in ("submitted", "waiting_confirmation") else 900), utc_now(), job_id, owner, time.time()))
            if cursor.rowcount != 1:
                raise OnlineConflict("任务执行租约已失效，禁止过期执行者提交结果")
            conn.commit()

    def claim_reconcile(self, job_id: str) -> dict[str, Any]:
        token = uuid4().hex
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in ("submitted", "waiting_confirmation", "outcome_unknown"):
                raise OnlineConflict("只有待确认或结果未知的修改可以查询平台结果")
            conn.execute("UPDATE online_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?", (token, time.time()+900, utc_now(), job_id))
            conn.commit()
        return {**self._job(row), "lease_token": token}
