"""从原领域记录投影统一队列；不复制商品、回执或业务终态。"""
from __future__ import annotations

import json

from erp_web.product_model.online_operation_model import effective_job_status
from erp_web.product_model.task_queue_model import PENDING_RESULTS, has_query_identity, task_display_status, publish_can_retry


class TaskQueueReader:
    def __init__(self, db):
        self.db = db

    def tasks(self, *, pending_only=False, source="", job_id="", platform=""):
        with self.db._connect() as conn:
            publishes = conn.execute("""SELECT p.job_id,p.product_id,p.created_at,p.updated_at,s.key AS platform,s.value AS item,
                json_extract(p.payload_json,'$.product_name') AS title,
                json_extract(p.payload_json,'$.task_origin') AS origin,
                json_type(p.payload_json,'$.approved_publications.' || s.key) AS approval,
                json_extract(p.payload_json,'$.approved_publications.' || s.key || '.store_identity') AS account_id,
                json_extract(p.payload_json,'$.terminal_persistence_error') AS persistence_error
                FROM publish_jobs p,json_each(p.payload_json,'$.platforms') s
                WHERE (?=0 OR json_extract(s.value,'$.status') IN ('pending_confirmation','outcome_unknown','needs_attention'))
                AND (?='' OR p.job_id=?) AND (?='' OR s.key=?) AND (?='' OR ?='publish')""",
                (int(pending_only), job_id, job_id, platform, platform, source, source)).fetchall()
            online = conn.execute("""SELECT id,platform,account_id,operation,target_id,status,result_json,created_at,updated_at,
                json_extract(request_json,'$.catalog') AS catalog,
                (SELECT json_extract(snapshot_json,'$.title') FROM online_listings WHERE id=online_jobs.target_id) AS title,
                EXISTS(SELECT 1 FROM online_jobs newer WHERE newer.target_id=online_jobs.target_id
                    AND newer.platform=online_jobs.platform AND newer.account_id=online_jobs.account_id
                    AND newer.operation<>'sync' AND newer.created_at>online_jobs.created_at) AS has_newer_change
                FROM online_jobs WHERE (?=0 OR (operation<>'sync' AND status IN ('submitted','waiting_confirmation','outcome_unknown','needs_attention')))
                AND (?='' OR id=?) AND (?='' OR ?='online')""",
                (int(pending_only), job_id, job_id, source, source)).fetchall()
        tasks = []
        for row in publishes:
            item = json.loads(row["item"])
            result = item.get("result") or {}
            status = str(item.get("status") or "")
            tasks.append({"id": f"publish:{row['job_id']}:{row['platform']}", "job_id": row["job_id"], "source": "publish",
                "origin": row["origin"] or "unknown", "platform": row["platform"], "account_id": row["account_id"] or "",
                "operation": "publish", "title": row["title"] or row["product_id"], "target_id": row["product_id"],
                "business_status": status, "status": "needs_attention" if row["persistence_error"] else task_display_status(status),
                "created_at": row["created_at"], "updated_at": item.get("updated_at") or row["updated_at"],
                "error": row["persistence_error"] or item.get("error") or item.get("confirmation", {}).get("check_error") or "", "protection": "禁止重复发布" if status in PENDING_RESULTS or status in {"queued", "running"} or row["persistence_error"] else "",
                "can_retry": bool(row["approval"]) and publish_can_retry(status, result), "confirmation": None, "_result": result, "_submitted_at": item.get("confirmation", {}).get("submitted_at") or item.get("created_at") or row["created_at"],
                "_can_query": has_query_identity(result), "_legacy_checks": int(item.get("confirmation", {}).get("checks_used") or 0),
                "_attention": status == "needs_attention" or bool(item.get("confirmation", {}).get("requires_attention"))})
        for raw in online:
            row = dict(raw)
            result = json.loads(row.pop("result_json"))
            status = effective_job_status({**row, "result": result})
            if pending_only and status not in PENDING_RESULTS:
                continue
            is_sync = row["operation"] == "sync"
            if is_sync:
                title = "同步归档商品" if row["catalog"] == "archived" else "同步店铺商品"
                can_retry = status in {"failed", "partial"} and any(item.get("status") == "failed" for item in result.get("items", []))
            else:
                title = result.get("before", {}).get("title") or row["title"] or row["target_id"]
                can_retry = status == "failed" and not row["has_newer_change"]
            protection = ""
            if not is_sync:
                if status in {"queued", "running"}:
                    protection = "正在执行，本商品暂不能提交其他修改"
                elif status in PENDING_RESULTS:
                    protection = "禁止相关字段的冲突修改；可核对后结束本地操作"
            tasks.append({"id": f"online:{row['id']}", "job_id": row["id"], "source": "online", "origin": result.get("task_origin") or "unknown",
                "platform": row["platform"], "account_id": row["account_id"], "operation": row["operation"],
                "title": title, "target_id": row["target_id"],
                "business_status": status, "status": task_display_status(status), "created_at": row["created_at"], "updated_at": row["updated_at"],
                "error": (result.get("platform_confirmation", {}).get("note") or result.get("error") or "") if status not in {"confirmed", "closed_unconfirmed"} else "",
                "protection": protection, "can_retry": can_retry,
                "confirmation": None, "_result": result, "_submitted_at": result.get("submitted_at") or row["created_at"], "_can_query": not is_sync,
                "_legacy_checks": int(result.get("polls") or 0), "_attention": bool(result.get("platform_confirmation", {}).get("needs_attention"))})
        return tasks

    def task(self, task_id):
        parts = task_id.split(":")
        if len(parts) == 3 and parts[0] == "publish":
            tasks = self.tasks(source="publish", job_id=parts[1], platform=parts[2])
        elif len(parts) == 2 and parts[0] == "online":
            tasks = self.tasks(source="online", job_id=parts[1])
        else:
            return None
        return tasks[0] if tasks else None


__all__ = ["TaskQueueReader"]
