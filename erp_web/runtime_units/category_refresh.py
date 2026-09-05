# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Callable

from erp_web import http_client
from erp_web.schemas.category_definition import ATTRIBUTE_OPTIONS_PREVIEW_LIMIT

JsonClient = Callable[..., dict[str, Any] | list[Any]]


def http_json(
    url: str,
    access_token: str | None = None,
    *,
    timeout_seconds: float = 8,
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 ERPCategoryLive/1.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return http_client.request_json(
        url,
        method=str(method or "GET").strip().upper(),
        headers=headers,
        data=data,
        timeout=timeout_seconds,
    )


def ml_attr_required(attr: dict[str, Any]) -> bool:
    tags = attr.get("tags") if isinstance(attr.get("tags"), dict) else {}
    return bool(attr.get("required") or tags.get("required") or tags.get("catalog_required"))


def normalize_ml_attribute(
    attr: dict[str, Any],
    *,
    allow_custom_values: bool | None = None,
    supplemental_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value_type = str(attr.get("value_type") or "string").strip() or "string"
    values = list(attr.get("values")) if isinstance(attr.get("values"), list) else []
    if supplemental_values:
        values.extend(item for item in supplemental_values if isinstance(item, dict))
    units = attr.get("allowed_units") if isinstance(attr.get("allowed_units"), list) else []
    normalized_units = [
        {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or item.get("id") or "").strip(),
        }
        for item in units
        if isinstance(item, dict)
        and str(item.get("name") or item.get("id") or "").strip()
    ]
    normalized_values: list[dict[str, str]] = []
    seen_values: set[tuple[str, str]] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        value_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or item.get("id") or "").strip()
        identity = (value_id, name.casefold())
        if not name or identity in seen_values:
            continue
        seen_values.add(identity)
        normalized_values.append({"id": value_id, "name": name})
    tags = attr.get("tags") if isinstance(attr.get("tags"), dict) else {}
    default_unit = str(attr.get("default_unit") or "").strip()
    constraints: dict[str, str] = {}
    try:
        max_length = int(attr.get("value_max_length") or 0)
    except (TypeError, ValueError):
        max_length = 0
    if max_length > 0:
        constraints["max_length"] = str(max_length)
    custom_values_allowed = (
        bool(allow_custom_values)
        if allow_custom_values is not None
        else value_type not in {"list", "boolean"}
    )
    has_restricted_candidates = bool(normalized_values) and not custom_values_allowed
    return {
        "variation_role": {"CHILD_PK": "variant", "PARENT_PK": "parent"}.get(str(attr.get("hierarchy") or ""), ""),
        "id": str(attr.get("id") or "").strip(),
        "name": str(attr.get("name") or attr.get("id") or "").strip(),
        "required": ml_attr_required(attr),
        "value_type": value_type,
        "value_mode": (
            "strict_enum"
            if value_type in {"list", "boolean"} or has_restricted_candidates
            else "open_enum"
            if normalized_values
            else "free_text"
        ),
        "allow_custom_values": custom_values_allowed,
        "is_dictionary": value_type in {"list", "boolean"} or has_restricted_candidates,
        "read_only": bool(tags.get("read_only") or tags.get("inferred")),
        "constraints": constraints,
        "unit": default_unit,
        "default_unit": default_unit,
        "default_unit_id": next(
            (
                item["id"]
                for item in normalized_units
                if item["name"] == default_unit
            ),
            "",
        ),
        "unit_options": normalized_units,
        "unit_ids": {
            item["name"]: item["id"]
            for item in normalized_units
            if item["id"]
        },
        # 保留有 ID 的规范化候选，CategoryDefinition 才能在发布边界把
        # 草稿文案编译成 Mercado 的 value_id/value_name。
        "values": normalized_values,
        "options": normalized_values,
        # Mercado 的 category attributes 响应给出该属性的完整 values 列表；
        # 只有超过本地预览上限时才存在未进入 definition 的候选。
        "has_more_values": len(normalized_values) > ATTRIBUTE_OPTIONS_PREVIEW_LIMIT,
        "is_collection": bool(tags.get("multivalued")),
        "description": str(attr.get("tooltip") or attr.get("hint") or "").strip(),
        "raw": attr,
    }


