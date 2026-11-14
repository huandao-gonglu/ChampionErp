"""任务投影的纯状态规则；不发送请求或保存业务数据。"""
from __future__ import annotations

from datetime import datetime
import hashlib

from erp_web.product_model.sku_model import record
from erp_web.schemas.yandex import YANDEX_PUBLISH_STEPS


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


def yandex_submission_receipt(row: dict) -> dict:
    result = record(row.get("result"))
    inner = record(result.get("result")) or result
    plan = record(inner.get("plan"))
    checkpoint = record(inner.get("checkpoint"))
    if (plan.get("business_id") and plan.get("campaign_id") and plan.get("offer_id")
            and all(plan.get(key) == checkpoint.get(key) for key in ("business_id", "campaign_id", "offer_id"))):
        return checkpoint
    return {}


def sku_submission_status(row: dict) -> str:
    # 旧回执把明确未发送的价格/库存请求记为失败，保留原错误与已完成步骤。
    status = row.get("status", "")
    if status == "failed" and record(row.get("result")).get("error_code") in {
        "EXTERNAL_REQUEST_DEADLINE", "EXTERNAL_REQUEST_CANCELLED",
    } and yandex_submission_receipt(row):
        return "submission_paused"
    return status


def completed_yandex_submission(row: dict) -> bool:
    steps = yandex_submission_receipt(row).get("completed_steps") or []
    return all(step in steps for step in YANDEX_PUBLISH_STEPS)


def sku_can_retry(row: dict) -> bool:
    return (sku_submission_status(row) in {"queued", "failed", "pending_submission", "submission_paused"}
            and not completed_yandex_submission(row))


def sku_can_check(row: dict) -> bool:
    status = sku_submission_status(row)
    if status == "outcome_unknown":
        return bool(row.get("task_id") or row.get("task_ids") or
                    "campaign_offer" in yandex_submission_receipt(row).get("completed_steps", []))
    if status in {"failed", "submission_paused", "pending_submission"}:
        return "campaign_offer" in yandex_submission_receipt(row).get("completed_steps", [])
    return status in {"pending_confirmation", "needs_attention"} and has_query_identity(row)


def project_publish_submission(item: dict) -> None:
    """将旧的发送前超时回执投影为可手动续提的中断，不在读取时写库。"""
    result = item.get("result") or {}
    rows = result.get("sku_results") or []
    for row in rows:
        if isinstance(row, dict):
            row["status"] = sku_submission_status(row)
    if item.get("status") in {"failed", "partial"} and any(record(row).get("status") == "submission_paused" for row in rows):
        item.update(status="needs_attention", stage="needs_attention")
        result.update(status="needs_attention", ok=False)


def can_check_publish_status(status: str, result: dict) -> bool:
    if status in PENDING_RESULTS or (result.get("sku_results") and status in {"failed", "partial", "interrupted"}):
        return has_query_identity(result)
    return False


def has_query_identity(result: dict) -> bool:
    if result.get("sku_results"):
        return any(sku_can_check(row) for row in result["sku_results"] if isinstance(row, dict))
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
        return any(sku_can_retry(row) for row in rows if isinstance(row, dict))
    return status in {"failed", "interrupted"} and result.get("status") != "outcome_unknown"


__all__ = [
    "PENDING_RESULTS", "can_check_publish_status", "has_query_identity", "project_publish_submission",
    "publish_can_retry", "sku_can_check", "sku_can_retry", "sku_submission_status",
    "task_account_key", "task_display_status", "task_timestamp",
]
