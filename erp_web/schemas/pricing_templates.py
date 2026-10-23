"""可复用的手动核价参数；不接收商品事实、官方佣金或自动报价。"""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .draft_pricing import Amount, ExchangeRateOverrides, Percent, PricingModel, PricingMoney, PricingShippingSelection, ReturnLossSettings


TemplateName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class PricingTemplateCommon(PricingModel):
    domestic_freight_cny: Amount = 0
    packaging_cost_cny: Amount = 0
    other_cost_cny: Amount = 0
    exchange_rate_overrides: ExchangeRateOverrides = Field(default_factory=ExchangeRateOverrides)


class PricingTemplateTarget(ReturnLossSettings):
    payment_fee_percent: Percent = 0
    other_fee_percent: Percent = 0
    pricing_mode: Literal["margin", "markup", "manual"] = "margin"
    target_margin_percent: Percent = 30
    markup_percent: Amount = 30
    shipping_quote_mode: Literal["auto", "manual"] = "auto"
    shipping_currency: Literal["CNY", "USD"] = "CNY"
    shipping_amount: Amount = 0
    shipping_selection: PricingShippingSelection | None = None
    manual_price: PricingMoney | None = None

    @model_validator(mode="after")
    def validate_rules(self):
        fees = self.payment_fee_percent + self.other_fee_percent
        if fees >= 100 or (self.pricing_mode == "margin" and fees + self.target_margin_percent >= 100):
            raise ValueError("平台费用与目标利润率之和必须小于 100%")
        if self.pricing_mode == "manual" and (self.manual_price is None or self.manual_price.amount <= 0):
            raise ValueError("手动售价必须大于 0")
        if self.shipping_quote_mode == "manual" and self.shipping_amount <= 0:
            raise ValueError("手动国际运费必须大于 0")
        return self


class PricingTemplateParameters(PricingModel):
    common: PricingTemplateCommon = Field(default_factory=PricingTemplateCommon)
    targets: dict[str, PricingTemplateTarget] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def validate_targets(self):
        for key in self.targets:
            if ":" not in key or key != key.lower() or key.split(":", 1)[0] not in {"yandex", "ozon", "mercadolibre"}:
                raise ValueError("模板市场键必须使用当前平台的 platform:site")
        return self


class SavePricingTemplate(PricingModel):
    id: str = Field(default="", max_length=80)
    name: TemplateName
    parameters: PricingTemplateParameters
    set_default: bool = False


class PricingTemplate(PricingModel):
    id: str = Field(min_length=1, max_length=80)
    name: TemplateName
    parameters: PricingTemplateParameters
    updated_at: str


class PricingTemplateSettings(PricingModel):
    default_template_id: str = ""
    items: list[PricingTemplate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_default(self):
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("核价模板 ID 不得重复")
        if self.default_template_id and self.default_template_id not in ids:
            raise ValueError("默认核价模板不存在")
        return self


__all__ = ["PricingTemplateSettings", "SavePricingTemplate"]
