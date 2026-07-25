from __future__ import annotations

import re
from typing import Any


_DIMENSION_TRIPLET_RE = re.compile(
    r"(?P<length>\d+(?:[.,]\d+)?)\s*[x×*]\s*"
    r"(?P<width>\d+(?:[.,]\d+)?)\s*[x×*]\s*"
    r"(?P<height>\d+(?:[.,]\d+)?)"
)
_IGNORED_DIMENSION_KEYS = ("体积", "容量", "电源线", "线长", "功率", "重量", "厚度")
_GENERIC_CATEGORY_VALUES = {"", "其他", "其它", "通用", "未知", "无"}


def _compact_attribute_key(value: Any) -> str:
    """移除属性名中的空格和装饰符，保留中文及字母数字用于语义匹配。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _dimension_scope(key: str) -> str:
    return "package" if any(token in key for token in ("包装", "包裹", "外箱", "物流")) else "product"


def _dimension_key_score(key: str) -> int:
    if not key or any(token in key for token in _IGNORED_DIMENSION_KEYS):
        return 0
    if "长宽高" in key:
        return 100
    if "尺寸" in key:
        score = 70
        if any(token in key for token in ("包装", "包裹", "外箱", "物流")):
            score += 25
        elif any(token in key for token in ("外观", "机身", "本体", "产品", "商品")):
            score += 18
        return score
    if "规格" in key:
        return 40
    return 0


def _dimension_unit_scale(key: str, value: str) -> tuple[float, str, bool]:
    hint = f"{key} {value}".lower()
    if "毫米" in hint or "mm" in hint:
        return (0.1, "mm", False)
    if "厘米" in hint or "cm" in hint:
        return (1.0, "cm", False)
    if ("米" in hint and "毫米" not in hint and "厘米" not in hint) or re.search(r"(?<![a-z])m(?![a-z])", hint):
        return (100.0, "m", False)
    # 1688 未标单位的三段尺寸按业务约定视为毫米，再统一写入厘米字段。
    return (0.1, "mm", True)


def _match_dimensions(attributes: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for source_key, raw_value in attributes.items():
        key = _compact_attribute_key(source_key)
        value = str(raw_value or "").strip()
        score = _dimension_key_score(key)
        match = _DIMENSION_TRIPLET_RE.search(value)
        if not score or not match:
            continue
        values = [float(match.group(name).replace(",", ".")) for name in ("length", "width", "height")]
        if any(number <= 0 for number in values):
            continue
        scale, unit, unit_inferred = _dimension_unit_scale(key, value)
        normalized = {
            "length_cm": _format_number(values[0] * scale),
            "width_cm": _format_number(values[1] * scale),
            "height_cm": _format_number(values[2] * scale),
        }
        candidates.append(
            {
                "source_key": str(source_key).strip(),
                "raw_value": value,
                "score": score,
                "scope": _dimension_scope(key),
                "unit": unit,
                "unit_inferred": unit_inferred,
                "requires_confirmation": False,
                "normalized": normalized,
            }
        )
    return max(candidates, key=lambda item: (int(item["score"]), -len(item["source_key"])), default={})


def _category_key_score(key: str) -> int:
    if not key or any(token in key for token in ("类目属性", "分类编码", "分类号")):
        return 0
    if "类目" in key:
        return 100
    if "品类" in key:
        return 95
    if "分类" in key:
        return 85
    if "产品类型" in key or "商品类型" in key:
        return 75
    return 0


def _match_category(attributes: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for source_key, raw_value in attributes.items():
        key = _compact_attribute_key(source_key)
        value = str(raw_value or "").strip()
        score = _category_key_score(key)
        if not score or not value or value in _GENERIC_CATEGORY_VALUES or len(value) > 120:
            continue
        candidates.append({"source_key": str(source_key).strip(), "value": value, "score": score})
    return max(candidates, key=lambda item: (int(item["score"]), -len(item["source_key"])), default={})


def infer_source_attribute_matches(attributes: dict[str, Any] | None) -> dict[str, Any]:
    """从供应商属性中按字段语义提取候选值，不依赖属性名的完全一致。"""
    clean_attributes = attributes if isinstance(attributes, dict) else {}
    matches: dict[str, Any] = {}
    category = _match_category(clean_attributes)
    dimensions = _match_dimensions(clean_attributes)
    if category:
        matches["category"] = category
    if dimensions:
        matches["dimensions"] = dimensions
    return matches


__all__ = ["infer_source_attribute_matches"]
