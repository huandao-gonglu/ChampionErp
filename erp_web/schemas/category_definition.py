# -*- coding: utf-8 -*-
"""统一类目定义契约与有界公共视图（类目 Schema 分离计划 Phase 0）。

平台类目规则的唯一所有权属于 CategoryProvider/Catalog；本模块冻结三类契约：

- ``CategoryDefinition``：当次临时平台规则，供属性填充、预检和 payload 编译使用。
  只保留校验与发布编译真正需要的规范化字段；不得包含平台原始报文（``raw``）、
  完整枚举 ``values`` 或 ``raw.values``。
- ``CategoryAttributePage`` / ``CategoryAttributeValuePage``：前端与 Agent 消费的
  有界分页公共视图，不含 ``platform_binding``。
- ``CategoryDetail``：类目详情（不含属性定义）。

``definition_fingerprint`` 是稳定语义指纹：相同语义定义跨进程产生相同指纹；
``fetched_at``、缓存时间戳等易变元数据不参与计算。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: 定义序列化格式版本；归一化形状变化时必须递增，使旧缓存自动失效。
DEFINITION_FORMAT_VERSION = 5

#: options 有界预览上限；公共视图与内部定义共用该边界。
ATTRIBUTE_OPTIONS_PREVIEW_LIMIT = 50


class CategoryCacheState(BaseModel):
    """定义读取的缓存状态；仅用于展示与 stale 判断，不参与指纹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal["live", "cache", "stale"] = "live"
    stale: bool = False
    retrieved_at: str = ""
    expires_at: str = ""
    stale_until: str = ""


class CategoryAttributePlatformBinding(BaseModel):
    """发布必需的归一化平台 wire 字段。

    Provider 负责把平台原始字段归一化到这里；发布代码不得再从 ``raw`` 猜测字段。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Ozon attribute_complex_id（复合属性归属）。
    complex_id: str = ""
    #: Yandex 属性的平台侧类型描述（例如 aspect/requirement 归类）。
    aspect: str = ""
    #: 平台原始 attribute type（仅用于 payload 编译的必要映射）。
    platform_type: str = ""


class CategoryUnitOption(BaseModel):
    """单位选项的规范化形状。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = ""
    name: str = ""


class CategoryAttributeOptionPreview(BaseModel):
    """枚举值的有界预览项；完整枚举只能通过 attribute_values 分页读取。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = ""
    dictionary_value_id: str = ""


class CategoryAttributeDefinition(BaseModel):
    """单个属性定义的规范化形状（内部视图，含 platform_binding）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    required: bool = False
    #: variant 为变体维度，parent 为同组必须一致的属性。
    variation_role: str = ""
    #: 归一化值类型：string/integer/float/boolean/enum/date/datetime 等。
    value_type: str = ""
    #: 归一化取值模式：single/multiple。
    value_mode: str = ""
    allow_custom_values: bool = False
    #: 平台声明只读/推导属性时，发布方不得提交。
    read_only: bool = False
    #: 平台校验约束的规范化投影（min/max/regex 等），有界键值。
    constraints: dict[str, str] = Field(default_factory=dict)
    dictionary_id: str = ""
    is_dictionary: bool = False
    is_collection: bool = False
    max_value_count: int | None = None
    #: Ozon 平台事实：字典候选是否随类目变化。
    category_dependent: bool = False
    default_unit: str = ""
    default_unit_id: str = ""
    unit_options: tuple[CategoryUnitOption, ...] = ()
    unit_ids: tuple[str, ...] = ()
    platform_binding: CategoryAttributePlatformBinding = Field(
        default_factory=CategoryAttributePlatformBinding
    )
    #: 有界枚举预览；完整候选全集不得进入定义。
    options: tuple[CategoryAttributeOptionPreview, ...] = ()
    #: 字典候选是否超过预览上限（需要分页读取完整枚举）。
    has_more_values: bool = False


class CategoryDefinition(BaseModel):
    """当次临时平台类目规则；不是持久化商品数据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    site: str = ""
    category_id: str
    category_path: str = ""
    description_category_id: str = ""
    fingerprint: str = ""
    cache: CategoryCacheState = Field(default_factory=CategoryCacheState)
    required: tuple[CategoryAttributeDefinition, ...] = ()
    optional: tuple[CategoryAttributeDefinition, ...] = ()

    def attribute_by_id(self, attribute_id: str) -> CategoryAttributeDefinition | None:
        target = str(attribute_id or "").strip()
        if not target:
            return None
        for attribute in (*self.required, *self.optional):
            if attribute.id == target:
                return attribute
        return None


class CategoryDetail(BaseModel):
    """类目详情（身份与展示字段；不含属性定义）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    site: str = ""
    category_id: str
    name: str = ""
    path: str = ""
    parent_id: str = ""
    is_leaf: bool = False
    active: bool = True


