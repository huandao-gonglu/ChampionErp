# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import html as html_module
import re
import urllib.parse
from copy import deepcopy
from html.parser import HTMLParser
from typing import Any

from erp_web.product_model.sku_model import collected_skus
from erp_web.product_model import (
    default_product_model,
    normalize_image_pool,
    parse_dimensions_text,
)
from erp_web.product_model.common import normalize_list
from erp_web.services import html_extract_service
from erp_web.stores.product_store import normalize_product_fields
from erp_shared.source_parser import (
    extract_text_pattern,
    infer_list_from_text,
    _clean_attribute_key,
    _clean_attribute_value,
    _add_attribute,
    _extract_balanced_json_after,
    _json_object_after,
    _AttributeTableParser,
    _section_after_id,
    extract_1688_attribute_table,
    _iter_cpv_rows,
    extract_1688_context_data,
    extract_1688_attributes,
    extract_1688_sku,
    _weight_text_to_kg,
    collect_product_image_urls,
    _DetailImageParser,
    extract_1688_detail_images,
)
































def finalize_collected_product(
    product: dict[str, Any],
    platform: str,
    page_url: str = "",
    *,
    image_refs: list[Any] | None = None,
    price: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Map parser output to the canonical product/source model."""

    product = deepcopy(product if isinstance(product, dict) else {})
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    dims = product.get("dimensions")
    normalized_refs = list(image_refs or source.get("images") or [])
    source.update(
        {
            "source_url": str(source.get("source_url") or page_url or "").strip(),
            "source_platform": str(source.get("source_platform") or platform or "").strip().lower(),
            "title": str(source.get("title") or product.get("name") or "").strip(),
            "price": str(source.get("price") or price or product.get("cost") or "").strip(),
            "currency": str(source.get("currency") or currency or "").strip(),
            "images": normalize_list(normalized_refs),
            "image_pool": normalize_image_pool(normalized_refs, platform),
            "dimensions": source.get("dimensions") if isinstance(source.get("dimensions"), dict) and any(source.get("dimensions").values()) else parse_dimensions_text(
                dims,
                default_unit="mm" if str(platform).strip().lower() == "1688" else "cm",
            ),
            "weight_kg": str(source.get("weight_kg") or product.get("weight_kg") or "").strip(),
            "material": str(source.get("material") or (normalize_list(product.get("materials")) or [""])[0] or "").strip(),
            "package_contents": normalize_list(source.get("package_contents") or product.get("package_includes")),
            "skus": deepcopy(source.get("skus") or product.get("sku_items") or []),
            "brand": str(source.get("brand") or product.get("brand") or "").strip(),
            "model": str(source.get("model") or product.get("model") or "").strip(),
            "sku": str(source.get("sku") or product.get("sku") or "").strip(),
        }
    )
    if isinstance(product.get("attributes"), dict) and product.get("attributes"):
        source["attributes"] = deepcopy(product["attributes"])
    product["source"] = source
    product["sku_items"] = collected_skus(source)
    return normalize_product_fields(product)








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
        text = html_extract_service.html_to_text(html)
    product = default_product_model()
    product["source"]["source_url"] = page_url
    product["source"]["source_platform"] = "1688"
    product["source_text"] = text

    context = extract_1688_context_data(html)
    attrs = extract_1688_attributes(text, html)
    sku_props = context.get("sku_props") if isinstance(context.get("sku_props"), dict) else {}
    for key, values in sku_props.items():
        if isinstance(values, list) and values:
            _add_attribute(attrs, key, values)
    title = str(context.get("title") or "").strip() or page_title or html_extract_service.extract_page_title(html) or extract_text_pattern(text, [r"产品名称[:：]\s*([^\n]+)", r"商品名称[:：]\s*([^\n]+)", r"标题[:：]\s*([^\n]+)"])
    if title:
        product["name"] = re.sub(r"\s*-\s*阿里巴巴\s*$", "", title.strip())
        product.update({key: value for key, value in html_extract_service.infer_product_from_title(title).items() if value})

    brand = attrs.get("品牌") or attrs.get("厂牌") or extract_text_pattern(text, [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand

    category = extract_text_pattern(text, [r"类目[:：]\s*([^\n]+)", r"品类[:：]\s*([^\n]+)"])
    if category:
        product["category"] = category

    target_customer = extract_text_pattern(text, [r"目标买家[:：]\s*([^\n]+)"])
    if target_customer:
        product["target_customer"] = target_customer

    dimensions = str(context.get("dimensions_text") or "").strip() or attrs.get("规格") or attrs.get("尺寸") or attrs.get("产品尺寸") or extract_text_pattern(text, [r"(?:规格|尺寸|长宽高)[:：]\s*([^\n]+)"])
    if dimensions:
        product["dimensions"] = dimensions

    weight = str(context.get("weight_kg") or "").strip() or _weight_text_to_kg(attrs.get("重量") or attrs.get("净重") or attrs.get("毛重") or attrs.get("自重")) or _weight_text_to_kg(extract_text_pattern(text, [r"重量[:：]\s*([^\n]+)"]))
    if weight:
        product["weight_kg"] = weight

    materials = attrs.get("材质") or attrs.get("材料") or attrs.get("面料") or attrs.get("扇叶材质") or extract_text_pattern(text, [r"(?:材质|材料|面料)[:：]\s*([^\n]+)"])
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
    if isinstance(context.get("skus"), list) and context["skus"]:
        product["sku_items"] = deepcopy(context["skus"])

    price, currency = (str(context.get("price") or "").strip(), str(context.get("currency") or "").strip()) if context.get("price") else html_extract_service.extract_price_currency(html)
    dims, parsed_weight = html_extract_service.extract_measurements(html)
    if dims and not product.get("dimensions"):
        product["dimensions"] = dims
    if parsed_weight and not product.get("weight_kg"):
        product["weight_kg"] = parsed_weight

    # 公共图与规格图分别建立引用；统一由采集入库边界下载一次，保留原 URL
    # 作为稳定身份，不能同时把下载路径和远程 URL 创建成两个素材。
    public_images = list(context.get("main_image_urls") or [])
    if not public_images:
        sku_images = {row.get("image") for row in context.get("skus", [])}
        public_images = [url for url in collect_product_image_urls(html, page_url, image_urls)
                         if url not in sku_images]
    detail_url = context.get("detail_url")
    if detail_url:
        payload = (raw_data.get("detail_html") if isinstance(raw_data, dict) and "detail_html" in raw_data
                   else html_extract_service.fetch_1688_detail_html(detail_url))
        public_images.extend(extract_1688_detail_images(payload, detail_url))
    public_images = list(dict.fromkeys(
        html_extract_service.normalize_image_url(url, page_url) for url in public_images if url
    ))

    return finalize_collected_product(
        product,
        "1688",
        page_url,
        image_refs=public_images,
        price=price,
        currency=currency,
    )


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
        text = html_extract_service.html_to_text(html)

    product = default_product_model()
    product["source"]["source_url"] = page_url
    product["source"]["source_platform"] = "amazon"
    product["source_text"] = text

    title = page_title or html_extract_service.extract_page_title(html)
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in html_extract_service.infer_product_from_title(title).items() if value})

    price, currency = html_extract_service.extract_price_currency(html)
    dims, parsed_weight = html_extract_service.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight

    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)

    return finalize_collected_product(
        product,
        "amazon",
        page_url,
        image_refs=extracted_image_urls,
        price=price,
        currency=currency,
    )


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
        text = html_extract_service.html_to_text(html)
    product = default_product_model()
    product["source"]["source_url"] = page_url
    product["source"]["source_platform"] = "unknown"
    product["source_text"] = text
    title = page_title or html_extract_service.extract_page_title(html) or extract_text_pattern(text, [r"(?:title|标题|商品名称)[:：]\s*([^\n]+)"])
    if title:
        product["name"] = title.strip()
    brand = extract_text_pattern(text, [r"(?:brand|品牌)[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand.strip()
    price, currency = html_extract_service.extract_price_currency(html)
    dims, parsed_weight = html_extract_service.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight
    if not image_urls:
        image_urls = html_extract_service.extract_product_image_urls(html, page_url, limit=20)
    image_urls = list(dict.fromkeys([str(item).strip() for item in image_urls if str(item).strip()]))[:20]
    return finalize_collected_product(
        product,
        "unknown",
        page_url,
        image_refs=image_urls,
        price=price,
        currency=currency,
    )


__all__ = [
    "collect_product_image_urls",
    "extract_1688_detail_images",
    "extract_1688_attribute_table",
    "extract_1688_attributes",
    "extract_1688_context_data",
    "extract_1688_sku",
    "extract_text_pattern",
    "infer_list_from_text",
    "parse_1688_product",
    "parse_amazon_product",
    "parse_generic_product",
    "finalize_collected_product",
]
