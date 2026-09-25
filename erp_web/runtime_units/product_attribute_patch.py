"""属性局部修改的纯函数；不读取数据、不发请求、不保存。"""

from copy import deepcopy
from typing import Any


def apply_attribute_patch(existing: dict[str, Any], updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    attributes = deepcopy(existing)
    for key, value in updates.items():
        if value is None:
            attributes.pop(key, None)
        else:
            attributes[key] = deepcopy(value)
    return attributes, [key for key in updates if existing.get(key) != attributes.get(key)]


def shared_attribute_fields(platform: str, updates: dict[str, Any], *, sku_id: str) -> dict[str, str]:
    """Mercado 公共品牌/型号由草稿根字段持有，枚举仅保留同名元数据。"""
    fields = {}
    if platform == "mercadolibre" and not sku_id:
        for attr_id, field in (("BRAND", "brand"), ("MODEL", "model")):
            if attr_id not in updates:
                continue
            value = updates[attr_id]
            if isinstance(value, dict):
                value = (value.get("values") or [{}])[0].get("value") if "values" in value else value.get("value")
            fields[field] = str(value or "").strip()
    return fields


__all__ = ["apply_attribute_patch", "shared_attribute_fields"]
