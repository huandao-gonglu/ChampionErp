# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any

from erp_web.product_model import default_collect_diagnostics, merge_source_partial_result, parse_dimensions_text

from .collect_helpers import (
    apply_claimed_platform_drafts,
    collect_error_code,
    collect_time_iso,
    finalize_collect_diagnostics,
    normalize_collect_source_images,
    snapshot_field_flags,
)
from .image_pool_core import current_image_pool, current_source_images
from .product_store import load_app_config, load_product, load_products_index, normalize_list, save_product


DEFAULT_1688_DETAIL_API_URL = "https://gw.open.1688.com/openapi/param2/1/com.alibaba.product/alibaba.product.get"


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


def normalize_1688_api_config(config: dict[str, Any] | None = None) -> dict[str, str]:
    app_cfg = config if isinstance(config, dict) else load_app_config()
    if any(key in app_cfg for key in ("app_key", "app_secret", "base_url", "method")):
        raw = app_cfg
    else:
        raw = app_cfg.get("1688_api") if isinstance(app_cfg.get("1688_api"), dict) else {}
    return {
        "app_key": str(raw.get("app_key") or "").strip(),
        "app_secret": str(raw.get("app_secret") or "").strip(),
        "access_token": str(raw.get("access_token") or "").strip(),
        "base_url": str(raw.get("base_url") or DEFAULT_1688_DETAIL_API_URL).strip(),
        "method": str(raw.get("method") or "alibaba.product.get").strip(),
        "api_version": str(raw.get("api_version") or "1.0").strip(),
        "sign_method": str(raw.get("sign_method") or "md5").strip().lower(),
        "timeout_seconds": str(raw.get("timeout_seconds") or "20").strip(),
    }


def ensure_1688_api_ready(config: dict[str, str]) -> None:
    if not config.get("app_key") or not config.get("app_secret"):
        raise RuntimeError("1688_API_CREDENTIALS_MISSING：请先填写 1688 App Key 和 App Secret。")
    if not config.get("base_url"):
        raise RuntimeError("1688_API_BASE_URL_MISSING：请先填写 1688 API 请求地址。")


def sign_1688_params(params: dict[str, Any], app_secret: str) -> str:
    signing_items = [(str(key), str(value)) for key, value in params.items() if key != "sign" and value not in (None, "")]
    signing_items.sort(key=lambda item: item[0])
    sign_text = "".join(f"{key}{value}" for key, value in signing_items)
    sign_text = f"{app_secret}{sign_text}{app_secret}"
    return hashlib.md5(sign_text.encode("utf-8")).hexdigest().upper()


def build_1688_api_params(config: dict[str, str], offer_id: str, fields: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {
        "app_key": config["app_key"],
        "method": config["method"],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "format": "json",
        "v": config["api_version"],
        "sign_method": config.get("sign_method") or "md5",
        "productID": offer_id,
        "productId": offer_id,
        "offerId": offer_id,
    }
    if config.get("access_token"):
        params["access_token"] = config["access_token"]
        params["session"] = config["access_token"]
    if fields:
        params["fields"] = fields
    params["sign"] = sign_1688_params(params, config["app_secret"])
    return params


def request_1688_product_detail(config: dict[str, str], offer_id: str) -> dict[str, Any]:
    try:
        timeout = max(3, min(120, int(float(config.get("timeout_seconds") or "20"))))
    except Exception:
        timeout = 20
    params = build_1688_api_params(config, offer_id)
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        config["base_url"],
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"1688_API_HTTP_{exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"1688_API_NETWORK_ERROR：{exc.reason}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"1688_API_INVALID_JSON：{text[:500]}") from exc
    if isinstance(payload, dict) and payload.get("error_response"):
        error = payload.get("error_response") if isinstance(payload.get("error_response"), dict) else {}
        message = str(error.get("sub_msg") or error.get("msg") or payload.get("error_response") or "")
        raise RuntimeError(f"1688_API_ERROR：{message}")
    if isinstance(payload, dict) and (payload.get("errorCode") or payload.get("error_message") or payload.get("errorMsg")):
        raise RuntimeError(f"1688_API_ERROR：{payload.get('error_message') or payload.get('errorMsg') or payload.get('errorCode')}")
    return {"http_status": status, "raw": payload, "request": {"base_url": config["base_url"], "method": config["method"], "offer_id": offer_id}}


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
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        price = find_first_key(item, {"price", "discountPrice", "retailPrice", "consignPrice"})
        stock = find_first_key(item, {"stock", "amountOnSale", "canBookCount", "inventory"})
        name = first_text(item.get("skuName"), item.get("name"), item.get("specId"), attrs.get("name"), f"SKU {index + 1}")
        skus.append(
            {
                "id": first_text(item.get("skuId"), item.get("id"), index),
                "name": name,
                "spec1": first_text(attrs.get("color"), attrs.get("颜色"), item.get("color")),
                "spec2": first_text(attrs.get("size"), attrs.get("尺码"), attrs.get("规格"), item.get("size")),
                "price": first_text(price),
                "stock": first_text(stock),
                "image": first_text(item.get("imageUrl"), item.get("picUrl"), item.get("image")),
            }
        )
    return skus[:200]


