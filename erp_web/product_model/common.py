from __future__ import annotations

import re
from typing import Any

from erp_web.marketplace_registry import PLATFORMS
IMAGE_ORIGINS = ("source", "amazon", "1688", "browser", "html_import", "manual", "local_upload", "ai_generated", "ai_translated", "chatgpt_import", "extension")
IMAGE_USAGES = ("main", "detail", "size", "scene", "package", "selling_point", "material", "unknown", "other")
SOURCE_COMPAT_IMAGE_ORIGINS = {"source", "amazon", "1688", "browser", "html_import", "manual", "extension"}


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def parse_dimensions_text(value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {"length_cm": "", "width_cm": "", "height_cm": ""}
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*[x×*]\s*([0-9]+(?:\.[0-9]+)?)\s*[x×*]\s*([0-9]+(?:\.[0-9]+)?)",
        text.replace("厘米", "cm"),
        flags=re.I,
    )
    if match:
        return {
            "length_cm": match.group(1),
            "width_cm": match.group(2),
            "height_cm": match.group(3),
        }
    return {"length_cm": "", "width_cm": "", "height_cm": ""}


def text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text

__all__ = [name for name in globals() if not name.startswith("__")]
