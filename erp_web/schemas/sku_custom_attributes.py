"""Mercado User Products 自定义规格的输入契约，不冒充类目字段 ID。"""

from typing import Any
import unicodedata


def custom_attribute_key(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def custom_attribute_entries(value: Any, definitions: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], ["自定义 SKU 属性必须是名称和值的列表"]
    reserved = {custom_attribute_key(item) for attr in definitions for item in (attr.id, attr.name)}
    seen: set[str] = set()
    entries, errors = [], []
    for item in value:
        if not isinstance(item, dict):
            errors.append("自定义 SKU 属性格式错误")
            continue
        name, content = str(item.get("name") or "").strip(), str(item.get("value") or "").strip()
        key = custom_attribute_key(name)
        if not name or not content:
            errors.append("自定义 SKU 属性的名称和值都需要填写")
        elif key in reserved:
            errors.append(f"自定义属性 {name} 与当前类目字段重复，请填写对应平台字段")
        elif key in seen:
            errors.append(f"自定义 SKU 属性名称重复：{name}")
        else:
            entries.append({"name": name, "values": [{"name": content}]})
        seen.add(key)
    return entries, errors


__all__ = ["custom_attribute_entries", "custom_attribute_key"]
