"""在线管理 HTTP 与领域服务的薄边界。"""
from __future__ import annotations

from typing import Any
from pydantic import ValidationError
from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.online_products import OnlineConflictResponse, OnlineAttributeQuery, RefreshDetailRequest, RefreshStatusRequest
from erp_web.stores.online_product_store import OnlineConflict


def read(query: dict[str, str]) -> dict[str, Any]:
    service = get_context().online_products
    if query.get("job_id"):
        return service.job_detail(query["job_id"])
    if query.get("id"):
        return service.detail(query["id"])
    try:
        page = max(1, int(query.get("page") or 1))
    except ValueError:
        page = 1
    return service.list(query.get("platform") or "mercadolibre", query=query.get("q", ""), status=query.get("status", ""), market=query.get("market", ""), page=page, catalog=query.get("catalog", "current"), warehouse_id=query.get('warehouse_id', ''))


def read_source_images(query: dict[str, str]) -> dict[str, Any]:
    listing_id = query.get("listing_id", "").strip()
    if not listing_id:
        raise ValueError("listing_id 不能为空")
    return get_context().online_products.source_images(listing_id)


def read_attributes(query: dict[str, str]) -> dict[str, Any]:
    return get_context().online_products.attribute_options(OnlineAttributeQuery.model_validate(query))


def export_ozon(body: dict[str, Any], *, download: bool = False) -> tuple[dict[str, Any], int]:
    try:
        service = get_context().online_products.ozon_export
        return (service.download(body) if download else service.preview(body)), 200
    except OnlineConflict as exc:
        return OnlineConflictResponse(error=str(exc), conflict_job_id=exc.job_id).model_dump(exclude_none=True), 409
    except (ValueError, ValidationError) as exc:
        return {"ok": False, "error": str(exc), "error_code": "OZON_EXPORT_INVALID"}, 400


def mutate(action: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    service = get_context().online_products
    try:
        if action == "sync":
            result = service.sync(body["platform"], body["idempotency_key"], catalog=body.get("catalog", "current"))
        elif action == "change":
            result = service.change(body)
        elif action == "change-batch/preview":
            result = service.preview_batch(body)
        elif action == "change-batch":
            result = service.change_batch(body)
        elif action == "reconcile":
            result = service.reconcile(body["job_id"])
        elif action == "close":
            result = service.close_operation(body)
        elif action == "refresh-status":
            result = service.refresh_status(RefreshStatusRequest.model_validate(body).listing_id)
        elif action == "refresh-detail":
            result = service.refresh_detail(RefreshDetailRequest.model_validate(body).listing_id)
        else:
            result = service.retry(body["job_id"], body["idempotency_key"])
        return result, 200
    except OnlineConflict as exc:
        return OnlineConflictResponse(error=str(exc), conflict_job_id=exc.job_id).model_dump(exclude_none=True), 409
    except (PublishAdapterError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": f"平台查询失败，原数据已保留：{exc}", "error_code": "ONLINE_PLATFORM_ERROR"}, 502
    except (ValueError, ValidationError) as exc:
        return {"ok": False, "error": str(exc), "error_code": "ONLINE_INVALID_REQUEST"}, 400
