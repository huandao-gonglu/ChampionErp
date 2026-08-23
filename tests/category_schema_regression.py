# -*- coding: utf-8 -*-
"""旧草稿超大输出回归 fixture（类目 Schema 分离计划 Phase 0）。

事故复盘：草稿顶层与 ``target_sites[0]`` 各保存一份完整
``category_attribute_schema``，单个枚举属性同时携带 ``options``、规范化
``values`` 与平台原始 ``raw.values``，导致 ``draft_read`` 工具输出超过
256 KiB 上限（TOOL_OUTPUT_TOO_LARGE）。

本模块构造与事故草稿同构的 legacy fixture：
- ``build_legacy_category_schema``：构造含完整枚举三件套的属性 Schema；
- ``build_legacy_draft_with_schema``：把同一 Schema 放进草稿顶层与第一个
  target site，完全复刻事故持久化形状。
"""

from __future__ import annotations

import json
from typing import Any

#: 与全局对话配置一致的工具输出上限（字节）。
TOOL_OUTPUT_LIMIT_BYTES = 262_144


def _legacy_enum_attribute(
    attribute_id: str,
    name: str,
    *,
    option_count: int,
    required: bool = False,
) -> dict[str, Any]:
    """构造同时携带 options/values/raw.values 的旧式枚举属性定义。"""

    values = [
        {
            "id": str(30_000_000 + index),
            "value": f"{name}候选值{index:04d}",
            "name": f"{name}候选值{index:04d}",
        }
        for index in range(option_count)
    ]
    return {
        "id": attribute_id,
        "name": name,
        "required": required,
        "value_type": "string",
        "value_mode": "strict_enum",
        "allow_custom_values": False,
        "unit": "",
        "unit_options": [],
        "default_unit": "",
        "unit_ids": {},
        "default_unit_id": "",
        "constraints": {},
        "description": f"{name} 的枚举属性，候选值来自平台字典。",
        "options": [item["value"] for item in values],
        "dictionary_id": f"legacy-dictionary-{attribute_id}",
        "is_dictionary": True,
        "is_collection": True,
        "max_value_count": 10,
        "category_dependent": False,
        "values": values,
        "raw": {
            "id": int(attribute_id) if str(attribute_id).isdigit() else attribute_id,
            "name": name,
            "is_required": required,
            "type": "String",
            "is_collection": True,
            "dictionary_id": 124413209,
            "values": [
                {"id": int(item["id"]), "value": item["value"], "info": "", "picture": ""}
                for item in values
            ],
        },
    }


def _legacy_plain_attribute(attribute_id: str, name: str, *, required: bool) -> dict[str, Any]:
    return {
        "id": attribute_id,
        "name": name,
        "required": required,
        "value_type": "string",
        "value_mode": "free_text",
        "allow_custom_values": True,
        "unit": "",
        "unit_options": [],
        "default_unit": "",
        "unit_ids": {},
        "default_unit_id": "",
        "constraints": {"max_length": "255"},
        "description": "",
        "options": [],
        "dictionary_id": "",
        "is_dictionary": False,
        "is_collection": False,
        "max_value_count": 1,
        "category_dependent": False,
    }


def build_legacy_category_schema(
    *,
    platform: str = "yandex",
    site: str = "global",
    category_id: str = "16088928",
    category_path: str = "服装和配饰 / 服装",
    enum_option_count: int = 600,
) -> dict[str, Any]:
    """构造旧式完整属性 Schema（含三件套枚举），共 19 个属性。"""

    required = [
        _legacy_enum_attribute(
            "14871214",
            "颜色",
            option_count=enum_option_count,
            required=True,
        ),
        _legacy_enum_attribute(
            "10096",
            "尺码",
            option_count=enum_option_count,
            required=True,
        ),
        _legacy_plain_attribute("85", "品牌", required=True),
        _legacy_plain_attribute("9048", "型号名称", required=True),
    ]
    optional = [
        _legacy_plain_attribute(str(4000 + index), f"可选属性{index}", required=False)
        for index in range(15)
    ]
    return {
        "platform": platform,
        "site": site,
        "category_id": category_id,
        "category_path": category_path,
        "source": f"{platform}_live",
        "fetched_at": "2026-08-22T00:00:00+00:00",
        "required": required,
        "optional": optional,
    }


def build_legacy_draft_with_schema(
    *,
    draft_id: str = "d5c8f09c565e1",
    product_id: str = "p5c8f09c565e1",
    platform: str = "yandex",
    site: str = "global",
    category_id: str = "16088928",
    enum_option_count: int = 600,
) -> dict[str, Any]:
    """复刻事故草稿：同一 Schema 同时存在于草稿顶层与 target_sites[0]。"""

    schema = build_legacy_category_schema(
        platform=platform,
        site=site,
        category_id=category_id,
        enum_option_count=enum_option_count,
    )
    import copy

    return {
        "draft_id": draft_id,
        "product_id": product_id,
        "platform": platform,
        "site": site,
        "status": "in_progress",
        "title": "事故回归草稿",
        "category_id": category_id,
        "category_path": schema["category_path"],
        "attributes": {
            "14871214": {
                "values": [{"dictionary_value_id": "30072093", "value": "蓝色"}]
            }
        },
        "category_attribute_schema": schema,
        "target_sites": [
            {
                "platform": platform,
                "site": site,
                "language": "ru-RU",
                "market_currency": "RUB",
                "listing_currency": "RUB",
                "category_id": category_id,
                "category_path": schema["category_path"],
                "category_attribute_schema": copy.deepcopy(schema),
                "attributes": {
                    "14871214": {
                        "values": [
                            {"dictionary_value_id": "30072093", "value": "蓝色"}
                        ]
                    }
                },
            }
        ],
    }


def serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


__all__ = [
    "TOOL_OUTPUT_LIMIT_BYTES",
    "build_legacy_category_schema",
    "build_legacy_draft_with_schema",
    "serialized_size",
]
