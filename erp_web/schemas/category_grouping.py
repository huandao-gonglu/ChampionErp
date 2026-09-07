"""刊登分组字段的唯一派生规则；不读取商品库或调用模型、平台。"""

from hashlib import sha256
from typing import Any, Mapping


def is_listing_grouping_attribute(platform: str, definition: Mapping[str, Any]) -> bool:
    """按平台语义识别分组字段，不写死 Ozon 的类目属性编号。"""
    if platform == "ozon":
        return str(definition.get("name") or "").strip().casefold() == "объединить на одной карточке"
    return platform == "yandex" and str(definition.get("id") or "") == "200"


def listing_group_name(draft: Mapping[str, Any]) -> str:
    grouping = draft.get("grouping") or {}
    return str(grouping.get("name") or draft.get("title") or "").strip()


def listing_grouping_value(
    draft: Mapping[str, Any], definition: Mapping[str, Any], *, seller_sku: str | None = None,
) -> str:
    """组合使用共同组名；独立刊登省略可选组名，必填组名按卖家 SKU 隔离。"""
    if (draft.get("grouping") or {}).get("mode", "combined") == "combined":
        return listing_group_name(draft)
    if not definition.get("required"):
        return ""
    identity = str((draft.get("sku") if seller_sku is None else seller_sku) or "").strip()
    if not identity:
        return ""
    return "sku-" + sha256(identity.encode("utf-8")).hexdigest()[:24]


def apply_listing_grouping_attributes(
    attributes: Mapping[str, Any], draft: Mapping[str, Any], platform: str,
    definitions: list[Mapping[str, Any]], *, seller_sku: str | None = None,
) -> dict[str, Any]:
    """发布值只由当前刊登设置派生，旧公共值及 SKU 覆盖值不能改变分组。"""
    result = dict(attributes)
    for definition in definitions:
        if not is_listing_grouping_attribute(platform, definition):
            continue
        attr_id = str(definition["id"])
        result.pop(attr_id, None)
        value = listing_grouping_value(draft, definition, seller_sku=seller_sku)
        if value and not definition.get("read_only"):
            result[attr_id] = value
    return result


__all__ = [
    "apply_listing_grouping_attributes", "is_listing_grouping_attribute",
    "listing_group_name", "listing_grouping_value",
]
