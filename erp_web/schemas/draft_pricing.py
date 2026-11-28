"""草稿核价契约：只提交本次修改，采购成本和包装资料由系统读取。"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
PositiveRate = Annotated[float, Field(gt=0, allow_inf_nan=False)]
ReturnRate = Annotated[float, Field(ge=0, lt=100, allow_inf_nan=False)]


class PricingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExchangeRateOverrides(PricingModel):
    usd_cny_rate: PositiveRate | None = None
    mxn_usd_rate: PositiveRate | None = None
    rub_cny_rate: PositiveRate | None = None


class PricingCommonPatch(PricingModel):
    domestic_freight_cny: Amount | None = Field(default=None, description="国内物流费用，人民币；与国际运费独立。")
    packaging_cost_cny: Amount | None = None
    other_cost_cny: Amount | None = None
    exchange_rate_mode: Literal["live", "manual"] | None = None
    usd_cny_rate: Rate | None = None
    mxn_usd_rate: Rate | None = None
    rub_cny_rate: Rate | None = None
    exchange_rate_overrides: ExchangeRateOverrides | None = None


class PricingMoney(PricingModel):
    amount: Rate
    currency: str = Field(max_length=3, description="按页面已显示的发布币种录入；币种尚未解析时可留空，由确定性引擎校验。")


class PricingShippingSelection(PricingModel):
    warehouse_id: int = Field(gt=0, strict=True)
    route_id: str = Field(min_length=1, max_length=200)


class ReturnLossPatch(PricingModel):
    return_rate_percent: ReturnRate | None = Field(default=None, description="预计退货件数占发出件数的比例；0 表示不计提货损。")
    return_disposition: Literal["discard", "resell"] | None = Field(default=None, description="弃货或退回再售；再售默认完整回收采购成本。")
    return_handling_cost_cny: Amount | None = Field(default=None, description="每件退货额外损失，人民币；含退回、处理及其他不退费用，不重复填写原发货物流和包装。")


class ReturnLossSettings(PricingModel):
    return_rate_percent: ReturnRate = 0
    return_disposition: Literal["discard", "resell"] = "discard"
    return_handling_cost_cny: Amount = 0


class PricingTargetPatch(ReturnLossPatch):
    payment_fee_percent: Percent | None = None
    other_fee_percent: Percent | None = None
    pricing_mode: Literal["margin", "markup", "manual"] | None = None
    target_margin_percent: Percent | None = None
    markup_percent: Amount | None = None
    shipping_quote_mode: Literal["auto", "manual"] | None = None
    shipping_currency: Literal["CNY", "USD"] | None = None
    shipping_amount: Amount | None = Field(default=None, description="目标市场的国际运费；国内物流请填写 common.domestic_freight_cny。")
    shipping_selection: PricingShippingSelection | None = Field(default=None, description="指定 Yandex 可用发布仓库和物流渠道；显式 null 恢复全部可用方案比价。")
    manual_price: PricingMoney | None = None


class PricingParameters(PricingModel):
    common: PricingCommonPatch = Field(default_factory=PricingCommonPatch)
    targets: dict[str, PricingTargetPatch] = Field(default_factory=dict, description="以 platform:site 为键，仅填写用户要求修改的费用或利润率；省略则使用已保存配置。")


class PricingTemplateSource(PricingModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)


class DraftPricingRequest(PricingParameters):
    draft_id: str = Field(min_length=1, max_length=160)
    preview_id: str = Field(default="", max_length=80, description="应用售价时传入最近成功预览返回的标识，并保持相同核价参数；有效期十分钟。")
    expected_updated_at: str = ""
    template_source: PricingTemplateSource | None = Field(default=None, description="本次人工费用模板的来源快照，仅记录来源，不据此重新套用模板；省略保留原来源，null 清除。")
    target_keys: list[str] = Field(default_factory=list, description="留空核价全部已选市场；系统自动读取全部已勾选 SKU 的成本、尺寸与覆盖配置。")


class SkuFixedFeesPatch(PricingModel):
    domestic_freight_cny: Amount | None = None
    packaging_cost_cny: Amount | None = None
    other_cost_cny: Amount | None = None


class SkuTargetPricingPatch(ReturnLossPatch):
    shipping_amount: Amount | None = None
    manual_price: PricingMoney | None = None


class SkuPricingOverrides(PricingModel):
    common: SkuFixedFeesPatch = Field(default_factory=SkuFixedFeesPatch)
    targets: dict[str, SkuTargetPricingPatch] = Field(default_factory=dict)


class SkuPricingUpdate(PricingModel):
    sku_id: str = Field(min_length=1)
    selected: bool
    pricing_overrides: SkuPricingOverrides = Field(default_factory=SkuPricingOverrides)


class DraftPricingHttpRequest(DraftPricingRequest):
    """页面显式携带尚未保存的 SKU 编辑；AI 常规核价无需这些字段。"""
    sku_updates: list[SkuPricingUpdate] = Field(default_factory=list)
    target_selections: dict[str, list[dict[str, JsonValue]]] = Field(default_factory=dict)


class PricingMarketSummary(PricingModel):
    target_key: str
    currency: str
    min_price: float
    max_price: float
    sku_count: int


class PricingIssue(PricingModel):
    sku_id: str = ""
    target_key: str = ""
    field: str = ""
    code: str = ""
    message: str


class DraftPricingResult(PricingModel):
    draft_id: str
    preview_id: str = ""
    applied: bool
    sku_count: int
    target_count: int
    updated_at: str = ""
    markets: list[PricingMarketSummary] = Field(default_factory=list, max_length=100)
    error_count: int = 0
    errors: list[PricingIssue] = Field(default_factory=list, max_length=100)


class DraftPricingBatchPreviewRequest(PricingParameters):
    draft_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="待批量预览核价的草稿 ID 列表（最多 50 份，自动去重保序）；共享本轮汇率、仓库与平台佣金合批请求，各草稿独立选仓并生成独立 preview_id。",
    )
    expected_updated_at_by_draft: dict[str, str] = Field(
        default_factory=dict,
        description="可选，按 draft_id 传入预期版本时间；省略则不额外校验调用前版本。",
    )
    template_source: PricingTemplateSource | None = Field(
        default=None,
        description="本次人工费用模板来源快照，仅记录来源；省略保留各草稿原来源，null 清除。",
    )
    target_keys: list[str] = Field(
        default_factory=list,
        description="留空核价各草稿全部已选市场；传入则仅核价指定 platform:site 市场。",
    )


class DraftPricingBatchApplyItem(PricingModel):
    draft_id: str = Field(min_length=1, max_length=160)
    preview_id: str = Field(min_length=1, max_length=80, description="最近一次预览返回的该草稿 preview_id。")
    expected_updated_at: str = ""


class DraftPricingBatchApplyRequest(PricingParameters):
    items: list[DraftPricingBatchApplyItem] = Field(
        min_length=1,
        max_length=50,
        description="待批量应用核价结果的草稿与 preview_id 列表（最多 50 份，不得含重复 draft_id）；须与预览使用相同核价参数。",
    )
    template_source: PricingTemplateSource | None = Field(
        default=None,
        description="须与预览时一致的模板来源快照；省略保留各草稿原来源，null 清除。",
    )
    target_keys: list[str] = Field(
        default_factory=list,
        description="须与预览时一致的目标市场列表；留空应用各草稿全部已选市场。",
    )


class DraftPricingBatchItemResult(PricingModel):
    draft_id: str
    ok: bool
    preview_id: str = ""
    applied: bool = False
    sku_count: int = 0
    target_count: int = 0
    updated_at: str = ""
    markets: list[PricingMarketSummary] = Field(default_factory=list, max_length=100)
    error_code: str = ""
    error: str = ""
    error_count: int = 0
    errors: list[PricingIssue] = Field(default_factory=list, max_length=100)


class DraftPricingBatchResult(PricingModel):
    total: int
    succeeded_count: int
    failed_count: int
    applied: bool
    items: list[DraftPricingBatchItemResult] = Field(default_factory=list, max_length=50)


__all__ = [
    "DraftPricingRequest",
    "DraftPricingHttpRequest",
    "DraftPricingResult",
    "DraftPricingBatchPreviewRequest",
    "DraftPricingBatchApplyItem",
    "DraftPricingBatchApplyRequest",
    "DraftPricingBatchItemResult",
    "DraftPricingBatchResult",
    "PricingParameters",
]
