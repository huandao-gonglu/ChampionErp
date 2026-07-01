# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import urllib.parse
from copy import deepcopy
from typing import Any

from erp_web.product_model import default_product_model, parse_dimensions_text
from erp_web.services import html_extract_service as legacy

from .product_store import normalize_list, normalize_product_fields, normalize_space
from .runtime_common import SOURCE_DIR

def extract_text_pattern(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.M)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return ""


def infer_list_from_text(value: str) -> list[str]:
    cleaned = re.sub(r"[，;；/]+", "、", value)
    return [item.strip() for item in cleaned.split("、") if item.strip()]


def extract_1688_attributes(text: str, html: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        match = re.match(r"^([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})\s*[:：]\s*(.+)$", line)
        if not match:
            continue
        key = normalize_space(match.group(1))
        value = normalize_space(match.group(2))
        if not key or not value or len(value) > 160:
            continue
        attrs.setdefault(key, value)

    targeted = {
        "品牌": [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"],
        "货号": [r"货号[:：]\s*([^\n]+)", r"产品货号[:：]\s*([^\n]+)", r"款号[:：]\s*([^\n]+)"],
        "SKU": [r"SKU[:：]\s*([^\n]+)", r"sku[:：]\s*([^\n]+)"],
        "型号": [r"型号[:：]\s*([^\n]+)", r"规格型号[:：]\s*([^\n]+)"],
        "材质": [r"材质[:：]\s*([^\n]+)", r"材料[:：]\s*([^\n]+)", r"面料[:：]\s*([^\n]+)"],
        "规格": [r"规格[:：]\s*([^\n]+)", r"尺寸[:：]\s*([^\n]+)", r"产品尺寸[:：]\s*([^\n]+)"],
        "重量": [r"重量[:：]\s*([^\n]+)", r"净重[:：]\s*([^\n]+)", r"毛重[:：]\s*([^\n]+)"],
        "包装清单": [r"包装清单[:：]\s*([^\n]+)", r"包装内容[:：]\s*([^\n]+)", r"包装说明[:：]\s*([^\n]+)"],
    }
    for key, patterns in targeted.items():
        if attrs.get(key):
            continue
        value = extract_text_pattern(text, patterns)
        if value:
            attrs[key] = value

    if not attrs:
        matches = re.findall(r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,24})\s*[:：]\s*([^\n]{1,160})", html)
        for key, value in matches[:40]:
            key = normalize_space(key)
            value = normalize_space(value)
            if key and value and key not in attrs:
                attrs[key] = value
    return attrs


def extract_1688_sku(page_url: str, text: str, html: str, attrs: dict[str, str]) -> str:
    candidates = [
        attrs.get("SKU", ""),
        attrs.get("货号", ""),
        attrs.get("型号", ""),
        attrs.get("款号", ""),
        attrs.get("产品编号", ""),
        attrs.get("商品编号", ""),
    ]
    match = re.search(r"/offer/(\d+)\.html", page_url)
    if match:
        candidates.append(match.group(1))
    for pattern in [
        r'"offerId"\s*[:=]\s*"?(\d+)"?',
        r'"itemId"\s*[:=]\s*"?(\d+)"?',
        r'"productId"\s*[:=]\s*"?(\d+)"?',
        r'货号[:：]\s*([A-Za-z0-9\-_]+)',
        r'SKU[:：]\s*([A-Za-z0-9\-_]+)',
    ]:
        found = re.search(pattern, f"{text}\n{html}", flags=re.I)
        if found:
            candidates.append(found.group(1))
    for value in candidates:
        value = normalize_space(str(value))
        if value:
            return value[:80]
    return ""


def populate_source_from_legacy_product(product: dict[str, Any], platform: str, page_url: str = "") -> dict[str, Any]:
    product = deepcopy(product if isinstance(product, dict) else {})
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    image_refs = []
    image_refs.extend(normalize_list(product.get("source_images")))
    image_refs.extend(normalize_list(product.get("source_image_urls")))
    image_refs.extend(normalize_list(product.get("detail_images")))
    image_refs.extend(normalize_list(product.get("detail_image_urls")))
    dims = product.get("dimensions")
    source.update(
        {
            "source_url": str(source.get("source_url") or product.get("source_url") or page_url or "").strip(),
            "source_platform": str(source.get("source_platform") or platform or product.get("source_platform") or "").strip().lower(),
            "title": str(source.get("title") or product.get("name") or "").strip(),
            "price": str(source.get("price") or product.get("detected_price") or product.get("cost") or "").strip(),
            "currency": str(source.get("currency") or product.get("detected_currency") or "").strip(),
            "bullets": normalize_list(source.get("bullets") or product.get("selling_points")),
            "description": str(source.get("description") or product.get("description") or "").strip(),
            "images": normalize_list(source.get("images") or image_refs),
            "dimensions": source.get("dimensions") if isinstance(source.get("dimensions"), dict) and any(source.get("dimensions").values()) else parse_dimensions_text(dims),
            "weight_kg": str(source.get("weight_kg") or product.get("weight_kg") or "").strip(),
            "material": str(source.get("material") or (normalize_list(product.get("materials")) or [""])[0] or "").strip(),
            "package_contents": normalize_list(source.get("package_contents") or product.get("package_includes")),
            "skus": deepcopy(source.get("skus") or product.get("sku_items") or []),
        }
    )
    if isinstance(product.get("attributes"), dict) and product.get("attributes"):
        source["attributes"] = deepcopy(product["attributes"])
    product["source"] = source
    return product


def collect_product_image_urls(html: str, page_url: str, snapshot_image_urls: list[Any] | None = None, limit: int = 20) -> list[str]:
    """Prefer product-image candidates from HTML over raw DOM image order.

    1688 pages contain many UI icons before the real product gallery in
    ``document.images``.  The HTML extractor finds product-specific fields such
    as og:image/mainUrl/imageUrl first, then we append DOM image URLs as a
    fallback.
    """

    candidates: list[Any] = []
    try:
        candidates.extend(legacy.extract_product_image_urls(html, page_url, limit=max(limit * 2, 20)))
    except Exception:
        pass
    candidates.extend(snapshot_image_urls or [])

    clean: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        raw = str(item or "").strip()
        if not raw:
            continue
        try:
            url = legacy.normalize_image_url(raw, page_url)
        except Exception:
            url = raw.replace("\\/", "/").strip()
        lowered = url.lower()
        parsed_path = urllib.parse.urlparse(url).path.lower()
        if not url or url in seen:
            continue
        if parsed_path.endswith(".svg") or any(skip in lowered for skip in ["sprite", "logo", "avatar", "icon"]):
            continue
        seen.add(url)
        clean.append(url)
        if len(clean) >= limit:
            break
    return clean


def parse_1688_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)
    product = default_product_model()
    product["source_url"] = page_url
    product["source_text"] = text

    attrs = extract_1688_attributes(text, html)
    title = page_title or legacy.extract_page_title(html) or extract_text_pattern(text, [r"产品名称[:：]\s*([^\n]+)", r"商品名称[:：]\s*([^\n]+)", r"标题[:：]\s*([^\n]+)"])
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    brand = extract_text_pattern(text, [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand

    category = extract_text_pattern(text, [r"类目[:：]\s*([^\n]+)", r"品类[:：]\s*([^\n]+)"])
    if category:
        product["category"] = category

    target_customer = extract_text_pattern(text, [r"目标买家[:：]\s*([^\n]+)"])
    if target_customer:
        product["target_customer"] = target_customer

    dimensions = extract_text_pattern(text, [r"(?:规格|尺寸|长宽高)[:：]\s*([^\n]+)"])
    if dimensions:
        product["dimensions"] = dimensions

    weight = extract_text_pattern(text, [r"重量[:：]\s*([^\n]+)"])
    if weight:
        product["weight_kg"] = re.sub(r"[^\d.,]", "", weight)

    materials = extract_text_pattern(text, [r"(?:材质|材料|面料)[:：]\s*([^\n]+)"])
    if materials:
        product["materials"] = infer_list_from_text(materials)

    package = extract_text_pattern(text, [r"(?:包装清单|包装内容|包装说明)[:：]\s*([^\n]+)"])
    if package:
        product["package_includes"] = infer_list_from_text(package)

    if attrs:
        product["attributes"] = attrs
    sku = extract_1688_sku(page_url, text, html, attrs)
    if sku:
        product["sku"] = sku
    model = attrs.get("型号") or attrs.get("货号") or attrs.get("SKU") or ""
    if model:
        product["model"] = model[:80]

    bullets: list[str] = []
    for line in text.splitlines():
        value = line.strip(" -•\t")
        if 6 <= len(value) <= 80 and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in value):
            if not value.startswith(("品牌", "型号", "规格", "尺寸", "包装", "材质", "价格")):
                bullets.append(value)
    product["selling_points"] = bullets[:6]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims and not product.get("dimensions"):
        product["dimensions"] = dims
    if parsed_weight and not product.get("weight_kg"):
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "1688", page_url))


