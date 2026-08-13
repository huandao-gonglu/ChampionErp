# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from erp_web.product_model import (
    apply_ai_attribute_fill,
    normalize_product_model,
    unresolved_required_category_attributes,
)
from erp_web.schemas.category import category_attribute_schema
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeFillAgentRun,
    run_category_attribute_fill_agent,
)

from .category_attribute_tools import build_category_attribute_value_toolset


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            item.strip()
            for item in re.split(r"[,，;；\n]+", value)
            if item.strip()
        ]
    return []


def _category_path_text(record: dict[str, Any] | None) -> str:
    raw = record if isinstance(record, dict) else {}
    for key in ("category_path", "path", "name_original", "name_cn", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("path_original", "path_cn"):
        value = raw.get(key)
        if isinstance(value, list):
            text = " / ".join(
                str(item).strip() for item in value if str(item).strip()
            )
            if text:
                return text
    return ""


def _short_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _product_context(product: dict[str, Any], platform: str) -> dict[str, Any]:
    source = (
        product.get("source") if isinstance(product.get("source"), dict) else {}
    )
    draft = (
        product.get("drafts", {}).get(platform)
        if isinstance(product.get("drafts"), dict)
        else {}
    )
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
            "attributes": (
                source.get("attributes")
                if isinstance(source.get("attributes"), dict)
                else {}
            ),
            "dimensions": (
                source.get("dimensions")
                if isinstance(source.get("dimensions"), dict)
                else {}
            ),
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
            "attributes": (
                draft.get("attributes")
                if isinstance(draft.get("attributes"), dict)
                else {}
            ),
            "package_dimensions": (
                draft.get("package_dimensions")
                if isinstance(draft.get("package_dimensions"), dict)
                else {}
            ),
        },
    }


def _agent_payload(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
    schema: list[dict[str, Any]],
) -> dict[str, Any]:
    record = category_record if isinstance(category_record, dict) else {}
    return {
        "platform": platform,
        "site": str(record.get("site") or ""),
        "category_id": str(record.get("category_id") or ""),
        "category_path": _category_path_text(category_record),
        "product_context": _product_context(product, platform),
        "attributes": schema,
    }


def _dictionary_value_id(value: Any) -> int | str:
    text = str(value or "").strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _validated_agent_attributes(
    agent_output: dict[str, Any],
    schema: list[dict[str, Any]],
    ledger: CategoryAttributeValueLedger,
) -> dict[str, Any]:
    schema_by_id = {str(attr.get("id") or ""): attr for attr in schema}
    accepted: dict[str, Any] = {}
    dictionary_values: dict[str, list[dict[str, Any]]] = {}
    assignments = (
        agent_output.get("assignments")
        if isinstance(agent_output.get("assignments"), list)
        else []
    )
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        attr_id = str(assignment.get("attribute_id") or "").strip()
        attr = schema_by_id.get(attr_id)
        if not attr:
            continue
        value = str(assignment.get("value") or "").strip()
        if not value:
            continue
        if attr.get("value_mode") == "strict_enum":
            value_id = str(assignment.get("dictionary_value_id") or "").strip()
            candidate = ledger.get(attr_id, value_id)
            if candidate is None:
                continue
            dictionary_values.setdefault(attr_id, []).append(
                {
                    "dictionary_value_id": _dictionary_value_id(
                        candidate["dictionary_value_id"]
                    ),
                    "value": candidate["value"],
                }
            )
            continue
        if value.upper() != attr_id.upper():
            options = (
                attr.get("options")
                if isinstance(attr.get("options"), list)
                else []
            )
            canonical_option = next(
                (
                    str(option)
                    for option in options
                    if str(option).strip().casefold() == value.casefold()
                ),
                "",
            )
            if canonical_option:
                value = canonical_option
            accepted[attr_id] = value[:255]
    for attr_id, values in dictionary_values.items():
        accepted[attr_id] = {"values": values}
    return accepted


def apply_ai_model_attribute_fill(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
    *,
    parent_conversation_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    platform = str(platform or "").strip().lower()
    base_product = apply_ai_attribute_fill(product, platform, category_record)
    schema = category_attribute_schema(category_record)
    if not schema:
        return base_product, {"source": "rules", "warning": "当前类目没有可填属性。"}
    meta: dict[str, Any] = {"source": "rules"}
    agent_run: CategoryAttributeFillAgentRun | None = None
    try:
        agent_schema = unresolved_required_category_attributes(
            base_product,
            platform,
            category_record,
        )
        if not agent_schema:
            return base_product, {"source": "rules", "ai_filled": []}
        ledger = CategoryAttributeValueLedger.from_schema(agent_schema)
        toolset = build_category_attribute_value_toolset(
            platform=platform,
            category_record=category_record,
            ledger=ledger,
        )
        agent_run = run_category_attribute_fill_agent(
            _agent_payload(
                base_product,
                platform,
                category_record,
                agent_schema,
            ),
            toolset,
            ledger,
            **(
                {"parent_conversation_id": parent_conversation_id}
                if parent_conversation_id
                else {}
            ),
        )
        ai_attrs = _validated_agent_attributes(
            agent_run.output,
            agent_schema,
            ledger,
        )
    except Exception as exc:
        meta["warning"] = f"AI 属性填充失败，已使用规则填充：{exc}"
        return base_product, meta

    updated = normalize_product_model(deepcopy(base_product))
    draft = deepcopy(updated.get("drafts", {}).get(platform, {}))
    attrs = deepcopy(
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    for attr_id, value in ai_attrs.items():
        attrs[attr_id] = value

    draft["attributes"] = attrs
    updated.setdefault("drafts", {})[platform] = draft
    need_review = sorted(
        str(definition.get("id") or "").strip()
        for definition in unresolved_required_category_attributes(
            updated,
            platform,
            category_record,
        )
    )
    draft["validation_errors"] = need_review
    updated["drafts"][platform] = draft
    meta["source"] = "ai_model"
    meta["ai_filled"] = sorted(ai_attrs)
    if ledger.failed_attribute_ids:
        meta["warning"] = (
            "部分平台字典值查询失败，已保留待人工复核："
            + "、".join(sorted(ledger.failed_attribute_ids))
        )
    if agent_run is not None:
        meta["conversation_id"] = (
            agent_run.outcome.conversation_id if agent_run.outcome is not None else ""
        )
        agent_run.finish_business_result(
            {
                "status": "completed",
                "filled_attribute_ids": sorted(ai_attrs),
                "need_review_attribute_ids": need_review,
                "enum_lookup_count": len(ledger.attempts),
            }
        )
    return normalize_product_model(updated), meta
