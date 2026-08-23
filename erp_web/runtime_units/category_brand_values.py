"""品牌类目属性的官方“无品牌”字典值解析与确定性填充。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from erp_web.schemas.category import (
    category_attribute_schema,
    category_attribute_value_is_valid,
    is_category_dictionary_attribute,
)
from erp_web.schemas.category_brand import (
    is_brand_attribute,
    is_no_brand_fact,
    is_official_no_brand_value,
    no_brand_query_term,
    product_context_declares_no_brand,
)


AttributeValuesLoader = Callable[..., dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def dictionary_value_rows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """把公共枚举页压缩为 strict_enum 可持久化的 ID/value 结构。"""

    values = payload.get("values")
    if not isinstance(values, list):
        return []
    rows: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        value_id = _text(item.get("id") or item.get("dictionary_value_id"))
        value = _text(item.get("value") or item.get("name"))
        if value_id and value:
            rows.append({"dictionary_value_id": value_id, "value": value})
    return rows


def definition_is_brand(
    definition: Mapping[str, Any],
    *,
    platform: str,
) -> bool:
    """属性是否为当前平台的 strict_enum 品牌字典。"""

    value_mode = _text(definition.get("value_mode"))
    return bool(
        (not value_mode or value_mode == "strict_enum")
        and is_category_dictionary_attribute(dict(definition))
        and is_brand_attribute(definition, platform=platform)
    )


def find_brand_definition(
    schema: list[dict[str, Any]],
    *,
    platform: str,
) -> dict[str, Any] | None:
    for definition in schema:
        if definition_is_brand(definition, platform=platform):
            return definition
    return None


def resolve_no_brand_option(
    loader: AttributeValuesLoader,
    *,
    platform: str,
    category_id: str,
    attribute_id: str,
    site: str,
) -> dict[str, str] | None:
    """从当前类目的实时平台字典解析官方“无品牌”值。

    不缓存、不硬编码 ID。Provider 已按凭据和类目提供 TTL 缓存；这里再次做
    全局缓存会污染多店铺作用域，硬编码跨类目 ID 也无法证明候选仍然有效。
    """

    term = no_brand_query_term(platform)
    if not term:
        return None
    try:
        payload = loader(
            platform,
            category_id,
            attribute_id,
            site=site,
            query=term,
            limit=20,
            timeout_seconds=15,
        )
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    return next(
        (
            row
            for row in dictionary_value_rows(payload)
            if is_official_no_brand_value(platform, row["value"])
        ),
        None,
    )


def _brand_context(
    draft: Mapping[str, Any],
    product: Mapping[str, Any] | None,
) -> dict[str, Any]:
    product_record = product if isinstance(product, Mapping) else {}
    source = product_record.get("source")
    source_record = source if isinstance(source, Mapping) else {}
    return {
        "draft": {"brand": draft.get("brand")},
        "product": {"brand": product_record.get("brand")},
        "source": {"brand": source_record.get("brand")},
    }


def apply_no_brand_attribute(
    draft: dict[str, Any],
    *,
    product: Mapping[str, Any] | None,
    platform: str,
    record: Mapping[str, Any],
    category_id: str,
    site: str,
    loader: AttributeValuesLoader,
) -> str:
    """把明确无品牌商品写为当前平台返回的官方 strict_enum 候选。"""

    brand_definition = find_brand_definition(
        category_attribute_schema(dict(record)),
        platform=platform,
    )
    if brand_definition is None:
        return ""
    attr_id = _text(brand_definition.get("id"))
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    if category_attribute_value_is_valid(brand_definition, attributes.get(attr_id)):
        return ""
    if not product_context_declares_no_brand(_brand_context(draft, product)):
        return ""
    option = resolve_no_brand_option(
        loader,
        platform=platform,
        category_id=category_id,
        attribute_id=attr_id,
        site=site,
    )
    if option is None:
        return ""
    draft["attributes"] = {
        **attributes,
        attr_id: {"values": [dict(option)]},
    }
    return attr_id


__all__ = [
    "AttributeValuesLoader",
    "apply_no_brand_attribute",
    "definition_is_brand",
    "dictionary_value_rows",
    "find_brand_definition",
    "is_no_brand_fact",
    "resolve_no_brand_option",
]
