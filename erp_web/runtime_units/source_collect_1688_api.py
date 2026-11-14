# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from erp_web.context import get_context
from erp_web.product_model import default_collect_diagnostics, merge_source_partial_result, parse_dimensions_text
from erp_web.services.alibaba_api_client import AlibabaApiClient, product_detail_method
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


def request_1688_product_detail(config: dict, offer_id: str) -> dict:
    product = AlibabaApiClient(config).product_detail(offer_id)
    return {"http_status": 200, "raw": product, "request": {"method": product_detail_method(config), "offer_id": offer_id}}


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).strip()
        if text:
            return text
    return ""


def nested_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values: list[Any] = [value]
        for item in value.values():
            values.extend(nested_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(nested_values(item))
        return values
    return []


def find_first_key(raw: Any, keys: set[str]) -> Any:
    lowered = {key.lower() for key in keys}
    for node in nested_values(raw):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if str(key).lower() in lowered and value not in (None, "", [], {}):
                return value
    return None


def collect_image_urls(raw: Any) -> list[str]:
    images: list[str] = []
    image_keys = {"image", "images", "imageurl", "imageurls", "picurl", "picurls", "mainimage", "mainimageurl", "detailimages"}
    for node in nested_values(raw):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if str(key).lower() not in image_keys:
                continue
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate = first_text(candidate.get("url"), candidate.get("imageUrl"), candidate.get("picUrl"), candidate.get("path"))
                url = str(candidate or "").strip()
                if url.startswith("//"):
                    url = f"https:{url}"
                if url.startswith(("http://", "https://")) and url not in images:
                    images.append(url)
    return images[:30]


def collect_attributes(raw: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    attr_payload = find_first_key(raw, {"attributes", "productAttribute", "productAttributes", "saleInfo", "productFeatureList"})
    candidates = attr_payload if isinstance(attr_payload, list) else [attr_payload]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = first_text(item.get("name"), item.get("key"), item.get("attributeName"), item.get("fidName"))
        value = first_text(item.get("value"), item.get("attributeValue"), item.get("val"), item.get("featureValue"))
        if name and value:
            attrs[name[:80]] = value[:300]
    for key in ("brand", "brandName", "model", "categoryName", "unit", "minOrderQuantity"):
        value = find_first_key(raw, {key})
        text = first_text(value)
        if text:
            attrs.setdefault(key, text[:300])
    return attrs


def collect_skus(raw: Any) -> list[dict[str, str]]:
    sku_payload = find_first_key(raw, {"skuInfos", "skuInfo", "skuList", "productSkuInfos"})
    rows = sku_payload if isinstance(sku_payload, list) else []
    skus: list[dict[str, str]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        attrs = dict(item["attributes"]) if isinstance(item.get("attributes"), dict) else {}
        sku_image = ""
        for attribute in item.get("skuAttributes") or []:
            if isinstance(attribute, dict):
                if attribute.get("attributeName") and attribute.get("value"):
                    attrs[attribute["attributeName"]] = attribute["value"]
                sku_image = sku_image or attribute.get("skuImageUrl") or ""
        price = find_first_key(item, {"price", "discountPrice", "retailPrice", "consignPrice"})
        stock = find_first_key(item, {"stock", "amountOnSale", "canBookCount", "inventory"})
        name = first_text(item.get("skuName"), item.get("name"), " / ".join(str(v) for v in attrs.values()), item.get("specId"), f"SKU {index + 1}")
        skus.append(
            {
                "id": first_text(item.get("skuId"), item.get("id")),
                "spec_id": first_text(item.get("specId")),
                "name": name,
                "options": {str(key): str(value) for key, value in attrs.items() if value is not None},
                "price": first_text(price),
                "stock": str(stock) if stock is not None else "",
                "image": first_text(item.get("imageUrl"), item.get("picUrl"), item.get("image"), sku_image),
            }
        )
    return skus


def parse_1688_api_product(raw: dict[str, Any], source_url: str, offer_id: str) -> dict[str, Any]:
    title = first_text(
        find_first_key(raw, {"subject", "title", "name", "productName", "offerTitle"}),
        f"1688 商品 {offer_id}",
    )
    price = first_text(find_first_key(raw, {"price", "priceRange", "salePrice", "consignPrice", "promotionPrice"}))
    attrs = collect_attributes(raw)
    images = collect_image_urls(raw)
    skus = collect_skus(raw)
    collected_offer = first_text(find_first_key(raw, {"productID", "productId", "offerId"}), offer_id)
    for sku in skus:
        sku["offer_id"] = collected_offer
    dimensions = parse_dimensions_text(
        first_text(
            find_first_key(raw, {"dimensions", "size", "specification", "规格"}),
            attrs.get("规格"),
        ),
        default_unit="mm",
    )
    weight = first_text(find_first_key(raw, {"weight", "grossWeight", "netWeight"}), attrs.get("重量"))
    material = first_text(find_first_key(raw, {"material", "材质"}), attrs.get("材质"))
    brand = first_text(find_first_key(raw, {"brand", "brandName"}), attrs.get("brand"), attrs.get("brandName"), attrs.get("品牌"))
    model = first_text(find_first_key(raw, {"model", "货号"}), attrs.get("model"), attrs.get("型号"), attrs.get("货号"))
    return {
        "source_url": source_url,
        "source_platform": "1688",
        "title": title,
        "price": price,
        "currency": "CNY" if price else "",
        "images": images,
        "dimensions": dimensions,
        "weight_kg": re.sub(r"[^0-9.,]", "", weight),
        "material": material,
        "skus": skus,
        "attributes": attrs,
        "brand": brand,
        "model": model,
        "collect_status": "success",
        "api_offer_id": offer_id,
        "api_raw": deepcopy(raw),
    }


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
            "api_method": product_detail_method(api_config),
        }
    )
    try:
        response = request_1688_product_detail(api_config, offer_id)
        raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
        source_updates = parse_1688_api_product(raw, source_url, offer_id)
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
        diagnostics["error_code"] = collect_error_code("1688", "api", error_reason) if error_reason else ""
        diagnostics["error_message"] = "1688 官方 API 采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
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
    "parse_1688_api_product",
    "request_1688_product_detail",
]
