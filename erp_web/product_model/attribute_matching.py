from __future__ import annotations

import re
from typing import Any

from .common import parse_dimension_measurement

_IGNORED_DIMENSION_KEYS = ("容量", "电源线", "线长", "功率", "重量", "厚度")
_GENERIC_CATEGORY_VALUES = {"", "其他", "其它", "通用", "未知", "无"}


def _compact_attribute_key(value: Any) -> str:
    """移除属性名中的空格和装饰符，保留中文及字母数字用于语义匹配。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _dimension_scope(key: str) -> str:
    return "package" if any(token in key for token in ("包装", "包裹", "外箱", "物流")) else "product"


def _dimension_key_score(key: str) -> int:
    if not key or any(token in key for token in _IGNORED_DIMENSION_KEYS):
        return 0
    if "体积" in key:
        return 90 if _dimension_scope(key) == "package" else 0
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


def _dimension_candidates(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source_key, raw_value in attributes.items():
        key = _compact_attribute_key(source_key)
        value = str(raw_value or "").strip()
        score = _dimension_key_score(key)
        normalized, unit, unit_inferred = parse_dimension_measurement(
            value,
            unit_hint=key,
            default_unit="mm",
        )
        if not score or not all(normalized.values()):
            continue
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
    return candidates


def _best_dimension_candidate(
    candidates: list[dict[str, Any]],
    scope: str,
) -> dict[str, Any]:
    return max(
        (item for item in candidates if item.get("scope") == scope),
        key=lambda item: (int(item["score"]), -len(item["source_key"])),
        default={},
    )


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
    dimension_candidates = _dimension_candidates(clean_attributes)
    product_dimensions = _best_dimension_candidate(dimension_candidates, "product")
    package_dimensions = _best_dimension_candidate(dimension_candidates, "package")
    if category:
        matches["category"] = category
    if product_dimensions or package_dimensions:
        matches["dimensions"] = product_dimensions or package_dimensions
    if package_dimensions:
        matches["package_dimensions"] = package_dimensions
    return matches


def source_package_dimensions(source: dict[str, Any] | None) -> dict[str, str]:
    """返回可安全用于平台包裹字段的来源尺寸，避免把商品本体尺寸当成包裹尺寸。"""

    source = source if isinstance(source, dict) else {}
    matches = source.get("attribute_matches") if isinstance(source.get("attribute_matches"), dict) else {}
    package_match = matches.get("package_dimensions") if isinstance(matches.get("package_dimensions"), dict) else {}
    dimensions_match = matches.get("dimensions") if isinstance(matches.get("dimensions"), dict) else {}
    matched = package_match or (dimensions_match if dimensions_match.get("scope") == "package" else {})
    normalized = matched.get("normalized") if isinstance(matched.get("normalized"), dict) else {}
    if normalized:
        return {
            "length_cm": str(normalized.get("length_cm") or "").strip(),
            "width_cm": str(normalized.get("width_cm") or "").strip(),
            "height_cm": str(normalized.get("height_cm") or "").strip(),
        }
    if dimensions_match.get("scope") == "product":
        return {"length_cm": "", "width_cm": "", "height_cm": ""}
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    return {
        "length_cm": str(dimensions.get("length_cm") or dimensions.get("lengthCm") or "").strip(),
        "width_cm": str(dimensions.get("width_cm") or dimensions.get("widthCm") or "").strip(),
        "height_cm": str(dimensions.get("height_cm") or dimensions.get("heightCm") or "").strip(),
    }


__all__ = ["infer_source_attribute_matches", "source_package_dimensions"]
