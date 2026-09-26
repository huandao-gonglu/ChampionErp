"""草稿核价契约：只提交本次修改，采购成本和包装资料由系统读取。"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class PricingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PricingCommonPatch(PricingModel):
    domestic_freight_cny: Amount | None = Field(default=None, description="国内物流费用，人民币；与国际运费独立。")
    packaging_cost_cny: Amount | None = None
    other_cost_cny: Amount | None = None
    battery: bool | None = None
    liquid: bool | None = None
    exchange_rate_mode: Literal["live", "manual"] | None = None
    usd_cny_rate: Rate | None = None
    mxn_usd_rate: Rate | None = None
    rub_cny_rate: Rate | None = None


class PricingMoney(PricingModel):
    amount: Rate
    currency: str = Field(max_length=3, description="按页面已显示的发布币种录入；币种尚未解析时可留空，由确定性引擎校验。")


class PricingTargetPatch(PricingModel):
    commission_percent: Percent | None = None
    payment_fee_percent: Percent | None = None
    other_fee_percent: Percent | None = None
    pricing_mode: Literal["margin", "markup", "manual"] | None = None
    target_margin_percent: Percent | None = None
    markup_percent: Amount | None = None
    shipping_quote_mode: Literal["auto", "manual"] | None = None
    shipping_currency: Literal["CNY", "USD"] | None = None
    shipping_amount: Amount | None = Field(default=None, description="目标市场的国际运费；国内物流请填写 common.domestic_freight_cny。")
    manual_price: PricingMoney | None = None


class PricingParameters(PricingModel):
    common: PricingCommonPatch = Field(default_factory=PricingCommonPatch)
    targets: dict[str, PricingTargetPatch] = Field(default_factory=dict, description="以 platform:site 为键，仅填写用户要求修改的费用或利润率；省略则使用已保存配置。")


class DraftPricingRequest(PricingParameters):
    draft_id: str = Field(min_length=1, max_length=160)
    expected_updated_at: str = ""
    target_keys: list[str] = Field(default_factory=list, description="留空核价全部已选市场；系统自动读取全部已勾选 SKU 的成本、尺寸与覆盖配置。")


class SkuPackagePatch(PricingModel):
    length_cm: str | float | None = None
    width_cm: str | float | None = None
    height_cm: str | float | None = None
    weight_kg: str | float | None = None


class SkuFactsPatch(PricingModel):
    cost_cny: str | float | None = None
    package_dimensions: SkuPackagePatch | None = None


class SkuFixedFeesPatch(PricingModel):
    domestic_freight_cny: Amount | None = None
    packaging_cost_cny: Amount | None = None
    other_cost_cny: Amount | None = None


class SkuTargetPricingPatch(PricingModel):
    shipping_amount: Amount | None = None
    manual_price: PricingMoney | None = None


class SkuPricingOverrides(PricingModel):
    common: SkuFixedFeesPatch = Field(default_factory=SkuFixedFeesPatch)
    targets: dict[str, SkuTargetPricingPatch] = Field(default_factory=dict)


class SkuPricingUpdate(PricingModel):
    sku_id: str = Field(min_length=1)
    selected: bool
    overrides: SkuFactsPatch = Field(default_factory=SkuFactsPatch)
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
    applied: bool
    sku_count: int
    target_count: int
    updated_at: str = ""
    markets: list[PricingMarketSummary] = Field(default_factory=list, max_length=100)
    error_count: int = 0
    errors: list[PricingIssue] = Field(default_factory=list, max_length=100)


__all__ = ["DraftPricingRequest", "DraftPricingHttpRequest", "DraftPricingResult", "PricingParameters"]
