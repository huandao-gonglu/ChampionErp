"""在线修改的回执语义和冲突范围；纯逻辑，不查询平台或推进任务。"""
from __future__ import annotations

from typing import Any


UNRESOLVED_STATUSES = frozenset({"waiting_confirmation", "outcome_unknown", "needs_attention"})
NONBLOCKING_STATUSES = frozenset({"submitted", "confirmed", "partial", "failed", "closed_unconfirmed"})


def receipt_errors(value: Any) -> list[Any]:
    """成功 HTTP 响应仍须检查逐项拒绝；警告不作为失败。"""
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "notArchivedOffers" and item:
                errors.extend({"offer_id": row.get("offerId"), "code": row.get("error"),
                               "message": "商品仍存放在 Yandex 官方仓库，需售完或移出后才能归档" if row.get("error") == "OFFER_HAS_STOCKS" else "平台未能归档该商品"}
                              for row in item if isinstance(row, dict))
            elif key == "notUnarchivedOfferIds" and item:
                errors.extend({"offer_id": offer_id, "message": "平台未能恢复该商品的归档"} for offer_id in item)
            elif key in ("error", "errors") and item:
                errors.extend(item if isinstance(item, list) else [item])
            elif isinstance(item, (dict, list)):
                errors.extend(receipt_errors(item))
        if value.get("success") is False and not errors:
            errors.append({"message": "平台返回明确失败，但未提供错误详情"})
    elif isinstance(value, list):
        for item in value:
            errors.extend(receipt_errors(item))
    return errors


def has_async_task(receipt: Any) -> bool:
    if isinstance(receipt, dict):
        return bool(receipt.get("task_id")) or any(has_async_task(value) for value in receipt.values())
    return isinstance(receipt, list) and any(has_async_task(value) for value in receipt)


def submission_status(platform: str, operation: str, receipt: Any, *, model: str = "", target_id: str = "", scope_id: str = "") -> str:
    """同步接口按明确回执完成；只有真实异步或生效审核才等待确认。

    Yandex 库存/隐藏接口返回 OK；价格隔离与卡片审核仍需核验。
    Mercado 普通修改可返回空对象，带 task_id 的 User Products 修改需核验任务。
    无法识别的回执保留结果未知，不能推断成功或自动重放。
    """
    if not isinstance(receipt, dict):
        return "outcome_unknown"
    errors = receipt_errors(receipt)
    if platform == "mercadolibre":
        if has_async_task(receipt):
            return "waiting_confirmation"
        if model == "user_products":
            if target_id and receipt.get("id") != target_id:
                return "outcome_unknown"
            targets = receipt.get("listing_sites") or []
            if operation == "price" and scope_id and scope_id != "global":
                targets = [row for row in targets if row.get("id") == scope_id]
                if len(targets) != 1:
                    return "outcome_unknown"
            successes = [row.get("success") for row in targets]
            if any(value is not True and value is not False for value in successes):
                return "outcome_unknown"
            if errors or receipt.get("success") is False or False in successes:
                return "partial" if True in successes or receipt.get("success") is True else "failed"
            return "confirmed" if receipt.get("success") is True or successes and all(successes) else "outcome_unknown"
        if errors:
            return "failed" if receipt.get("success") is False else "partial"
        # 传统 /global/items 的官方成功回执允许空对象。
        # 站点改价直接写入所选 Item；历史记录中的 target_id 可能仍是 CBT 父商品。
        expected_id = scope_id if operation == "price" and scope_id and scope_id != "global" else target_id
        return "confirmed" if receipt == {} or receipt.get("success") is True or expected_id and receipt.get("id") == expected_id else "outcome_unknown"
    if platform == "yandex" and operation == "archive" and receipt.get("status") == "OK":
        # 每个领域任务只提交一个 SKU；官方返回失败清单，其余即归档/恢复成功。
        return "failed" if errors else "confirmed"
    if errors or receipt.get("status") in ("ERROR", "FAILED"):
        rejected = receipt.get("status") in ("ERROR", "FAILED") or (
            receipt.get("success") is False and not receipt.get("listing_sites")
        )
        return "failed" if rejected else "partial"
    if platform == "yandex" and receipt.get("status") == "OK":
        if operation in ("stock", "sale_state"):
            return "confirmed"
        if operation in ("price", "content"):
            return "waiting_confirmation"
    return "outcome_unknown"


def effective_job_status(job: dict[str, Any]) -> str:
    """旧 submitted 记录按实际回执读取；不改写历史、补造确认或发送请求。"""
    if job["status"] == "submitted" and job["operation"] != "sync":
        result = job["result"]
        return submission_status(job["platform"], job["operation"], result.get("receipt"),
            model=result.get("before", {}).get("model", ""), target_id=result.get("receipt_target", ""), scope_id=result.get("scope_id", ""))
    return job["status"]


def modifications_overlap(platform: str, previous: dict[str, Any], following: dict[str, Any]) -> bool:
    """仅对尚在处理或结果未知的变更判断范围，执行中的任务另做短期互斥。"""
    left, right = previous.get("operation"), following.get("operation")
    supported = {"price", "stock", "content", "sale_state", "archive"}
    if left not in supported or right not in supported or platform not in ("yandex", "mercadolibre"):
        return True
    if left == right:
        return True
    if "archive" in (left, right):
        return bool({left, right} & {"sale_state", "stock"})
    if {left, right} == {"stock", "sale_state"}:
        # Mercado 的库存可以影响销售状态；Yandex 隐藏独立于库存，但恢复须有库存。
        return platform == "mercadolibre" or (
            following.get("operation") == "sale_state" and following.get("changes", {}).get("state") == "active"
        )
    return False


def blocks_modification(job: dict[str, Any], request: dict[str, Any]) -> bool:
    status = effective_job_status(job)
    if status in ("queued", "running"):
        return True
    if status in UNRESOLVED_STATUSES:
        return modifications_overlap(job["platform"], job["request"], request)
    return False
