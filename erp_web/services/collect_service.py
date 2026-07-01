"""Low-risk collection helpers.

This module only cleans pasted text/HTML and extracts product fields. It does
not bypass CAPTCHA, sliders, login checks, stealth detection, or proxy blocks.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any


VERIFY_MARKERS_1688 = (
    "安全验证",
    "slide.1688.com",
    "请验证身份",
    "验证中心",
    "captcha",
    "punish",
    "anti-bot",
)

NOISE_KEYWORDS = (
    "关注",
    "客服",
    "入驻",
    "立即下单",
    "加入进货单",
    "收藏",
    "铺货",
    "商品评价",
    "退货包运费",
    "准时发货",
    "买家保障",
    "优惠券",
    "跨境专供",
    "物流",
    "运费",
    "视频",
    "主图视频",
    "新人价",
    "plus",
    "load",
    "retry",
)

ALLOW_ATTR_KEYWORDS = (
    "品牌",
    "货号",
    "型号",
    "材质",
    "材料",
    "成分",
    "尺寸",
    "规格",
    "尺码",
    "重量",
    "净重",
    "毛重",
    "颜色",
    "包装",
    "装箱",
    "适用",
    "产地",
)


def service_status() -> dict[str, str]:
    return {"service": "collect", "status": "ready"}


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def split_lines(value: Any) -> list[str]:
    lines = []
    for line in re.split(r"[\r\n]+", str(value or "")):
        text = normalize_space(line)
        if text:
            lines.append(text)
    return lines


def html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", str(html or ""))
    cleaned = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>|</tr>", "\n", cleaned)
    text = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()[:30000]


def is_verification_page(text_or_html: str) -> bool:
    lowered = str(text_or_html or "").lower()
    return any(marker.lower() in lowered for marker in VERIFY_MARKERS_1688)


def has_noise(value: Any) -> bool:
    text = normalize_space(value).casefold()
    return bool(text) and any(keyword.casefold() in text for keyword in NOISE_KEYWORDS)


def count_noise_segments(value: Any) -> int:
    return sum(1 for item in re.split(r"[\r\n]+|\s{2,}|[。；;!?]", str(value or "")) if has_noise(item))


def clean_price_number(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"(\d)\s+([.,])\s+(\d)", r"\1\2\3", text)
    text = re.sub(r"[^0-9.,]", "", text)
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts[-1]) == 3 else ".".join(parts)
    return text.strip(".")


def parse_weight_to_kg(value: Any) -> str:
    text = normalize_space(value)
    match = re.search(r"([0-9]+(?:[,.][0-9]+)?)\s*(公斤|千克|kg|KG|克|g|G|lb|lbs|磅)", text)
    if not match:
        return ""
    amount = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit in {"g", "克"}:
        amount /= 1000
    elif unit in {"lb", "lbs", "磅"}:
        amount *= 0.453592
    return f"{amount:.3f}".rstrip("0").rstrip(".")


def parse_price(text: str) -> dict[str, Any]:
    raw = normalize_space(html_to_text(text) if "<" in str(text) else text)
    include_terms = ("起批", "批发价", "单价", "价格", "活动价", "¥", "￥")
    exclude_terms = ("运费", "邮费", "配送", "快递", "物流", "包邮", "优惠券")
    candidates = []
    patterns = [
        r"(?:¥|￥)\s*([0-9]+(?:\s*[,.]\s*[0-9]+)?)\s*\d+\s*(?:个|件)?\s*起批",
        r"(?:批发价|单价|价格|起批|活动价)[^¥￥0-9]{0,24}(?:¥|￥)?\s*([0-9]+(?:\s*[,.]\s*[0-9]+)?)",
        r"(?:¥|￥)\s*([0-9]+(?:\s*[,.]\s*[0-9]+)?)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw, flags=re.I):
            value = clean_price_number(match.group(1))
            if not value:
                continue
            amount = float(value)
            if amount <= 0 or amount > 100000:
                continue
            start, end = match.span(1)
            context = raw[max(0, start - 40): min(len(raw), end + 40)]
            score = 10
            if any(term in context for term in include_terms):
                score += 50
            if any(term in context for term in exclude_terms):
                score -= 100
            candidates.append({"value": f"{amount:.2f}".rstrip("0").rstrip("."), "amount": amount, "context": context, "score": score})
    valid = sorted([item for item in candidates if item["score"] > 0], key=lambda item: (-item["score"], item["amount"]))
    return {"price": str(valid[0]["value"]) if valid else "", "selected": valid[0] if valid else {}, "candidates": candidates}


def extract_attribute_pairs(text_or_html: str) -> dict[str, str]:
    text = html_to_text(text_or_html) if "<" in str(text_or_html) else str(text_or_html or "")
    pairs: dict[str, str] = {}
    for line in split_lines(text):
        if ":" not in line and "：" not in line:
            continue
        key, value = re.split(r"[:：]", line, maxsplit=1)
        key = normalize_space(key).strip(":：")
        value = normalize_space(value)
        if not key or not value or len(key) > 40 or len(value) > 300:
            continue
        if has_noise(key) or has_noise(value):
            continue
        if not any(term in key for term in ALLOW_ATTR_KEYWORDS):
            continue
        pairs[key] = value
    return pairs


def first_attr(attrs: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        for key, value in attrs.items():
            if name in key:
                return value
    return ""


def unique_nonempty(items: list[Any]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        text = normalize_space(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def extract_labeled_value(text: str, labels: tuple[str, ...]) -> str:
    for line in split_lines(text):
        for label in labels:
            match = re.match(rf"^\s*{re.escape(label)}\s*[:：]\s*(.+)$", line, flags=re.I)
            if match:
                value = normalize_space(match.group(1))
                if value and not has_noise(value):
                    return value
    return ""


def extract_title(text: str) -> str:
    labeled = extract_labeled_value(text, ("标题", "商品标题", "品名", "产品名称", "名称", "title"))
    if labeled:
        return labeled
    for line in split_lines(text):
        if has_noise(line):
            continue
        if re.search(r"https?://|^\s*(价格|规格|SKU|材质|重量|图片|包装)\s*[:：]", line, flags=re.I):
            continue
        if 4 <= len(line) <= 120:
            return line
    return ""


def extract_image_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"'<>]*)?", text, flags=re.I)
    return unique_nonempty(urls)


def extract_specs_and_skus(text: str, attrs: dict[str, str]) -> tuple[list[str], list[str]]:
    specs: list[str] = []
    skus: list[str] = []
    for key, value in attrs.items():
        if any(name in key for name in ("规格", "尺寸", "颜色", "尺码", "型号")):
            specs.extend(re.split(r"[,，/、;\s]+", value))
        if "sku" in key.lower() or "SKU" in key:
            skus.extend(re.split(r"[,，/、;\s]+", value))
    skus.extend(re.split(r"[,，/、;\s]+", extract_labeled_value(text, ("SKU", "sku", "规格SKU"))))
    return unique_nonempty(specs), unique_nonempty(skus)


def clean_1688_text(text_or_html: str, source_url: str = "") -> dict[str, Any]:
    raw = str(text_or_html or "")
    text = html_to_text(raw) if "<" in raw else raw
    attrs = extract_attribute_pairs(raw)
    material = first_attr(attrs, ("主体材质", "塑料材质", "产品材质", "材质", "面料", "成分"))
    dimension = first_attr(attrs, ("尺寸", "规格", "尺码", "产品尺寸", "包装尺寸"))
    weight_raw = first_attr(attrs, ("商品重量", "重量", "净重", "毛重", "包装重量", "单件重量"))
    package_value = first_attr(attrs, ("包装清单", "包装内容", "装箱清单", "包装"))
    target_customer = first_attr(attrs, ("适用年龄", "适用人群"))
    color_value = first_attr(attrs, ("颜色", "色号"))
    price = parse_price(raw)
    title = extract_title(text)
    images = extract_image_urls(raw)
    specs, skus = extract_specs_and_skus(text, attrs)
    colors = unique_nonempty(re.split(r"[、,/，\s]+", color_value)) if color_value else []
    package_items = unique_nonempty(re.split(r"[、,/，+\s]+", package_value)) if package_value else []
    summary_lines = []
    for label, value in [
        ("材质", material),
        ("尺寸/规格", dimension),
        ("重量", weight_raw),
        ("颜色", " / ".join(colors)),
        ("包装清单", " / ".join(package_items)),
        ("适用人群", target_customer),
    ]:
        if value and not has_noise(value):
            summary_lines.append(f"{label}: {value}")
    for key, value in attrs.items():
        line = f"{key}: {value}"
        if line not in summary_lines:
            summary_lines.append(line)
    clean_source_text = "\n".join(summary_lines[:30])
    return {
        "ok": True,
        "source_platform": "1688",
        "source_url": source_url,
        "title": title,
        "images": images,
        "manual_required": is_verification_page(raw),
        "message": "检测到验证或登录提示，请人工处理后再粘贴文本。" if is_verification_page(raw) else "",
        "source_price_cny": price.get("price", ""),
        "source_price_cny_for_cost": price.get("price", ""),
        "source_price_debug": json.dumps(price, ensure_ascii=False),
        "source_weight_kg": parse_weight_to_kg(weight_raw),
        "source_weight_raw": weight_raw,
        "source_material": material,
        "materials": [material] if material else [],
        "dimensions": dimension,
        "specs": specs,
        "skus": skus,
        "colors": colors,
        "package_includes": package_items,
        "target_customer": target_customer,
        "attribute_dict": attrs,
        "source_attributes": attrs,
        "clean_source_text": clean_source_text,
        "source_text": clean_source_text,
        "supplemental_info": clean_source_text,
        "raw_page_text_length": len(text),
        "noise_segment_count": count_noise_segments(text),
        "clean_field_count": len(summary_lines) + len(attrs),
        "safety": {
            "captcha_bypass": False,
            "auto_slider": False,
            "proxy_pool": False,
            "stealth": False,
        },
    }


__all__ = [
    "clean_1688_text",
    "clean_price_number",
    "count_noise_segments",
    "extract_attribute_pairs",
    "extract_image_urls",
    "extract_specs_and_skus",
    "html_to_text",
    "is_verification_page",
    "parse_price",
    "parse_weight_to_kg",
    "service_status",
]
