"""在线管理 HTTP 与领域服务的薄边界。"""
from __future__ import annotations

from typing import Any
from pydantic import ValidationError
from erp_web.context import get_context
from erp_web.stores.online_product_store import OnlineConflict


def read(query: dict[str, str]) -> dict[str, Any]:
    service = get_context().online_products
    if query.get("id"):
        return service.detail(query["id"])
    try:
        page = max(1, int(query.get("page") or 1))
    except ValueError:
        page = 1
    return service.list(query.get("platform") or "mercadolibre", query=query.get("q", ""), status=query.get("status", ""), market=query.get("market", ""), page=page)


def mutate(action: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    service = get_context().online_products
    try:
        if action == "sync":
            result = service.sync(body["platform"], body["idempotency_key"])
        elif action == "change":
            result = service.change(body)
        elif action == "reconcile":
            result = service.reconcile(body["job_id"])
        else:
            result = service.retry(body["job_id"], body["idempotency_key"])
        return result, 200
    except OnlineConflict as exc:
        return {"ok": False, "error": str(exc), "error_code": "ONLINE_CONFLICT"}, 409
    except (ValueError, ValidationError) as exc:
        return {"ok": False, "error": str(exc), "error_code": "ONLINE_INVALID_REQUEST"}, 400
