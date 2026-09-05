from __future__ import annotations

"""商品/草稿保存、读取与删除的 Capability 契约。

删除属于破坏性写入：审批摘要与规范化参数由服务端快照函数生成，digest
绑定冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照复核，
模型既不能提供审批 payload，也不能在批准后篡改受保护参数。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductProfilePatch(BaseModel):
    """模型可见的 canonical 商品主档补丁；平台草稿不属于此能力。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int | None = None
    product_id: str = ""
    name: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    target_customer: str = ""
    sku: str = ""
    stock: str = ""
    upc: str = ""
    cost: str = ""
    materials: list[str] = Field(default_factory=list)
    selling_points: list[str] = Field(default_factory=list)
    package_includes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    avoid_claims: list[str] = Field(default_factory=list)
    description: str = ""
    dimensions: str = Field(
        default="",
        description="商品尺寸文本，例如 30x20x10cm。",
    )
    weight_kg: str = Field(
        default="",
        description="商品重量（kg），例如 0.8。字段名必须是 weight_kg。",
    )
    source: dict[str, JsonValue] = Field(default_factory=dict)
    marketplace_terms: dict[str, JsonValue] = Field(default_factory=dict)
    attributes: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "商品主档补充属性，对应 product_read 返回的 attributes 和前端商品补充属性。"
            "提供时替换整个字典；增删单个属性前先读取并保留其他键。"
            "来源属性由 source.attributes 单独保存。"
        ),
    )
    listing_overrides: dict[str, JsonValue] = Field(default_factory=dict)
    copy_results: dict[str, JsonValue] = Field(default_factory=dict)
    sku_items: list[dict[str, JsonValue]] = Field(default_factory=list)
    selected_sku_indices: list[int] = Field(default_factory=list)
    pricing_defaults: dict[str, JsonValue] = Field(default_factory=dict)
    publish_preview: dict[str, JsonValue] = Field(default_factory=dict)
    collect_status: str = ""
    collect_logs: list[JsonValue] = Field(default_factory=list)
    workflow_statuses: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ProductSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product: ProductProfilePatch

    @model_validator(mode="after")
    def require_product(self) -> "ProductSaveRequest":
        if not self.product.model_fields_set:
            raise ValueError("product 不能为空")
        return self


class ProductSaveResult(BaseModel):
    """写回执：有界、类型化的 mutation receipt。

    禁止携带完整商品聚合对象；完整数据只能由 focused read 能力读取。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str = Field(default="", max_length=160)
    changed_fields: Annotated[tuple[str, ...], Field(max_length=200)] = ()
    updated_at: str = Field(default="", max_length=64)
    changed: bool = False


class ProductDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class ProductDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deleted: int = Field(default=0, ge=0)
    deleted_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()


class DraftReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class DraftReadView(BaseModel):
    """draft_read 的类型化有界视图。

    只返回排查与下一步决策需要的字段：身份、状态、类目身份、已填写属性、
    价格/库存摘要、图片计数与预检/发布摘要。完整图片、发布日志、平台枚举
    值等通过 focused 分页工具读取；平台类目规则一律不进入视图。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: TrimmedText = ""
    product_id: TrimmedText = ""
    source_product_id: TrimmedText = ""
    platform: TrimmedText = ""
    site: TrimmedText = ""
    status: TrimmedText = ""
    publish_status: TrimmedText = ""
    title: TrimmedText = ""
    description: TrimmedText = ""
    brand: TrimmedText = ""
    model: TrimmedText = ""
    sku: TrimmedText = ""
    upc: TrimmedText = ""
    stock: TrimmedText = ""
    language: TrimmedText = ""
    category_id: TrimmedText = ""
    description_category_id: TrimmedText = ""
    category_path: TrimmedText = ""
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    image_count: int = 0
    validation_errors: tuple[JsonValue, ...] = ()
    category_precheck: dict[str, JsonValue] = Field(default_factory=dict)
    last_precheck: dict[str, JsonValue] = Field(default_factory=dict)
    last_publish_task: dict[str, JsonValue] = Field(default_factory=dict)
    publication: dict[str, JsonValue] = Field(default_factory=dict)
    pricing_summary: dict[str, JsonValue] = Field(default_factory=dict)
    target_sites: tuple[dict[str, JsonValue], ...] = ()


class DraftReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: DraftReadView = Field(default_factory=DraftReadView)
    product_context: dict[str, JsonValue] = Field(default_factory=dict)


class DraftSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_draft(self) -> "DraftSaveRequest":
        if not self.draft:
            raise ValueError("draft 不能为空")
        return self


class DraftSaveResult(BaseModel):
    """写回执：有界、类型化的 mutation receipt。

    禁止返回完整 draft、完整 product_context（含 raw）、products/drafts
    index、图片池或完整类目 Schema；保存后详情通过独立只读 Capability 获取。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str = Field(default="", max_length=160)
    product_id: str = Field(default="", max_length=160)
    platform: str = Field(default="", max_length=40)
    changed_fields: Annotated[tuple[str, ...], Field(max_length=200)] = ()
    updated_at: str = Field(default="", max_length=64)
    changed: bool = False


class DraftDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class DraftDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deleted: int = Field(default=0, ge=0)
    deleted_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()
    affected_product_ids: tuple[str, ...] = ()


class DraftStockUpdateRequest(BaseModel):
    """Focused write：平台草稿库存的唯一 owner 写入。

    发布流程中的库存以平台草稿为 owner；商品主档库存只能作为默认值或
    来源事实，不得替代目标市场草稿库存。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    stock: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=40),
    ]

    @model_validator(mode="after")
    def require_numeric_stock(self) -> "DraftStockUpdateRequest":
        if not self.stock.isdigit():
            raise ValueError("stock 必须是非负整数字符串，例如 10。")
        return self


class DraftStockUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str = Field(default="", max_length=160)
    stock: str = Field(default="", max_length=40)
    updated_at: str = Field(default="", max_length=64)
    changed: bool = False


class DraftPricingApplyRequest(BaseModel):
    """Focused write：把确定性核价结果持久化为平台草稿的最终售价。

    ``pricing_input`` 与 ``draft_prepare_for_market.pricing_input`` 同形：
    common 共享成本、target/targets 目标输入；只计算不应用不会落库。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    target_platform: Annotated[
        TrimmedText,
        StringConstraints(max_length=40),
    ] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=40)] = ""
    sales_target: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=120)]
    ] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "仅供任务补充界面的 Mercado Libre CBT 销售目标选择器列表；每项格式为 "
            "SITE_ID:logistic_type，例如 [\"MLM:remote\", \"MLB:remote\"]；"
            "初始计划必须留空列表。"
        ),
    )
    pricing_input: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_pricing_input(self) -> "DraftPricingApplyRequest":
        if not self.pricing_input:
            raise ValueError("pricing_input 不能为空；只计算不应用不会落库。")
        return self


class DraftPricingApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str = Field(default="", max_length=160)
    target_key: str = Field(default="", max_length=120)
    applied_price: str = Field(default="", max_length=80)
    fingerprint: str = Field(default="", max_length=160)
    changed: bool = False


class ProductProfilePatchRequest(BaseModel):
    """Focused write：商品主档部分补丁；未提供字段保持原值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product: ProductProfilePatch

    @model_validator(mode="after")
    def require_product(self) -> "ProductProfilePatchRequest":
        if not self.product.model_fields_set:
            raise ValueError("product 不能为空")
        return self


class ProductProfilePatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str = Field(default="", max_length=160)
    changed_fields: Annotated[tuple[str, ...], Field(max_length=200)] = ()
    updated_at: str = Field(default="", max_length=64)
    changed: bool = False


__all__ = [
    "DraftDeleteRequest",
    "DraftDeleteResult",
    "DraftPricingApplyRequest",
    "DraftPricingApplyResult",
    "DraftReadRequest",
    "DraftReadResult",
    "DraftSaveRequest",
    "DraftSaveResult",
    "DraftStockUpdateRequest",
    "DraftStockUpdateResult",
    "ProductDeleteRequest",
    "ProductDeleteResult",
    "ProductProfilePatch",
    "ProductProfilePatchRequest",
    "ProductProfilePatchResult",
    "ProductSaveRequest",
    "ProductSaveResult",
]
