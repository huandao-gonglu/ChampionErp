from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from erp_web.context import get_context

from erp_web.runtime_units.collect_helpers import (claim_products_to_platforms, collect_debug_file_url,
                                                 collect_platform_reason)
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
    if "receipt_id" in body:
        return get_context().collection_receipts.start(body["receipt_id"], _collect_batch, body)
    return _collect_batch(body)


def _collect_batch(body):
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
    if "receipt_id" in body:
        return get_context().collection_receipts.start(body["receipt_id"], _resume_collection, body)
    return _resume_collection(body)


def collection_receipt_payload(body):
    return get_context().collection_receipts.read(body["receipt_id"])


def _resume_collection(body):
    return collect_browser_url(
        product_url=str(body['source_url']),
        platform_hint=str(body.get('platform') or ''),
        browser_tab_id=str(body['browser_tab_id']),
    )


def collection_verification_payload(body: dict[str, Any]) -> ApiResponse:
    return inspect_collection_verification(str(body["browser_tab_id"]), str(body["source_url"]))


def manage_links_payload(body):
    try:
        return get_context().collection_links.manage(body)
    except ValidationError as exc:
        detail = exc.errors(include_input=False)[0]
        reason = (detail.get('ctx') or {}).get('error')
        raise RequestValidationError(str(reason or '链接设置无效，请检查商品 URL 和采集方式。')) from None


def run_link_payload(body):
    context = get_context()
    links = context.collection_links
    key, revision = body['id'], body['revision']
    url, mode, verification = links.target(key, revision)
    _check_platform(collect_platform_reason(mode, [url]))

    def execute(_):
        running_revision = links.begin(key, revision)
        try:
            result = _resume_collection(verification) if verification else collect_source_product(url, mode)
            status, summary = _link_result(result)
        except (ValueError, RuntimeError) as exc:
            status, summary, result = 'failed', {'error': str(exc), 'errorCode': 'COLLECT_FAILED', 'nextAction': ''}, {}
        except Exception:
            links.finish(key, running_revision, 'unconfirmed', {'error': '采集中断，请先在采集箱核对结果。', 'errorCode': 'COLLECT_RESULT_UNCONFIRMED', 'nextAction': ''})
            raise
        return {'ok': True, 'item': links.finish(key, running_revision, status, summary, result.get('verification'))}

    return context.collection_receipts.start(body['receipt_id'], execute, body)


def _link_result(result):
    diagnostics = result.get('diagnostics') or {}
    waiting = bool(result.get('verification'))
    ok = bool(result.get('ok'))
    partial = bool(diagnostics.get('partial_success')) and not ok
    status = 'waiting_verification' if waiting else 'success' if ok else 'failed'
    if diagnostics.get('error_code') in ('DESKTOP_RESULT_UNKNOWN', 'COLLECT_RESULT_UNCONFIRMED'):
        status = 'unconfirmed'
    summary = {
        'error': result.get('error') or '',
        'errorCode': diagnostics.get('error_code') or '',
        'nextAction': result.get('next_action') or diagnostics.get('next_action') or '',
        'message': diagnostics.get('error_message') or '',
        'screenshot': collect_debug_file_url(diagnostics.get('screenshot_path')),
        'snapshot': collect_debug_file_url(diagnostics.get('html_snapshot_path')),
    }
    # 失败响应可能携带全局当前商品；只在本链接真正取得资料时绑定商品。
    if ok or partial:
        product = result.get('product') or {}
        source = product.get('source') or {}
        images = source.get('image_pool') or []
        cover = images[0] if images else {}
        source_images = source.get('images') or []
        summary.update(productId=product.get('product_id') or '', title=source.get('title') or product.get('name') or '',
                       image=cover.get('preview_url') or cover.get('url') or (source_images[0] if source_images else ''), price=str(source.get('price') or ''),
                       currency=source.get('currency') or '', imageCount=len(images or source_images),
                       skuCount=len(product.get('sku_items') or source.get('skus') or []),
                       partial=partial)
    return status, summary


def verify_link_payload(body):
    links = get_context().collection_links
    _, _, verification = links.target(body['id'], body['revision'])
    if not verification:
        raise RequestValidationError('该链接当前没有等待验证的采集。', status_code=409)
    result = collection_verification_payload(verification)
    if result.get('ok') and result['status'] == 'unavailable':
        links.verification_failed(body['id'], body['revision'], result['message'])
    return result


__all__ = [
    "manage_links_payload", "run_link_payload", "verify_link_payload",
    "collection_verification_payload",
    "collection_receipt_payload",
    "resume_collection_payload",
    "claim_products_payload",
    "collect_1688_payload",
    "collect_batch_payload",
    "collect_source_payload",
]
