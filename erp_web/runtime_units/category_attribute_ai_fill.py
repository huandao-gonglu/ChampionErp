# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from erp_web.product_model import apply_ai_attribute_fill, normalize_product_model
from erp_web.schemas.category import (
    category_attribute_dictionary_id,
    is_category_dictionary_attribute,
)

from .ai_use_case import run_ai_use_case
from .category_store import fetch_category_attribute_values


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，;；\n]+", value) if item.strip()]
    return []


def _normalize_attr(attr: Any, required_fallback: bool = False) -> dict[str, Any]:
    raw = attr if isinstance(attr, dict) else {}
    dictionary_id = category_attribute_dictionary_id(raw)
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
        "dictionary_id": dictionary_id,
        "is_dictionary": is_category_dictionary_attribute(raw),
    }


def _attribute_schema(category_record: dict[str, Any] | None) -> list[dict[str, Any]]:
    record = category_record if isinstance(category_record, dict) else {}
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
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
    payload = {
        "platform": platform,
        "category_id": str((category_record or {}).get("category_id") or ""),
        "category_path": _category_path_text(category_record),
        "product_context": _product_context(product, platform),
        "attributes": schema,
    }
    return run_ai_use_case(
        "category.attribute_fill",
        payload,
        lambda value: value if isinstance(value, dict) else {},
        temperature=0,
    )


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
    if isinstance(value, dict) and isinstance(value.get("values"), list):
        selected = [item for item in value.get("values") or [] if isinstance(item, dict)]
        return bool(selected) and all(
            item.get("dictionary_value_id") not in (None, "")
            and str(item.get("value") or "").strip()
            for item in selected
        )
    text = str(value or "").strip()
    return bool(text) and text.upper() != attr_id.upper()


def _dictionary_candidate_text(raw_value: Any) -> str:
    if isinstance(raw_value, dict):
        raw_value = raw_value.get("value") or raw_value.get("label")
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    return str(raw_value or "").strip()[:255]


def _validated_ai_attributes(
    ai_result: dict[str, Any],
    schema: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str], set[str]]:
    schema_by_id = {str(attr.get("id") or ""): attr for attr in schema}
    raw_attrs = ai_result.get("attributes") if isinstance(ai_result.get("attributes"), dict) else {}
    accepted: dict[str, Any] = {}
    dictionary_candidates: dict[str, str] = {}
    for attr_id, raw_value in raw_attrs.items():
        attr_id = str(attr_id or "").strip()
        attr = schema_by_id.get(attr_id)
        if not attr:
            continue
        if attr.get("is_dictionary"):
            candidate = _dictionary_candidate_text(raw_value)
            if candidate:
                dictionary_candidates[attr_id] = candidate
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
    return accepted, dictionary_candidates, review


def _dictionary_selection(value: dict[str, Any]) -> dict[str, Any] | None:
    value_id = value.get("id") or value.get("dictionary_value_id")
    label = str(value.get("value") or value.get("name") or "").strip()
    if value_id in (None, "") or not label:
        return None
    try:
        normalized_id: int | str = int(value_id)
    except (TypeError, ValueError):
        normalized_id = str(value_id).strip()
    if normalized_id == "":
        return None
    return {
        "values": [
            {
                "dictionary_value_id": normalized_id,
                "value": label,
            }
        ]
    }


def _resolve_dictionary_candidates(
    platform: str,
    category_record: dict[str, Any] | None,
    candidates: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    record = category_record if isinstance(category_record, dict) else {}
    category_id = str(record.get("category_id") or "").strip()
    site = str(record.get("site") or "").strip()
    resolved: dict[str, dict[str, Any]] = {}
    unresolved: set[str] = set()
    failed: list[str] = []
    if not category_id:
        return resolved, set(candidates), failed

    for attr_id, candidate in candidates.items():
        try:
            result = fetch_category_attribute_values(
                platform,
                category_id,
                attr_id,
                site=site,
                query=candidate,
                limit=20,
                timeout_seconds=15,
            )
        except Exception:
            unresolved.add(attr_id)
            failed.append(attr_id)
            continue
        values = result.get("values") if isinstance(result, dict) else []
        exact = [
            item
            for item in (values if isinstance(values, list) else [])
            if isinstance(item, dict)
            and str(item.get("value") or item.get("name") or "").strip().casefold()
            == candidate.casefold()
        ]
        selection = _dictionary_selection(exact[0]) if len(exact) == 1 else None
        if selection is None:
            unresolved.add(attr_id)
            continue
        resolved[attr_id] = selection
    return resolved, unresolved, failed


def apply_ai_model_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    base_product = apply_ai_attribute_fill(product, platform, category_record)
    schema = _attribute_schema(category_record)
    if not schema:
        return base_product, {"source": "rules", "warning": "当前类目没有可填属性。"}
    meta: dict[str, Any] = {"source": "rules"}
    try:
        ai_result = _request_ai_fill(normalize_product_model(product or {}), platform, category_record, schema)
        ai_attrs, dictionary_candidates, ai_review = _validated_ai_attributes(ai_result, schema)
    except Exception as exc:
        meta["warning"] = f"AI 属性填充失败，已使用规则填充：{exc}"
        return base_product, meta

    dictionary_attrs, unresolved_dictionary, failed_dictionary = (
        _resolve_dictionary_candidates(platform, category_record, dictionary_candidates)
    )
    ai_attrs.update(dictionary_attrs)
    ai_review.update(unresolved_dictionary)

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
        definition = schema_by_id.get(attr_id) or {}
        if definition.get("required") and not attrs.get(attr_id):
            need_review.add(attr_id)
    for attr_id, attr in schema_by_id.items():
        if attr.get("required") and not _is_meaningful_existing(
            attr_id,
            attrs.get(attr_id),
        ):
            need_review.add(attr_id)

    draft["attributes"] = attrs
    draft["validation_errors"] = sorted(need_review)
    updated.setdefault("drafts", {})[platform] = draft
    meta["source"] = "ai_model"
    meta["ai_filled"] = sorted(ai_attrs)
    if failed_dictionary:
        meta["warning"] = (
            "部分平台字典值查询失败，已保留待人工复核："
            + "、".join(sorted(failed_dictionary))
        )
    return normalize_product_model(updated), meta
