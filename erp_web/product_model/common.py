from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_web.marketplace_registry import PLATFORMS
IMAGE_ORIGINS = ("source", "amazon", "1688", "browser", "html_import", "manual", "local_upload", "ai_generated", "ai_translated", "chatgpt_import", "extension")
IMAGE_USAGES = ("main", "detail", "size", "scene", "package", "selling_point", "material", "unknown", "other")
SOURCE_IMAGE_ORIGINS = {"source", "amazon", "1688", "browser", "html_import", "manual", "extension"}


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


_DIMENSION_NUMBER = r"[0-9]+(?:[.,][0-9]+)?"
_DIMENSION_UNIT = r"(?:毫米|厘米|英寸|mm|cm|inches?|inch|in|m|米)"
_DIMENSION_TRIPLET_RE = re.compile(
    rf"(?P<length>{_DIMENSION_NUMBER})\s*(?:{_DIMENSION_UNIT})?\s*[x×*]\s*"
    rf"(?P<width>{_DIMENSION_NUMBER})\s*(?:{_DIMENSION_UNIT})?\s*[x×*]\s*"
    rf"(?P<height>{_DIMENSION_NUMBER})\s*(?:{_DIMENSION_UNIT})?",
    flags=re.I,
)
_DIMENSION_UNIT_SCALES = {
    "mm": Decimal("0.1"),
    "cm": Decimal("1"),
    "m": Decimal("100"),
    "in": Decimal("2.54"),
}


def _dimension_unit(value: Any) -> str:
    text = str(value or "").strip().lower()
    if re.search(r"毫米|(?<![a-z])mm(?![a-z])", text):
        return "mm"
    if re.search(r"厘米|(?<![a-z])cm(?![a-z])", text):
        return "cm"
    if re.search(r"英寸|(?<![a-z])(?:inches|inch|in)(?![a-z])", text):
        return "in"
    without_metric_words = text.replace("毫米", "").replace("厘米", "")
    if "米" in without_metric_words or re.search(r"(?<![a-z])m(?![a-z])", without_metric_words):
        return "m"
    return ""


def _dimension_number_text(value: Decimal) -> str:
    if not value.is_finite():
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_dimension_measurement(
    value: Any,
    *,
    unit_hint: Any = "",
    default_unit: str = "cm",
) -> tuple[dict[str, str], str, bool]:
    """把三段尺寸统一转换成厘米，并返回识别到的来源单位。"""

    text = str(value or "").strip()
    empty = {"length_cm": "", "width_cm": "", "height_cm": ""}
    if not text:
        return empty, "", False
    match = _DIMENSION_TRIPLET_RE.search(text)
    if not match:
        return empty, "", False

    explicit_unit = _dimension_unit(text) or _dimension_unit(unit_hint)
    unit = explicit_unit or str(default_unit or "cm").strip().lower()
    if unit not in _DIMENSION_UNIT_SCALES:
        unit = "cm"
    try:
        values = [
            Decimal(match.group(name).replace(",", "."))
            for name in ("length", "width", "height")
        ]
    except (InvalidOperation, ValueError):
        return empty, unit, not bool(explicit_unit)
    if any(number <= 0 or not number.is_finite() for number in values):
        return empty, unit, not bool(explicit_unit)
    scale = _DIMENSION_UNIT_SCALES[unit]
    normalized = {
        field: _dimension_number_text(number * scale)
        for field, number in zip(
            ("length_cm", "width_cm", "height_cm"),
            values,
            strict=True,
        )
    }
    return normalized, unit, not bool(explicit_unit)


def parse_dimensions_text(value: Any, *, default_unit: str = "cm") -> dict[str, str]:
    dimensions, _, _ = parse_dimension_measurement(value, default_unit=default_unit)
    return dimensions


def text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text

__all__ = [name for name in globals() if not name.startswith("__")]
