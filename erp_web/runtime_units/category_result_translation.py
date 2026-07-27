# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from .ai_use_case import run_ai_use_case


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"\s*/\s*|\s*>\s*", value) if part.strip()]
    return []


def _joined(value: Any) -> str:
    return " / ".join(_text_list(value))


def _normalized_category(item: Any) -> dict[str, str]:
    raw = item if isinstance(item, dict) else {}
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else raw
    category_id = str(raw.get("id") or raw.get("category_id") or nested.get("id") or nested.get("category_id") or "").strip()
    name = str(raw.get("name") or raw.get("title") or nested.get("name") or nested.get("title") or nested.get("name_original") or category_id).strip()
    path = str(raw.get("path") or raw.get("category_path") or nested.get("path") or nested.get("category_path") or _joined(nested.get("path_original")) or name).strip()
    cn_path = str(raw.get("path_cn") or raw.get("name_cn") or _joined(nested.get("path_cn")) or nested.get("name_cn") or "").strip()
    return {"id": category_id, "name": name, "path": path, "cn_path": cn_path}


def _normalize_translation_map(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    if isinstance(raw.get("translations"), dict):
        raw = raw["translations"]
    result: dict[str, str] = {}
    for category_id, item in raw.items():
        if isinstance(item, dict):
            text = str(item.get("path") or item.get("label") or item.get("zh_path") or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if str(category_id).strip() and text:
            result[str(category_id).strip()] = text
    return result


def _request_ai_category_translations(platform: str, language: str, categories: list[dict[str, str]]) -> dict[str, str]:
    payload = {"platform": platform, "target_language": language, "categories": categories}
    return run_ai_use_case(
        "category.result_translation",
        payload,
        _normalize_translation_map,
        temperature=0.1,
    )


def translate_category_results(platform: str, categories: list[Any], language: str = "zh-CN") -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    language = str(language or "zh-CN").strip() or "zh-CN"
    normalized = [_normalized_category(item) for item in categories]
    normalized = [item for item in normalized if item.get("id")]
    if not normalized:
        return {"ok": True, "platform": platform, "language": language, "source": "empty", "translations": {}}
    translations: dict[str, str] = {}
    missing: list[dict[str, str]] = []
    for item in normalized:
        if item.get("cn_path"):
            translations[item["id"]] = item["cn_path"]
        else:
            missing.append(item)
    source = "provided"
    if missing:
        ai_translations = _request_ai_category_translations(platform, language, missing)
        for category_id, text in ai_translations.items():
            translations[category_id] = text
        source = "ai"
    return {"ok": True, "platform": platform, "language": language, "source": source, "translations": translations}