def mercadolibre_category_attributes(
    category_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
    *,
    technical_specs: dict[str, Any] | None = None,
    supplemental_values_by_attribute: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    raw = http_client(
        f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category_id)}/attributes",
        access_token,
    )
    custom_value_rules = mercadolibre_technical_spec_custom_value_rules(
        technical_specs or {}
    )
    supplemental = (
        supplemental_values_by_attribute
        if isinstance(supplemental_values_by_attribute, dict)
        else {}
    )
    attrs = [
        normalize_ml_attribute(
            item,
            allow_custom_values=custom_value_rules.get(
                str(item.get("id") or "").strip()
            ),
            supplemental_values=supplemental.get(
                str(item.get("id") or "").strip()
            ),
        )
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict)
    ]
    return {
        "required": [item for item in attrs if item.get("required")],
        "optional": [item for item in attrs if not item.get("required")],
    }


def mercadolibre_category_technical_specs(
    category_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
) -> dict[str, Any]:
    data = http_client(
        "https://api.mercadolibre.com/categories/"
        f"{urllib.parse.quote(category_id)}/technical_specs/input",
        access_token,
    )
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Mercado Libre 类目 technical specs 响应不是对象：{category_id}"
        )
    return data


def mercadolibre_technical_spec_custom_value_rules(
    technical_specs: dict[str, Any],
) -> dict[str, bool]:
    """提取组件级 ``allow_custom_value``，并投影到组件包含的属性。"""

    rules: dict[str, bool] = {}

    def visit_component(component: Any) -> None:
        if not isinstance(component, dict):
            return
        ui_config = (
            component.get("ui_config")
            if isinstance(component.get("ui_config"), dict)
            else {}
        )
        declared = ui_config.get("allow_custom_value")
        if isinstance(declared, bool):
            for attribute in component.get("attributes") or []:
                if not isinstance(attribute, dict):
                    continue
                attribute_id = str(attribute.get("id") or "").strip()
                if attribute_id:
                    rules[attribute_id] = declared
        for child in component.get("components") or []:
            visit_component(child)

    root = (
        technical_specs.get("input")
        if isinstance(technical_specs.get("input"), dict)
        else technical_specs
    )
    for group in root.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for component in group.get("components") or []:
            visit_component(component)
    return rules


def mercadolibre_catalog_attribute_top_values(
    catalog_domain: str,
    attribute_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
    *,
    limit: int = 51,
) -> list[dict[str, Any]]:
    """读取 catalog domain 的高频规范值；该查询端点使用 POST。"""

    safe_limit = max(1, min(1000, int(limit or 51)))
    data = http_client(
        "https://api.mercadolibre.com/catalog_domains/"
        f"{urllib.parse.quote(catalog_domain)}/attributes/"
        f"{urllib.parse.quote(attribute_id)}/top_values?limit={safe_limit}",
        access_token,
        method="POST",
    )
    if not isinstance(data, list):
        raise RuntimeError(
            "Mercado Libre catalog attribute top_values 响应不是数组："
            f"{catalog_domain}/{attribute_id}"
        )
    return [item for item in data if isinstance(item, dict)]


def mercadolibre_category_detail(
    category_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
) -> dict[str, Any]:
    data = http_client(
        f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category_id)}",
        access_token,
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Mercado Libre 类目详情响应不是对象：{category_id}")
    return data


def mercadolibre_category_record(
    detail: dict[str, Any],
    site: str,
    attrs: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    path_items = detail.get("path_from_root") if isinstance(detail.get("path_from_root"), list) else []
    path = [str(item.get("name") or "").strip() for item in path_items if isinstance(item, dict) and str(item.get("name") or "").strip()]
    path_ids = [str(item.get("id") or "").strip() for item in path_items if isinstance(item, dict) and str(item.get("id") or "").strip()]
    category_id = str(detail.get("id") or "").strip()
    name = str(detail.get("name") or category_id).strip()
    attrs = attrs if isinstance(attrs, dict) else {"required": [], "optional": []}
    return {
        "platform": "mercadolibre",
        "site": str(site or "").strip().upper(),
        "category_id": category_id,
        "subject_id": category_id,
        "type_id": "",
        "name_original": name,
        "name_cn": "",
        "category_path": " / ".join(path or [name]),
        "path_original": path or [name],
        "path_ids": path_ids or [category_id],
        "path_cn": [],
        "parent_id": path_ids[-2] if len(path_ids) > 1 else "",
        "level": len(path or [name]),
        "keywords": [name, category_id],
        "attributes": {
            "required": list(attrs.get("required") or []),
            "optional": list(attrs.get("optional") or []),
        },
        "raw": detail,
    }


__all__ = [
    "JsonClient",
    "http_json",
    "mercadolibre_catalog_attribute_top_values",
    "mercadolibre_category_attributes",
    "mercadolibre_category_detail",
    "mercadolibre_category_record",
    "mercadolibre_category_technical_specs",
    "mercadolibre_technical_spec_custom_value_rules",
    "ml_attr_required",
    "normalize_ml_attribute",
]
