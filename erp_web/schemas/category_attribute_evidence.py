"""属性翻译的来源引用：代码核对原字段/原文，Agent 负责等义判断。"""

from typing import Annotated, Any
import re

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class CategoryAttributeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: list[Annotated[str, StringConstraints(min_length=1, max_length=160)]] = Field(
        min_length=2, max_length=6,
        description="仅引用 product_context.product、source 或 sku_scope.common_options 中实际存在的字段，不得引用 draft；例如 ['source','attributes','适合季节'] 或 ['sku_scope','common_options','颜色']；必须包含末级字段，不能停在 common_options 对象",
    )
    source_value: Annotated[str, StringConstraints(min_length=1, max_length=1000)] = Field(
        description="该字段的完整原文；保留原语言，不得只截取多值中的一项",
    )
    reason: Annotated[str, StringConstraints(min_length=1, max_length=300)] = Field(
        description="用一句短句说明原文与目标枚举等义，不重复原文或规则；不能由材质、类目或常识推导未声明的性能",
    )


def evidence_reference_is_valid(reference: dict[str, Any], context: dict[str, Any]) -> bool:
    path = reference.get("source_path")
    if not isinstance(path, list) or len(path) < 2:
        return False
    if path[0] not in {"source", "product"} and path[:2] != ["sku_scope", "common_options"]:
        return False
    current: Any = context
    for part in path:
        if not isinstance(current, dict) or not isinstance(part, str) or part not in current:
            return False
        current = current[part]
    # 字符串原文必须完整相等；不允许以整张表、对象或一个子串充当事实。
    return isinstance(current, str) and bool(current.strip()) and current == reference.get("source_value")


def has_translated_attribute_evidence(
    value: str, definition: dict[str, Any], reference: Any, context: dict[str, Any],
) -> bool:
    if (
        not isinstance(reference, dict)
        or not str(reference.get("reason") or "").strip()
        or not evidence_reference_is_valid(reference, context)
    ):
        return False
    # 翻译用于描述性枚举。数值、单位换算、链接和自由文本仍走各自的事实校验。
    return (
        definition.get("value_mode") in {"strict_enum", "open_enum"}
        and not re.search(r"\d|https?://", value, re.IGNORECASE)
        and not definition.get("unit_options")
        and str(definition.get("value_type") or "").lower()
        not in {"number_unit", "numeric", "number", "integer", "decimal", "float"}
    )


def attribute_evidence_sources(context: dict[str, Any]) -> list[dict[str, Any]]:
    """提供来源的完整字符串末级引用；对象、数组和草稿不能充当翻译证据。"""
    references: list[dict[str, Any]] = []

    def collect(value: Any, path: list[str]) -> None:
        if len(path) > 6 or len(references) >= 100:
            return
        if isinstance(value, str) and value.strip() and len(value) <= 1000:
            references.append({"source_path": path, "source_value": value})
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and 0 < len(key) <= 160:
                    collect(item, [*path, key])

    collect((context.get("sku_scope") or {}).get("common_options"), ["sku_scope", "common_options"])
    for section in ("source", "product"):
        collect(context.get(section), [section])
    return references
