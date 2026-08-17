from __future__ import annotations

"""Yandex Market wire/状态机 shape。

本模块只定义 Yandex 平台自身的请求与状态机结构。平台响应先在 HTTP 边界
（``marketplaces/yandex_http.py``）转换为这些 shape；进入草稿与通用类目 UI
前，再由 Provider 转换为 ``schemas/category.py`` 和 ``schemas/product.py``
的共享 shape。通用草稿字段不出现 ``parameterId/valueId`` 等平台专用键。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


YandexPublishPhase = Literal[
    "offer_mapping",
    "campaign_offer",
    "price",
    "stock",
    "confirmation",
    "terminal",
]

# 与 Yandex 店铺模型对应的库存写入方式。
YandexStockUpdateMode = Literal["campaign_warehouses", "business", "none"]


class YandexTokenInfo(BaseModel):
    """``POST /v2/auth/token`` 解析出的 API-Key 信息。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    auth_scopes: list[str] = Field(default_factory=list)


class YandexCampaignInfo(BaseModel):
    """``GET /v2/campaigns/{campaignId}`` 解析出的店铺信息。"""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    business_id: str = ""
    business_name: str = ""
    shop_name: str = ""
    placement_type: str = ""
    api_availability: str = ""

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_required(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("campaign_id 不能为空")
        return str(value).strip()

    @property
    def api_available(self) -> bool:
        return self.api_availability.strip().upper() == "AVAILABLE"


class YandexCategoryRecord(BaseModel):
    """Yandex 类目树节点的规范记录（叶子判定由 Provider 负责）。"""

    model_config = ConfigDict(extra="forbid")

    category_id: str
    name: str = ""
    parent_id: str = ""
    is_leaf: bool = False
    path_segments: list[str] = Field(default_factory=list)
    children_count: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category_id")
    @classmethod
    def _category_id_required(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("category_id 不能为空")
        return str(value).strip()


class YandexCategoryParameterValue(BaseModel):
    """类目属性枚举值；``value_id`` 规范化为字符串。"""

    model_config = ConfigDict(extra="forbid")

    value_id: str = ""
    value: str = ""


class YandexCategoryParameterUnit(BaseModel):
    """类目参数允许的单位（``unit.units[]`` 项）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str = ""
    full_name: str = ""


class YandexCategoryParameter(BaseModel):
    """类目属性定义（平台边界转换前的 Yandex 视图）。

    官方 CategoryParameterDTO 关键字段：``multivalue``（是否多值）、
    ``allowCustomValues``（ENUM 是否允许自定义值）、``unit.defaultUnitId``
    与 ``unit.units[]``（单位 ID）、``constraints``（数值/文本约束）。
    """

    model_config = ConfigDict(extra="forbid")

    parameter_id: str
    name: str = ""
    required: bool = False
    parameter_type: str = ""
    is_collection: bool = False
    allow_custom_values: bool = False
    max_value_count: int = 0
    unit: str = ""
    unit_options: list[str] = Field(default_factory=list)
    default_unit: str = ""
    default_unit_id: str = ""
    units: list[YandexCategoryParameterUnit] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    values: list[YandexCategoryParameterValue] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameter_id")
    @classmethod
    def _parameter_id_required(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("parameter_id 不能为空")
        return str(value).strip()


class YandexOfferMappingPayload(BaseModel):
    """``offer-mappings/update`` 的单个商品写入体。"""

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    name: str
    market_category_id: str = ""
    pictures: list[str] = Field(default_factory=list)
    vendor: str = ""
    description: str = ""
    parameter_values: list[dict[str, Any]] = Field(default_factory=list)
    weight_dimensions: dict[str, Any] = Field(default_factory=dict)
    # 官方 basicPrice.value 为 number，currencyId 为平台枚举（如 RUR）。
    basic_price: dict[str, Any] | None = None

    @field_validator("offer_id", "name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("offerId 与 name 不能为空")
        return str(value).strip()


class YandexPublishCheckpoint(BaseModel):
    """Yandex 发布状态机检查点：可持久化、无凭据。

    PublishingBus 通过 pending result 持久化本结构并在重启后恢复；
    绝不包含 API-Key 或其它秘密。
    """

    model_config = ConfigDict(extra="forbid")

    phase: YandexPublishPhase = "offer_mapping"
    completed_steps: list[str] = Field(default_factory=list)
    offer_id: str = ""
    campaign_id: str = ""
    business_id: str = ""
    # 各已完成 mutation 的远端事实/证据（task id、响应状态等）。
    evidence: dict[str, Any] = Field(default_factory=dict)
    last_response_summary: dict[str, Any] = Field(default_factory=dict)
    retries: int = 0
    # 下一次允许轮询的 epoch 秒；poller 使用有界退避。
    next_poll_at: float = 0.0
    warnings: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("completed_steps")
    @classmethod
    def _unique_steps(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item) for item in value if str(item)))

    def step_done(self, step: str) -> bool:
        return str(step) in self.completed_steps


class YandexPublishResult(BaseModel):
    """Yandex 发布终态回读结果。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: str
    offer_id: str = ""
    external_id: str = ""
    campaign_id: str = ""
    business_id: str = ""
    campaign_status: str = ""
    card_status: str = ""
    checked_at: str = ""
    checkpoint: YandexPublishCheckpoint | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    field_errors: dict[str, list[str]] = Field(default_factory=dict)


__all__ = [
    "YandexCampaignInfo",
    "YandexCategoryParameter",
    "YandexCategoryParameterUnit",
    "YandexCategoryParameterValue",
    "YandexCategoryRecord",
    "YandexOfferMappingPayload",
    "YandexPublishCheckpoint",
    "YandexPublishPhase",
    "YandexPublishResult",
    "YandexStockUpdateMode",
    "YandexTokenInfo",
]
