# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any

from erp_web.services import ai_gateway, ai_prompt_templates

from .product_store import load_app_config
from .runtime_common import APP_DIR, CACHE_DIR


ATTRIBUTE_TRANSLATION_CACHE_PATH = CACHE_DIR / "category_attribute_translations.json"


def _read_translation_cache() -> dict[str, Any]:
    try:
        if ATTRIBUTE_TRANSLATION_CACHE_PATH.exists():
            data = json.loads(ATTRIBUTE_TRANSLATION_CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _write_translation_cache(cache: dict[str, Any]) -> None:
    ATTRIBUTE_TRANSLATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ATTRIBUTE_TRANSLATION_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_key(platform: str, category_id: str, language: str) -> str:
    return f"{platform.strip().lower() or 'mercadolibre'}:{category_id.strip()}:{language.strip().lower() or 'zh-CN'}"


def _normalized_attribute(attr: Any) -> dict[str, Any]:
    record = attr if isinstance(attr, dict) else {}
    values = record.get("values") if isinstance(record.get("values"), list) else []
    options = record.get("options") if isinstance(record.get("options"), list) else []
    value_labels: list[str] = []
    for item in values[:30]:
        if isinstance(item, dict):
            label = str(item.get("name") or item.get("value_name") or item.get("id") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            value_labels.append(label)
    for item in options[:30]:
        label = str(item or "").strip()
        if label:
            value_labels.append(label)
    return {
        "id": str(record.get("id") or record.get("attribute_id") or "").strip(),
        "name": str(record.get("name") or record.get("label") or record.get("id") or "").strip(),
        "value_type": str(record.get("value_type") or "").strip(),
        "required": bool(record.get("required")),
        "values": list(dict.fromkeys(value_labels))[:30],
    }


def _normalize_translation_map(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    if isinstance(raw.get("translations"), dict):
        raw = raw["translations"]
    normalized: dict[str, Any] = {}
    for attr_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("zh_label") or item.get("name") or "").strip()
        help_text = str(item.get("help") or item.get("zh_help") or item.get("description") or "").strip()
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        normalized[str(attr_id).strip()] = {
            "label": label,
            "help": help_text,
            "values": {str(key): str(text) for key, text in values.items() if str(key).strip() and str(text).strip()},
        }
    return normalized


def _request_ai_attribute_translations(
    platform: str,
    category_id: str,
    category_path: str,
    language: str,
    attributes: list[dict[str, Any]],
) -> dict[str, Any]:
    app_cfg = load_app_config()
    prompt = {
        "platform": platform,
        "category_id": category_id,
        "category_path": category_path,
        "target_language": language or "zh-CN",
        "attributes": attributes,
    }
    prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(APP_DIR, app_cfg, "category.attribute_translation")
    messages = [
        {
            "role": "system",
            "content": prompt_pair["system"],
        },
        {
            "role": "user",
            "content": ai_prompt_templates.render_prompt_template(
                prompt_pair["user"],
                {"input_json": json.dumps(prompt, ensure_ascii=False)},
            ),
        },
    ]
    return _normalize_translation_map(ai_gateway.chat_json(APP_DIR, app_cfg, "category.attribute_translation", messages, temperature=0.1))


def translate_category_attributes(
    platform: str,
    category_id: str,
    category_path: str,
    attributes: list[Any],
    language: str = "zh-CN",
) -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    category_id = str(category_id or "").strip()
    language = str(language or "zh-CN").strip() or "zh-CN"
    normalized_attrs = [_normalized_attribute(attr) for attr in attributes]
    normalized_attrs = [attr for attr in normalized_attrs if attr.get("id")]
    if not normalized_attrs:
        return {
            "ok": True,
            "platform": platform,
            "category_id": category_id,
            "language": language,
            "source": "empty",
            "translations": {},
        }
    key = _cache_key(platform, category_id, language)
    cache = _read_translation_cache()
    cached = cache.get(key) if isinstance(cache.get(key), dict) else {}
    cached_translations = _normalize_translation_map(cached.get("translations") if isinstance(cached, dict) else {})
    attr_ids = {str(attr.get("id") or "") for attr in normalized_attrs}
    if attr_ids and attr_ids.issubset(set(cached_translations)):
        return {
            "ok": True,
            "platform": platform,
            "category_id": category_id,
            "language": language,
            "source": "cache",
            "translations": {attr_id: cached_translations[attr_id] for attr_id in attr_ids},
        }
    translations = _request_ai_attribute_translations(platform, category_id, category_path, language, normalized_attrs)
    merged = {**cached_translations, **translations}
    cache[key] = {
        "platform": platform,
        "category_id": category_id,
        "category_path": category_path,
        "language": language,
        "translations": merged,
    }
    _write_translation_cache(cache)
    return {
        "ok": True,
        "platform": platform,
        "category_id": category_id,
        "language": language,
        "source": "ai",
        "translations": {attr_id: merged.get(attr_id, {}) for attr_id in attr_ids},
    }
