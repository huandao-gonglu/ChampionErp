# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from erp_web.context import get_context
from erp_web.product_model import default_collect_diagnostics, merge_source_partial_result
from erp_web.services.alibaba_api_client import alibaba_provider
from erp_web.services.alibaba_authorization_service import alibaba_runtime_config

from .collect_helpers import (
    apply_claimed_platform_drafts,
    collect_error_code,
    collect_time_iso,
    finalize_collect_diagnostics,
    normalize_collect_source_images,
    snapshot_field_flags,
)
from .image_pool_core import current_image_pool, current_source_images


def extract_1688_offer_id(url_or_id: str) -> str:
    text = str(url_or_id or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{6,}", text):
        return text
    patterns = [
        r"/offer/(\d+)\.html",
        r"[?&](?:offerId|offer_id|productId|product_id|itemId|item_id)=(\d+)",
        r"/(\d{8,})(?:[/?#.]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def resolve_1688_api_config(config: dict[str, Any] | None = None) -> dict:
    """仅使用管理员所选应用及当前客户的对应授权。"""
    return alibaba_runtime_config(get_context().config)


def request_1688_product_detail(config: dict, offer_id: str, source_url: str) -> dict:
    return alibaba_provider(config).get_product(offer_id, source_url)


def collect_1688_product_via_api(
    url: str,
    claim_platforms: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_url = str(url or "").strip()
    offer_id = extract_1688_offer_id(source_url)
    if not offer_id:
        raise RuntimeError("1688_API_OFFER_ID_MISSING：请使用 detail.1688.com/offer/{id}.html 商品详情链接。")
    api_config = resolve_1688_api_config(config)
    started_at = collect_time_iso()
    original_product = get_context().products.collection_product(source_url)
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": "api",
            "source_url": source_url,
            "normalized_url": source_url,
            "platform_detected": "1688",
            "started_at": started_at,
            "api_offer_id": offer_id,
        }
    )
    try:
        response = request_1688_product_detail(api_config, offer_id, source_url)
        source_updates = response["product"]
        source_updates = normalize_collect_source_images(source_updates, "1688", "api", claim_platforms)
        flags = snapshot_field_flags(source_updates)
        error_reason = ""
        if not flags["title_found"]:
            error_reason = "NO_TITLE"
        elif flags["images_found_count"] <= 0:
            error_reason = "NO_IMAGES"
        diagnostics.update(flags)
        diagnostics["http_status"] = str(response.get("http_status") or "")
        diagnostics["api_request"] = response.get("request") or {}
        diagnostics["api_method"] = diagnostics["api_request"].get("method", "")
        diagnostics["error_code"] = collect_error_code("1688", "api", error_reason) if error_reason else ""
        diagnostics["error_message"] = "1688 API 采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any([flags["title_found"], flags["images_found_count"], flags["price_found"], flags["sku_found_count"]])
        diagnostics["success"] = bool(flags["title_found"] and not diagnostics["error_code"])
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, "1688")
        merged = merge_source_partial_result(original_product, source_updates, diagnostics)
        original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
        if source_url and source_url != original_url:
            merged.pop("product_id", None)
            merged.pop("id", None)
        merged["source"]["source_url"] = source_url
        merged["source"]["source_platform"] = "1688"
        if source_updates.get("brand"):
            merged["brand"] = source_updates["brand"]
        if source_updates.get("model"):
            merged["model"] = source_updates["model"]
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append({"started_at": started_at, "finished_at": diagnostics["finished_at"], "mode": "api", "platform": "1688", "success": diagnostics["success"], "partial_success": diagnostics["partial_success"], "error_code": diagnostics["error_code"], "error_message": diagnostics["error_message"]})
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = get_context().products.save_product(merged)
        return {"ok": diagnostics["success"], "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": get_context().products.load_products_index(), "diagnostics": diagnostics, "error": "" if diagnostics["success"] else diagnostics["error_message"], "next_action": diagnostics.get("next_action", "")}
    except Exception as exc:
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics["success"] = False
        diagnostics["partial_success"] = False
        diagnostics["error_code"] = "1688_API_FAILED"
        diagnostics["error_message"] = str(exc)
        diagnostics["next_action"] = "请检查商品链接和当前 1688 买家授权。若平台提示缺少商详权限，请联系 ERP 管理员。"
        diagnostics["checked_at"] = collect_time_iso()
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        if source_url:
            merged["source"]["source_url"] = source_url
            merged["source"]["source_platform"] = "1688"
        merged["source"]["collect_status"] = "failed"
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = get_context().products.save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": get_context().products.load_products_index(), "diagnostics": diagnostics, "error": str(exc), "next_action": diagnostics["next_action"]}


__all__ = [
    "collect_1688_product_via_api",
    "extract_1688_offer_id",
    "resolve_1688_api_config",
    "request_1688_product_detail",
]
