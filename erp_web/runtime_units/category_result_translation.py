# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any

from services import ai_gateway, ai_prompt_templates

from .product_store import load_app_config
from .runtime_common import APP_DIR, CACHE_DIR


CATEGORY_RESULT_TRANSLATION_CACHE_PATH = CACHE_DIR / "category_result_translations.json"


def _read_cache() -> dict[str, Any]:
    try:
        if CATEGORY_RESULT_TRANSLATION_CACHE_PATH.exists():
            data = json.loads(CATEGORY_RESULT_TRANSLATION_CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _write_cache(cache: dict[str, Any]) -> None:
    CATEGORY_RESULT_TRANSLATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATEGORY_RESULT_TRANSLATION_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


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
    app_cfg = load_app_config()
    payload = {"platform": platform, "target_language": language, "categories": categories}
    prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(APP_DIR, app_cfg, "category.result_translation")
    messages = [
        {
            "role": "system",
            "content": prompt_pair["system"],
        },
        {
            "role": "user",
            "content": ai_prompt_templates.render_prompt_template(
                prompt_pair["user"],
                {"input_json": json.dumps(payload, ensure_ascii=False)},
            ),
        },
    ]
    return _normalize_translation_map(ai_gateway.chat_json(APP_DIR, app_cfg, "category.result_translation", messages, temperature=0.1))


def translate_category_results(platform: str, categories: list[Any], language: str = "zh-CN") -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    language = str(language or "zh-CN").strip() or "zh-CN"
    normalized = [_normalized_category(item) for item in categories]
    normalized = [item for item in normalized if item.get("id")]
    if not normalized:
        return {"ok": True, "platform": platform, "language": language, "source": "empty", "translations": {}}
    cache = _read_cache()
    translations: dict[str, str] = {}
    missing: list[dict[str, str]] = []
    for item in normalized:
        cache_key = f"{platform}:{item['id']}:{language}"
        cached = str(cache.get(cache_key) or "").strip()
        if item.get("cn_path"):
            translations[item["id"]] = item["cn_path"]
            cache[cache_key] = item["cn_path"]
        elif cached:
            translations[item["id"]] = cached
        else:
            missing.append(item)
    source = "cache"
    if missing:
        ai_translations = _request_ai_category_translations(platform, language, missing)
        for category_id, text in ai_translations.items():
            translations[category_id] = text
            cache[f"{platform}:{category_id}:{language}"] = text
        source = "ai"
    _write_cache(cache)
    return {"ok": True, "platform": platform, "language": language, "source": source, "translations": translations}
