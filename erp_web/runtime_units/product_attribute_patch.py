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


__all__ = ["apply_attribute_patch"]
