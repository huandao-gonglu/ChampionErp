# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from services import config_service

from product_model import apply_ai_attribute_fill, normalize_product_model

from .copy_generation import openai_client_from_config
from .product_store import load_app_config
from .runtime_common import APP_DIR


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，;；\n]+", value) if item.strip()]
    return []


def _normalize_attr(attr: Any, required_fallback: bool = False) -> dict[str, Any]:
    raw = attr if isinstance(attr, dict) else {}
    values = raw.get("values") if isinstance(raw.get("values"), list) else []
    options = _normalize_list(raw.get("options"))
    for item in values:
        if isinstance(item, dict):
            label = str(item.get("name") or item.get("value_name") or item.get("id") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            options.append(label)
    return {
        "id": str(raw.get("id") or raw.get("attribute_id") or raw.get("code") or "").strip(),
        "name": str(raw.get("name") or raw.get("label") or raw.get("id") or "").strip(),
        "required": bool(raw.get("required", required_fallback)),
        "value_type": str(raw.get("value_type") or "").strip(),
        "options": list(dict.fromkeys(options))[:80],
    }


def _attribute_schema(category_record: dict[str, Any] | None) -> list[dict[str, Any]]:
    record = category_record if isinstance(category_record, dict) else {}
    attrs = record.get("attributes_cache") if isinstance(record.get("attributes_cache"), dict) else {}
    required = [_normalize_attr(attr, True) for attr in (attrs.get("required") if isinstance(attrs.get("required"), list) else [])]
    optional = [_normalize_attr(attr, False) for attr in (attrs.get("optional") if isinstance(attrs.get("optional"), list) else [])]
    return [attr for attr in required + optional if attr.get("id")]


def _category_path_text(record: dict[str, Any] | None) -> str:
    raw = record if isinstance(record, dict) else {}
    for key in ("category_path", "path", "name_original", "name_cn", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("path_original", "path_cn"):
        value = raw.get(key)
        if isinstance(value, list):
            text = " / ".join(str(item).strip() for item in value if str(item).strip())
            if text:
                return text
    return ""


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}


def _short_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _product_context(product: dict[str, Any], platform: str) -> dict[str, Any]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    draft = product.get("drafts", {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    return {
        "product": {
            "name": product.get("name"),
            "brand": product.get("brand"),
            "model": product.get("model"),
            "category": product.get("category"),
            "colors": product.get("colors"),
            "materials": product.get("materials"),
            "attributes": product.get("attributes"),
        },
        "source": {
            "platform": source.get("source_platform"),
            "url": source.get("source_url"),
            "title": _short_text(source.get("title"), 500),
            "description": _short_text(source.get("description"), 2500),
            "bullets": _normalize_list(source.get("bullets"))[:30],
            "attributes": source.get("attributes") if isinstance(source.get("attributes"), dict) else {},
            "dimensions": source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {},
            "weight_kg": source.get("weight_kg"),
            "material": source.get("material"),
            "colors": source.get("colors"),
            "package_contents": source.get("package_contents"),
        },
        "draft": {
            "title": _short_text(draft.get("title"), 500),
            "description": _short_text(draft.get("description"), 1800),
            "brand": draft.get("brand"),
            "model": draft.get("model"),
            "sku": draft.get("sku"),
            "upc": draft.get("upc"),
            "attributes": draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {},
            "package_dimensions": draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {},
        },
    }


def _request_ai_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None, schema: list[dict[str, Any]]) -> dict[str, Any]:
    app_cfg = load_app_config()
    text_ai = config_service.ai_config_from_sources(APP_DIR, app_cfg).get("text_ai", {})
    model = str(text_ai.get("model") or "deepseek-chat").strip()
    client = openai_client_from_config(app_cfg)
    payload = {
        "platform": platform,
        "category_id": str((category_record or {}).get("category_id") or ""),
        "category_path": _category_path_text(category_record),
        "product_context": _product_context(product, platform),
        "attributes": schema,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You fill marketplace category attributes for ecommerce listings. "
                "Use only evidence from product_context. Return only JSON. "
                "For attributes with options, choose exactly one option string from the provided options. "
                "If evidence is insufficient, put the attribute id in need_review and do not invent a value."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return JSON with this shape: "
                '{"attributes":{"ATTRIBUTE_ID":"exact value"},"need_review":[{"id":"ATTRIBUTE_ID","reason":"short reason"}]}.\n'
                f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(model=model, messages=messages, temperature=0)
    content = response.choices[0].message.content or ""
    return _json_from_text(content)


def _option_value(raw_value: Any, options: list[str]) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    for option in options:
        if text == option:
            return option
    lowered = text.lower()
    for option in options:
        if lowered == option.lower():
            return option
    return ""


def _is_meaningful_existing(attr_id: str, value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.upper() != attr_id.upper()


def _validated_ai_attributes(ai_result: dict[str, Any], schema: list[dict[str, Any]]) -> tuple[dict[str, str], set[str]]:
    schema_by_id = {str(attr.get("id") or ""): attr for attr in schema}
    raw_attrs = ai_result.get("attributes") if isinstance(ai_result.get("attributes"), dict) else {}
    accepted: dict[str, str] = {}
    for attr_id, raw_value in raw_attrs.items():
        attr_id = str(attr_id or "").strip()
        attr = schema_by_id.get(attr_id)
        if not attr:
            continue
        options = attr.get("options") if isinstance(attr.get("options"), list) else []
        if options:
            value = _option_value(raw_value, options)
            if value:
                accepted[attr_id] = value
            continue
        value = str(raw_value or "").strip()
        if value and value.upper() != attr_id.upper():
            accepted[attr_id] = value[:255]
    review: set[str] = set()
    raw_review = ai_result.get("need_review") if isinstance(ai_result.get("need_review"), list) else []
    for item in raw_review:
        if isinstance(item, dict):
            attr_id = str(item.get("id") or item.get("attribute_id") or "").strip()
        else:
            attr_id = str(item or "").strip()
        if attr_id in schema_by_id:
            review.add(attr_id)
    return accepted, review


def apply_text_ai_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    base_product = apply_ai_attribute_fill(product, platform, category_record)
    schema = _attribute_schema(category_record)
    if not schema:
        return base_product, {"source": "rules", "warning": "当前类目没有可填属性。"}
    meta: dict[str, Any] = {"source": "rules"}
    try:
        ai_result = _request_ai_fill(normalize_product_model(product or {}), platform, category_record, schema)
        ai_attrs, ai_review = _validated_ai_attributes(ai_result, schema)
    except Exception as exc:
        meta["warning"] = f"文本 AI 填充失败，已使用规则填充：{exc}"
        return base_product, meta

    updated = normalize_product_model(deepcopy(base_product))
    draft = deepcopy(updated.get("drafts", {}).get(platform, {}))
    attrs = deepcopy(draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {})
    original_draft = (product.get("drafts", {}) if isinstance(product.get("drafts"), dict) else {}).get(platform, {})
    original_attrs = original_draft.get("attributes") if isinstance(original_draft, dict) and isinstance(original_draft.get("attributes"), dict) else {}
    need_review = {str(item).strip() for item in (draft.get("validation_errors") if isinstance(draft.get("validation_errors"), list) else []) if str(item).strip()}

    for attr_id, value in ai_attrs.items():
        if _is_meaningful_existing(attr_id, original_attrs.get(attr_id)) and attr_id not in need_review:
            continue
        attrs[attr_id] = value
        need_review.discard(attr_id)

    schema_by_id = {str(attr.get("id") or ""): attr for attr in schema}
    for attr_id in ai_review:
        if not attrs.get(attr_id):
            need_review.add(attr_id)
    for attr_id, attr in schema_by_id.items():
        if attr.get("required") and not str(attrs.get(attr_id) or "").strip():
            need_review.add(attr_id)

    draft["attributes"] = attrs
    draft["validation_errors"] = sorted(need_review)
    updated.setdefault("drafts", {})[platform] = draft
    meta["source"] = "text_ai"
    meta["ai_filled"] = sorted(ai_attrs)
    return normalize_product_model(updated), meta