def parse_amazon_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)

    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "Amazon"
    product["source_text"] = text

    title = page_title or legacy.extract_page_title(html)
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    bullets = []
    try:
        bullets = legacy.extract_amazon_bullets(html)
    except Exception:
        bullets = []
    if bullets:
        product["selling_points"] = bullets[:10]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "amazon", page_url))

def parse_amazon_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)

    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "Amazon"
    product["source_text"] = text

    title = page_title or legacy.extract_page_title(html)
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    bullets = []
    try:
        bullets = legacy.extract_amazon_bullets(html)
    except Exception:
        bullets = []
    if bullets:
        product["selling_points"] = bullets[:10]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "amazon", page_url))


def parse_generic_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)
    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "unknown"
    product["source_text"] = text
    title = page_title or legacy.extract_page_title(html) or extract_text_pattern(text, [r"(?:title|标题|商品名称)[:：]\s*([^\n]+)"])
    if title:
        product["name"] = title.strip()
    brand = extract_text_pattern(text, [r"(?:brand|品牌)[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand.strip()
    bullets = [line.strip(" -•\t") for line in text.splitlines() if 8 <= len(line.strip()) <= 120][:8]
    if bullets:
        product["selling_points"] = bullets
    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight
    if not image_urls:
        image_urls = legacy.extract_product_image_urls(html, page_url, limit=20)
    image_urls = list(dict.fromkeys([str(item).strip() for item in image_urls if str(item).strip()]))[:20]
    product["source_image_urls"] = image_urls[:7]
    product["detail_image_urls"] = image_urls[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "unknown", page_url))


__all__ = [
    "collect_product_image_urls",
    "extract_1688_attributes",
    "extract_1688_sku",
    "extract_text_pattern",
    "infer_list_from_text",
    "parse_1688_product",
    "parse_amazon_product",
    "parse_generic_product",
    "populate_source_from_legacy_product",
]