def parse_1688_api_product(raw: dict[str, Any], source_url: str, offer_id: str) -> dict[str, Any]:
    title = first_text(
        find_first_key(raw, {"subject", "title", "name", "productName", "offerTitle"}),
        f"1688 商品 {offer_id}",
    )
    description = first_text(find_first_key(raw, {"description", "desc", "detail", "productDetail"}))
    price = first_text(find_first_key(raw, {"price", "priceRange", "salePrice", "consignPrice", "promotionPrice"}))
    attrs = collect_attributes(raw)
    images = collect_image_urls(raw)
    skus = collect_skus(raw)
    dimensions = parse_dimensions_text(first_text(find_first_key(raw, {"dimensions", "size", "specification", "规格"}), attrs.get("规格")))
    weight = first_text(find_first_key(raw, {"weight", "grossWeight", "netWeight"}), attrs.get("重量"))
    material = first_text(find_first_key(raw, {"material", "材质"}), attrs.get("材质"))
    brand = first_text(find_first_key(raw, {"brand", "brandName"}), attrs.get("brand"), attrs.get("brandName"), attrs.get("品牌"))
    model = first_text(find_first_key(raw, {"model", "货号"}), attrs.get("model"), attrs.get("型号"), attrs.get("货号"))
    bullets = normalize_list([f"{key}: {value}" for key, value in list(attrs.items())[:6]])
    return {
        "source_url": source_url,
        "source_platform": "1688",
        "title": title,
        "price": price,
        "currency": "CNY" if price else "",
        "description": description,
        "bullets": bullets,
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
    api_config = normalize_1688_api_config(config)
    ensure_1688_api_ready(api_config)
    started_at = collect_time_iso()
    original_product = load_product()
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": "api",
            "source_url": source_url,
            "normalized_url": source_url,
            "platform_detected": "1688",
            "started_at": started_at,
            "api_offer_id": offer_id,
            "api_method": api_config.get("method") or "",
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
        if isinstance(source_updates.get("attributes"), dict) and source_updates["attributes"]:
            merged["attributes"] = source_updates["attributes"]
        if isinstance(source_updates.get("skus"), list) and source_updates["skus"]:
            merged["sku_items"] = source_updates["skus"]
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append({"started_at": started_at, "finished_at": diagnostics["finished_at"], "mode": "api", "platform": "1688", "success": diagnostics["success"], "partial_success": diagnostics["partial_success"], "error_code": diagnostics["error_code"], "error_message": diagnostics["error_message"]})
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {"ok": diagnostics["success"], "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "error": "" if diagnostics["success"] else diagnostics["error_message"], "next_action": diagnostics.get("next_action", "")}
    except Exception as exc:
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics["success"] = False
        diagnostics["partial_success"] = False
        diagnostics["error_code"] = "1688_API_FAILED"
        diagnostics["error_message"] = str(exc)
        diagnostics["next_action"] = "请检查 1688 App Key/App Secret、接口权限、API 地址和商品链接；如果权限未开通，先在开放平台申请商品详情接口。"
        diagnostics["checked_at"] = collect_time_iso()
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        if source_url:
            merged["source"]["source_url"] = source_url
            merged["source"]["source_platform"] = "1688"
        merged["source"]["collect_status"] = "failed"
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "error": str(exc), "next_action": diagnostics["next_action"]}


__all__ = [
    "build_1688_api_params",
    "collect_1688_product_via_api",
    "extract_1688_offer_id",
    "normalize_1688_api_config",
    "parse_1688_api_product",
    "request_1688_product_detail",
    "sign_1688_params",
]
