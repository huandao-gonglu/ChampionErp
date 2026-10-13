"""远端快照、同步进度和修改回执的持久化；不修改商品库或草稿。"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from erp_web.db import ErpDatabase, utc_now
from erp_web.schemas.online_products import BuyerLink, OnlineListing, OnlineStatus, snapshot_version

ACTIVE = ("queued", "running", "submitted", "waiting_confirmation", "outcome_unknown")


class OnlineConflict(ValueError):
    def __init__(self, message: str, *, job_id: str | None = None):
        super().__init__(message)
        self.job_id = job_id


class OnlineProductStore:
    def __init__(self, db: ErpDatabase):
        self.db = db

    def save(self, listing: OnlineListing, *, lease: dict[str, Any] | None = None, full_snapshot: bool = True) -> OnlineListing:
        listing.version = snapshot_version(listing)
        if full_snapshot:
            listing.synced_at = utc_now()
            if listing.details_state == "ready" and not listing.errors:
                listing.status_checked_at = datetime.now(timezone.utc).isoformat()
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

    def sync_state(self, listing_id: str, state: str, errors: list[str], *, lease: dict[str, Any]) -> None:
        """详情进度独立于业务快照；读取失败不覆盖旧价格、库存、版本和同步时间。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_lease(conn, lease)
            conn.execute("""UPDATE online_listings SET snapshot_json=json_set(
                snapshot_json,'$.details_state',?,'$.errors',json(?)) WHERE id=?""",
                (state, json.dumps(errors, ensure_ascii=False), listing_id))
            conn.commit()

    @staticmethod
    def _assert_status_refreshable(conn, listing: OnlineListing):
        running = conn.execute("""SELECT 1 FROM online_jobs WHERE platform=? AND account_id=?
            AND target_id IN (?, '*') AND status IN ('queued', 'running') LIMIT 1""",
            (listing.platform, listing.account_id, listing.id)).fetchone()
        if running:
            raise OnlineConflict("该商品正在同步或执行修改，请完成后再刷新状态")

    def assert_status_refreshable(self, listing: OnlineListing) -> None:
        with self.db._connect() as conn:
            self._assert_status_refreshable(conn, listing)

    def update_status(self, before: OnlineListing, status: OnlineStatus) -> OnlineListing:
        """短事务内核对版本后合并状态；平台等待在事务外，完整同步时间不变。"""
        if status.remote_id != before.remote_id:
            raise ValueError("刷新结果与目标商品身份不一致")
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_status_refreshable(conn, before)
            row = conn.execute("SELECT snapshot_json FROM online_listings WHERE id=?", (before.id,)).fetchone()
            if row is None:
                raise OnlineConflict("商品记录已变化，请重新打开商品")
            current = OnlineListing.model_validate_json(row[0])
            if (current.version, current.synced_at, current.status_checked_at) != (before.version, before.synced_at, before.status_checked_at):
                raise OnlineConflict("查询期间商品数据已更新，请重新刷新状态")
            markets = {item.id: item for item in status.markets}
            if len(markets) != len(status.markets) or set(markets) - {item.id for item in current.markets}:
                raise OnlineConflict("商品市场关系已变化，请同步店铺商品")
            current.raw_status = status.raw_status
            current.raw_sub_status = status.raw_sub_status
            current.platform_issues = status.platform_issues
            current.sale_state = status.sale_state
            for market in current.markets:
                if market.id in markets:
                    market.raw_status = markets[market.id].raw_status
                    market.raw_sub_status = markets[market.id].raw_sub_status
            current.status_checked_at = datetime.now(timezone.utc).isoformat()
            current.version = snapshot_version(current)
            conn.execute("UPDATE online_listings SET version=?,snapshot_json=? WHERE id=?",
                (current.version, current.model_dump_json(), before.id))
            conn.commit()
        return self.get(before.id)

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

    def thumbnails(self, platform: str, account: str, remote_ids: set[str]) -> dict[str, str]:
        """按完整账号和远端商品身份批量读取平台缩略图；不使用本地资产或采购图片。"""
        ids = sorted(remote_id for remote_id in remote_ids if remote_id)
        result = {}
        if not ids or not account:
            return result
        with self.db._connect() as conn:
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                marks = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"""SELECT remote_id,json_extract(snapshot_json,'$.thumbnail') AS thumbnail
                        FROM online_listings WHERE platform=? AND account_id=?
                        AND remote_id IN ({marks})""", (platform, account, *batch),
                ).fetchall()
                for row in rows:
                    url = str(row["thumbnail"] or "").strip()
                    try:
                        parsed = urlsplit(url)
                        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                            result[row["remote_id"]] = url
                    except ValueError:
                        continue
        return result

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

    def latest_sync_summary(self, platform: str, account: str) -> dict[str, Any] | None:
        """查询最新同步的固定进度字段，避免为商品读取加载历史任务及逐项回执。"""
        with self.db._connect() as conn:
            row = conn.execute("""SELECT id,status,updated_at,
                json_extract(result_json,'$.discovered') AS discovered,
                json_extract(result_json,'$.completed') AS completed,
                json_extract(result_json,'$.failed') AS failed,
                json_extract(result_json,'$.discovery_complete') AS discovery_complete,
                COALESCE(json_extract(result_json,'$.error_code'),'') AS error_code
                FROM online_jobs WHERE platform=? AND account_id=? AND operation='sync'
                ORDER BY created_at DESC,id DESC LIMIT 1""", (platform, account)).fetchone()
        return dict(row) if row is not None else None

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
            # 同步只与正在排队或执行的任务互斥；等待回执不应冻结整店只读同步。
            # 新修改仍受同目标的全部未完成任务保护，不能借同步绕过未知写入。
            rows = conn.execute("SELECT id,target_id,operation,status,result_json FROM online_jobs WHERE platform=? AND account_id=? AND status IN ('queued','running','submitted','waiting_confirmation','outcome_unknown')", (platform, account)).fetchall()
            conflict = next((row for row in rows
                             if (operation != "sync" or row["status"] in ("queued", "running"))
                             and (target == "*" or row["target_id"] in ("*", target))), None)
            if conflict:
                if conflict["operation"] == "sync":
                    progress = json.loads(conflict["result_json"])
                    count = int(progress.get("completed", 0)) + int(progress.get("failed", 0))
                    raise OnlineConflict(f"当前店铺正在同步，已处理 {count} 件；请查看同步进度，无需重复启动", job_id=conflict["id"])
                if operation == "sync":
                    raise OnlineConflict("当前店铺有排队或执行中的商品修改，请完成后再同步", job_id=conflict["id"])
                raise OnlineConflict("该商品已有未完成的修改，请到操作记录查询平台结果；核对后仍未确认的操作可由人工结束", job_id=conflict["id"])
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
            row = conn.execute("""SELECT * FROM online_jobs WHERE status='queued'
                ORDER BY created_at LIMIT 1""").fetchone()
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
                         (status, json.dumps(result, ensure_ascii=False), int(dispatched), time.time()+(0 if status in {"submitted", "waiting_confirmation"} else 900), utc_now(), job_id, owner, time.time()))
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
            syncing = conn.execute("""SELECT 1 FROM online_jobs WHERE platform=? AND account_id=?
                AND operation='sync' AND status IN ('queued', 'running') LIMIT 1""",
                (row["platform"], row["account_id"])).fetchone()
            if syncing:
                raise OnlineConflict("当前店铺正在同步，请完成后再查询平台结果")
            result = json.loads(row["result_json"])
            earliest = float(result.get("last_confirmation_at") or 0) + 30
            if earliest > time.time():
                raise OnlineConflict(f"平台结果尚在等待窗口，请 {max(1, int(earliest-time.time()))} 秒后查询")
            conn.execute("UPDATE online_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?", (token, time.time()+900, utc_now(), job_id))
            conn.commit()
        return {**self._job(row), "lease_token": token}

    def close_operation(self, job_id: str, expected_updated_at: str) -> dict[str, Any]:
        """短事务内核验证据并解除目标互斥；旧提交键和外部请求阻断始终保留。"""
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM online_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("操作记录不存在")
            result = json.loads(row["result_json"])
            if row["status"] == "closed_unconfirmed" and result.get("resolution", {}).get("based_on_updated_at") == expected_updated_at:
                return self._job(row)
            if row["updated_at"] != expected_updated_at:
                raise OnlineConflict("操作记录已变化，请重新查询平台结果并确认")
            if row["operation"] == "sync" or row["status"] not in ("outcome_unknown", "waiting_confirmation"):
                raise OnlineConflict("只有查询后仍未确认的商品修改可以人工结束")
            evidence = result.get("evidence") or {}
            try:
                checked_at = datetime.fromisoformat(evidence.get("checked_at", "")).timestamp()
            except (TypeError, ValueError):
                checked_at = 0
            observed = evidence.get("observed") or {}
            checks = result.get("confirmation") or {}
            if (not 0 <= time.time() - checked_at <= 300 or checked_at < float(result.get("last_confirmation_at") or 0)
                    or result.get("read_errors") or not checks or all(checks.values())
                    or not observed or any(value is None for value in observed.values())):
                raise OnlineConflict("请先成功查询平台当前值，并在五分钟内核对后结束操作")
            if result.get("platform_confirmation", {}).get("pending"):
                raise OnlineConflict("平台仍在处理原请求，请等待处理结束后重新查询")
            active = conn.execute("""SELECT 1 FROM online_jobs WHERE platform=? AND account_id=?
                AND target_id IN (?, '*') AND status IN ('queued','running') LIMIT 1""",
                (row["platform"], row["account_id"], row["target_id"])).fetchone()
            listing = conn.execute("SELECT version FROM online_listings WHERE id=? AND platform=? AND account_id=?",
                                   (row["target_id"], row["platform"], row["account_id"])).fetchone()
            if active or not listing or listing["version"] != evidence.get("version"):
                raise OnlineConflict("商品正在处理或核对后已变化，请重新查询平台结果")
            now = utc_now()
            result["resolution"] = {"decision": "accept_current_state", "previous_status": row["status"],
                "closed_at": now, "based_on_updated_at": expected_updated_at,
                "acknowledged_unconfirmed": True, "evidence": evidence}
            conn.execute("""UPDATE online_jobs SET status='closed_unconfirmed',result_json=?,
                owner='',lease_until=0,updated_at=? WHERE id=?""", (json.dumps(result, ensure_ascii=False), now, job_id))
            conn.commit()
        return self.job(job_id)
