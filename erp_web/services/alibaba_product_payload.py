"""将 1688 商详转换为 ERP 采集资料。"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from erp_web.product_model import parse_dimensions_text


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