class CategoryAttributeSummary(BaseModel):
    """公共有界摘要：前端属性编辑器与 Agent 工具共用。

    不含 ``raw``、``platform_binding``、完整 ``values``；options 仅为有界预览。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    required: bool = False
    value_type: str = ""
    value_mode: str = ""
    allow_custom_values: bool = False
    read_only: bool = False
    is_dictionary: bool = False
    is_collection: bool = False
    max_value_count: int | None = None
    dictionary_id: str = ""
    default_unit: str = ""
    default_unit_id: str = ""
    unit_options: tuple[CategoryUnitOption, ...] = ()
    options: tuple[CategoryAttributeOptionPreview, ...] = ()
    has_more_values: bool = False


class CategoryAttributePage(BaseModel):
    """属性定义分页公共视图；必须携带 limit/cursor/next_cursor/has_more。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    site: str = ""
    category_id: str
    category_path: str = ""
    limit: int = 50
    cursor: str = ""
    attributes: tuple[CategoryAttributeSummary, ...] = ()
    next_cursor: str = ""
    has_more: bool = False


class CategoryAttributeValue(BaseModel):
    """枚举候选值（当前页项）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = ""
    dictionary_value_id: str = ""


class CategoryAttributeValuePage(BaseModel):
    """枚举候选分页视图；候选全集不得一次性进入模型上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    site: str = ""
    category_id: str
    attribute_id: str
    limit: int = 50
    cursor: str = ""
    values: tuple[CategoryAttributeValue, ...] = ()
    next_cursor: str = ""
    has_more: bool = False


# ---------------------------------------------------------------------------
# 稳定指纹
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _attribute_fingerprint_projection(
    attribute: CategoryAttributeDefinition,
) -> dict[str, Any]:
    """参与指纹的稳定字段投影；排除时间戳等非语义元数据。"""

    return {
        "id": attribute.id,
        "name": attribute.name,
        "required": attribute.required,
        "value_type": attribute.value_type,
        "value_mode": attribute.value_mode,
        "allow_custom_values": attribute.allow_custom_values,
        "read_only": attribute.read_only,
        "constraints": dict(attribute.constraints),
        "dictionary_id": attribute.dictionary_id,
        "is_dictionary": attribute.is_dictionary,
        "is_collection": attribute.is_collection,
        "max_value_count": attribute.max_value_count,
        "category_dependent": attribute.category_dependent,
        "default_unit": attribute.default_unit,
        "default_unit_id": attribute.default_unit_id,
        "unit_options": sorted(
            [
                {"id": unit.id, "name": unit.name}
                for unit in attribute.unit_options
            ],
            key=lambda item: (item["id"], item["name"]),
        ),
        "unit_ids": sorted(attribute.unit_ids),
        "platform_binding": {
            "complex_id": attribute.platform_binding.complex_id,
            "aspect": attribute.platform_binding.aspect,
            "platform_type": attribute.platform_binding.platform_type,
        },
        # 发布编译器会使用预览中的 dictionary_value_id 将草稿文案编译成
        # Mercado 等平台的枚举 wire 值，因此预览内容本身属于 payload 语义。
        "options": sorted(
            [
                {
                    "value": option.value,
                    "dictionary_value_id": option.dictionary_value_id,
                }
                for option in attribute.options
            ],
            key=lambda item: (item["dictionary_value_id"], item["value"]),
        ),
        "has_more_values": attribute.has_more_values,
    }


def definition_fingerprint_projection(
    definition: CategoryDefinition,
) -> dict[str, Any]:
    """指纹语义投影：全部影响校验与 payload 的稳定字段。

    属性按 id 排序，保证跨进程、跨 Provider 的确定性；明确排除
    ``fetched_at``、``retrieved_at``、``expires_at``、``stale_until``、
    缓存 source 等易变元数据。枚举 options 虽是有界预览，但会影响 payload
    编译，因此必须参与指纹。
    """

    attributes = sorted(
        (*definition.required, *definition.optional),
        key=lambda item: item.id,
    )
    return {
        "format_version": DEFINITION_FORMAT_VERSION,
        "platform": definition.platform,
        "site": definition.site,
        "category_id": definition.category_id,
        "description_category_id": definition.description_category_id,
        "attributes": [
            _attribute_fingerprint_projection(attribute)
            for attribute in attributes
        ],
    }


def definition_fingerprint(definition: CategoryDefinition) -> str:
    """计算定义的稳定指纹：SHA-256(canonical_json(语义投影))。"""

    projection = definition_fingerprint_projection(definition)
    payload = _canonical_json(projection).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ATTRIBUTE_OPTIONS_PREVIEW_LIMIT",
    "DEFINITION_FORMAT_VERSION",
    "CategoryAttributeDefinition",
    "CategoryAttributeOptionPreview",
    "CategoryAttributePage",
    "CategoryAttributePlatformBinding",
    "CategoryAttributeSummary",
    "CategoryAttributeValue",
    "CategoryAttributeValuePage",
    "CategoryCacheState",
    "CategoryDefinition",
    "CategoryDetail",
    "CategoryUnitOption",
    "definition_fingerprint",
    "definition_fingerprint_projection",
]
