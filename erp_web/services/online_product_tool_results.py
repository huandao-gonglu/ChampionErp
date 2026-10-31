"""在线任务面向模型的只读投影；完整回执继续保存在领域任务中。"""
from datetime import datetime, timezone
from typing import Any

from erp_web.schemas.online_product_capabilities import OnlineJobResult
from erp_web.product_model.online_operation_model import UNRESOLVED_STATUSES


def compact_online_job(response: dict[str, Any]) -> OnlineJobResult:
    job = response["job"]
    result = job["result"]
    status = job["status"]
    unresolved = status in UNRESOLVED_STATUSES
    evidence = result.get("evidence") or {}
    try:
        age = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(evidence.get("checked_at", "")).timestamp()
    except (ValueError, TypeError):
        age = float("inf")
    checks = result.get("confirmation") or {}
    observed = evidence.get("observed") or {}
    pending = result.get("platform_confirmation", {}).get("pending")
    can_close = (unresolved and 0 <= age <= 300 and bool(checks) and not all(checks.values())
                 and bool(observed) and all(value is not None for value in observed.values())
                 and not result.get("read_errors") and not pending)
    if status in {"queued", "running"}:
        explanation = "任务仍在排队或执行，不能人工结束或重复提交。"
    elif unresolved:
        explanation = ("原结果尚未确认。只有用户已明确授权且回读证据有效、平台不在处理中时才能结束本地任务；原请求仍可能延迟生效。"
                       "can_close 仅表示回执具备基本条件，执行时仍核对证据、商品版本及活动任务。")
    else:
        explanation = (f"该任务为 {status}，本任务不再阻塞新修改，无需人工解锁，不要再次 close。"
                       "partial 不代表全部成功。新修改须读取当前商品版本并检查其他活动任务，不能重放旧请求。")
    # 不回传 before 中的完整文案、图片和所有属性，也不携带原始平台响应。
    visible_result = {key: result[key] for key in (
        "changes", "scope_id", "confirmation", "evidence", "platform_confirmation", "platform_errors",
        "read_errors", "error", "error_code", "resolution", "last_confirmation_at", "polls",
    ) if key in result}
    if job["operation"] == "sync":
        visible_result.update({key: result[key] for key in (
            "completed", "failed", "created", "updated", "phase", "discovered", "discovery_complete",
        ) if key in result})
        # 同步回执可能包含数千个成功项；只投影有界失败样例，完整集合由原重试入口读取。
        failures = [item for item in result.get("items", []) if item.get("status") == "failed"]
        visible_result["failed_items"] = [
            {"remote_id": item["remote_id"], "error": str(item.get("error") or "")[:500]}
            for item in failures[:10]
        ]
        visible_result["failed_items_truncated"] = len(failures) > 10
        if status in {"partial", "failed"}:
            explanation = "同步任务已结束，无需解锁；已保存的成功项不会因后续尝试失败而撤销。"
            explanation += ("目录发现未完成，需重新调用 online_products_sync 完整发现目录。"
                            if result.get("discovery_complete") is False or not failures else
                            "继续处理时调用 online_products_retry 仅重试失败项，不重复全量同步；沿用原审批。")
    return OnlineJobResult.model_validate({
        "ok": response["ok"], "job": {**job, "result": visible_result},
        "recovery": {"requires_resolution": unresolved,
                     "can_reconcile": status in UNRESOLVED_STATUSES or status == "submitted",
                     "can_close": can_close, "explanation": explanation},
    })


__all__ = ["compact_online_job"]
