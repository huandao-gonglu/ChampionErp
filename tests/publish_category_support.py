# -*- coding: utf-8 -*-
"""发布测试的类目定义注入支撑（类目 Schema 分离计划 Phase 2）。

新契约下发布链路不再读取草稿/商品上的规则副本；测试通过
``StaticDefinitionCatalog`` 把当次临时定义注入 CategoryCatalog 入口。
"""

from __future__ import annotations

from typing import Any

from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
)
from erp_web.schemas.category_definition import CategoryDefinition


def record_from_schema(
    *,
    platform: str,
    category_id: str,
    schema: dict[str, Any],
    category_path: str = "",
    description_category_id: str = "",
) -> dict[str, Any]:
    """旧测试 schema fixture → 内部 legacy record shape（过渡视图）。"""

    return {
        "platform": platform,
        "site": str(schema.get("site") or ""),
        "category_id": category_id,
        "description_category_id": description_category_id,
        "category_path": category_path,
        "source": f"{platform}_live",
        "attributes": {
            "required": list(schema.get("required") or []),
            "optional": list(schema.get("optional") or []),
        },
    }


def definition_from_record(record: dict[str, Any]) -> CategoryDefinition:
    attributes = (
        record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    )
    return definition_from_legacy_attributes(
        platform=str(record.get("platform") or ""),
        site=str(record.get("site") or ""),
        category_id=str(record.get("category_id") or ""),
        category_path=str(record.get("category_path") or ""),
        description_category_id=str(record.get("description_category_id") or ""),
        required=list(attributes.get("required") or []),
        optional=list(attributes.get("optional") or []),
    )


__all__ = [
    "definition_from_record",
    "record_from_schema",
]
