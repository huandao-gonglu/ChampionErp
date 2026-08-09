from __future__ import annotations

"""类目属性枚举查询的稳定数据形状与候选账本。"""

from dataclasses import dataclass, field
from typing import Any


CATEGORY_ATTRIBUTE_VALUE_PERMISSION = "category.read"
CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID = "category.attribute_values"


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


@dataclass
class CategoryAttributeValueLedger:
    """记录一次 Agent run 中工具真实返回的平台枚举值。"""

    definitions: dict[str, dict[str, Any]]
    candidates: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    attempts: list[dict[str, str]] = field(default_factory=list)
    failed_attribute_ids: set[str] = field(default_factory=set)

    @classmethod
    def from_schema(
        cls,
        schema: list[dict[str, Any]],
    ) -> "CategoryAttributeValueLedger":
        return cls(
            definitions={
                attr_id: dict(item)
                for item in schema
                if (attr_id := _text(item.get("id"), 160))
            }
        )

    def definition(self, attribute_id: str) -> dict[str, Any] | None:
        item = self.definitions.get(_text(attribute_id, 160))
        return dict(item) if item is not None else None

    def record_attempt(self, attribute_id: str, query: str) -> None:
        self.attempts.append(
            {
                "attribute_id": _text(attribute_id, 160),
                "query": _text(query, 255),
            }
        )

    def record_failure(self, attribute_id: str) -> None:
        attr_id = _text(attribute_id, 160)
        if attr_id:
            self.failed_attribute_ids.add(attr_id)

    def add_values(
        self,
        attribute_id: str,
        values: list[dict[str, Any]],
    ) -> None:
        attr_id = _text(attribute_id, 160)
        if not attr_id:
            return
        stored = self.candidates.setdefault(attr_id, {})
        for item in values:
            if not isinstance(item, dict):
                continue
            value_id = _text(
                item.get("dictionary_value_id") or item.get("id"),
                160,
            )
            value = _text(item.get("value") or item.get("name"))
            if value_id and value:
                stored[value_id] = {
                    "dictionary_value_id": value_id,
                    "value": value,
                }

    def get(
        self,
        attribute_id: str,
        dictionary_value_id: str,
    ) -> dict[str, str] | None:
        candidate = self.candidates.get(_text(attribute_id, 160), {}).get(
            _text(dictionary_value_id, 160)
        )
        return dict(candidate) if candidate is not None else None

    def was_queried(self, attribute_id: str) -> bool:
        attr_id = _text(attribute_id, 160)
        return any(item["attribute_id"] == attr_id for item in self.attempts)


__all__ = [
    "CATEGORY_ATTRIBUTE_VALUE_PERMISSION",
    "CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID",
    "CategoryAttributeValueLedger",
]
