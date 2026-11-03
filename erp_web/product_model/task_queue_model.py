"""任务投影的纯状态规则；不发送请求或保存业务数据。"""
from __future__ import annotations

from datetime import datetime
import hashlib


PENDING_RESULTS = frozenset({"pending_confirmation", "waiting_confirmation", "outcome_unknown", "needs_attention"})


def task_account_key(source: str, platform: str, account_id: str) -> str:
    """与发布确认绑定的稳定店铺身份一致，使两类任务共用账号占位。"""
    if source == "publish":
        return account_id or platform
    values = [platform, *account_id.split(":")] if platform == "yandex" else [platform, account_id]
    return platform + ":" + hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]


def task_timestamp(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def task_display_status(status: str) -> str:
    if status == "interrupted":
        return status
    if status in {"pending", "queued"}:
        return "queued"
    if status in {"running", "retrying"}:
        return "running"
    if status in {"pending_confirmation", "waiting_confirmation"}:
        return "waiting"
    if status in {"outcome_unknown", "needs_attention"}:
        return "needs_attention"
    if status in {"success", "published", "confirmed", "submitted", "real_publish_success"}:
        return "completed"
    if status == "partial":
        return "partial"
    if status == "closed_unconfirmed":
        return "closed_unconfirmed"
    return "failed"


def has_query_identity(result: dict) -> bool:
    if result.get("sku_results"):
        for row in result["sku_results"]:
            if not isinstance(row, dict):
                continue
            if row.get("status") == "outcome_unknown":
                if row.get("task_id") or row.get("task_ids"):
                    return True
            elif row.get("status") in {"pending_confirmation", "needs_attention"} and has_query_identity(row):
                return True
        return False
    if result.get("task_id") or any(result.get("task_ids") or []):
        return True
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("offer_id"):
        return True
    inner = result.get("result")
    if isinstance(inner, dict) and has_query_identity(inner):
        return True
    return False


def publish_can_retry(status: str, result: dict) -> bool:
    if status not in {"failed", "partial", "interrupted", "needs_attention", "outcome_unknown"}:
        return False
    rows = result.get("sku_results")
    if rows:
        return any(row.get("status") in {"queued", "failed", "pending_submission"} for row in rows)
    return status in {"failed", "interrupted"} and result.get("status") != "outcome_unknown"


__all__ = ["PENDING_RESULTS", "task_timestamp", "task_display_status", "has_query_identity", "task_account_key", "publish_can_retry"]
