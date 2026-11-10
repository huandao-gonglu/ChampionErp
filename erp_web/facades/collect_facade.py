from __future__ import annotations

from typing import Any

from erp_web.context import get_context

from erp_web.runtime_units.collect_helpers import claim_products_to_platforms, collect_platform_reason
from erp_web.runtime_units.source_collect_verification import inspect_collection_verification
from erp_web.schemas.requests import RequestValidationError
from erp_web.runtime_units.source_collect_workflows import (
    collect_1688_payload_service,
    collect_batch_products,
    collect_browser_url,
    collect_source_product,
)
from erp_web.schemas.api import ApiResponse

ResponseWithStatus = tuple[ApiResponse, int]


def _check_platform(reason):
    if reason:
        raise RequestValidationError(reason, status_code=409, error_code="DESKTOP_UNAVAILABLE")


def collect_source_payload(body: dict[str, Any]) -> ResponseWithStatus:
    _check_platform(collect_platform_reason(body.get("mode", "browser"), body.get("url")))
    context = get_context()
    try:
        result = collect_source_product(
            body.get("url", ""),
            body.get("mode", "browser"),
            body.get("platform", ""),
            body.get("platforms") if isinstance(body.get("platforms"), list) else None,
        )
        result["productsIndex"] = (
            context.products.load_products_index()
        )
        return result, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def collect_batch_payload(body: dict[str, Any]) -> ApiResponse:
    _check_platform(collect_platform_reason(body.get("mode", "browser"), body.get("urls") or body.get("url")))
    return collect_batch_products(
        body.get("urls") if body.get("urls") is not None else body.get("url", ""),
        body.get("mode", "browser"),
        body.get("platform", ""),
        body.get("platforms") if isinstance(body.get("platforms"), list) else None,
    )


def claim_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = claim_products_to_platforms(
            body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
            selected_markets=body.get("targets") if isinstance(body.get("targets"), list) else [],
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return result, 200 if result.get("ok") else 400


def collect_1688_payload(body: dict[str, Any]) -> ResponseWithStatus:
    _check_platform(collect_platform_reason(body.get("mode", "browser"), body.get("url")))
    try:
        result = collect_1688_payload_service(body)
        status = 200 if result.get("ok") or result.get("status") == "waiting_verification" or (result.get("diagnostics") or {}).get("partial_success") else 400
        return result, status
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def resume_collection_payload(body: dict[str, Any]) -> ApiResponse:
    return collect_browser_url(
        product_url=str(body['source_url']),
        platform_hint=str(body.get('platform') or ''),
        browser_tab_id=str(body['browser_tab_id']),
    )


def collection_verification_payload(body: dict[str, Any]) -> ApiResponse:
    return inspect_collection_verification(str(body["browser_tab_id"]), str(body["source_url"]))


__all__ = [
    "collection_verification_payload",
    "resume_collection_payload",
    "claim_products_payload",
    "collect_1688_payload",
    "collect_batch_payload",
    "collect_source_payload",
]
