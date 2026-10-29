"""以整批订单收支验证货损分摊、分价利润目标与输入边界。"""
from fractions import Fraction
from copy import deepcopy

import pytest
from pydantic import ValidationError

from erp_web.schemas.draft_pricing import DraftPricingRequest
from erp_web.schemas.pricing_templates import PricingTemplateTarget
from erp_web.services.pricing_service import calculate_target_pricing
from tests.pricing_test_utils import official_commission


def calculate(platform="yandex", mode="margin", disposition="discard", rate=10, currency="CNY", **extra):
    common = {"cost_cny": 100, "freight_cny": 5, "prep_fee_cny": 2, "other_cost_cny": 13,
              "usd_cny_rate": 7, "rub_cny_rate": 12}
    target = {"platform": platform, "site": "global", "listing_currency": currency,
              "shipping_quote_mode": "manual", "shipping_currency": "USD", "shipping_amount": 30 / 7,
              "pricing_mode": mode, "target_margin_percent": 30, "markup_percent": 30,
              "payment_fee_percent": 2, "other_fee_percent": 1,
              "manual_price": {"amount": 400, "currency": currency},
              "return_rate_percent": rate, "return_disposition": disposition, "return_handling_cost_cny": 40, **extra}
    # 以可精确表示的原币报价验证汇率，国际运费为 28 元。
    target["shipping_amount"] = 4
    return calculate_target_pricing(common, target, commission_resolver=official_commission)


@pytest.mark.parametrize("platform", ["yandex"])
@pytest.mark.parametrize("mode", ["margin", "markup", "manual"])
@pytest.mark.parametrize("disposition", ["discard", "resell"])
@pytest.mark.parametrize("currency,fx", [("CNY", 1), ("USD", Fraction(1, 7)), ("RUB", 12)])
def test_order_cohort_cash_flow_matches_pricing(platform, mode, disposition, currency, fx):
    result = calculate(platform, mode, disposition, currency=currency)
    assert result["ok"], result["errors"]
    price = Fraction(result["applied_price"]["amount"])
    revenue = price / fx
    # 100 件发出、10 件退货、90 件成交。其他固定成本 13 元不自动重复计入退货。
    single_loss = 5 + 2 + 28 + 40 + (100 if disposition == "discard" else 0)
    normal_cost = 100 + 5 + 2 + 28 + 13
    cohort_profit = 90 * (revenue * Fraction(77, 100) - normal_cost) - 10 * single_loss
    profit = cohort_profit / 90
    total_cost = Fraction(normal_cost) + Fraction(10 * single_loss, 90)
    assert result["return_loss"]["single_loss_cny"] == single_loss
    assert result["return_loss_cny"] == round(single_loss / 9, 2)
    assert result["profit_cny"] == round(float(profit), 2)
    if mode == "margin":
        assert profit >= revenue * Fraction(3, 10)
        assert profit - revenue * Fraction(3, 10) < Fraction(1, 100) / fx
    elif mode == "markup":
        assert profit >= total_cost * Fraction(3, 10)
        assert profit - total_cost * Fraction(3, 10) < Fraction(1, 100) / fx
    else:
        assert price == 400
    assert result["calculation_basis"]["commission_evidence"]["price"] == result["applied_price"]


def test_zero_rate_does_not_charge_saved_handling_cost():
    result = calculate(rate=0)
    assert result["return_loss_cny"] == 0
    assert result["total_cost_cny"] == 148
    assert result["return_loss"]["single_loss_cny"] == 0


@pytest.mark.parametrize("rate", [0.01, 33.333, 99.99])
def test_fractional_and_high_rates_retain_profit_goal(rate):
    result = calculate(rate=rate)
    assert result["ok"], result["errors"]
    price = Fraction(result["applied_price"]["amount"])
    r = Fraction(str(rate)) / 100
    exact_cost = 148 + r * 175 / (1 - r)
    assert price * Fraction(47, 100) >= exact_cost


@pytest.mark.parametrize("field,value", [("return_rate_percent", 100), ("return_rate_percent", -1),
    ("return_rate_percent", float("nan")), ("return_rate_percent", float("inf")),
    ("return_handling_cost_cny", -1), ("return_handling_cost_cny", float("inf")),
    ("return_disposition", "unknown")])
def test_invalid_return_inputs_rejected_by_contract_and_engine(field, value):
    with pytest.raises(ValidationError):
        DraftPricingRequest(draft_id="draft", targets={"ozon:global": {field: value}})
    with pytest.raises(ValidationError):
        PricingTemplateTarget(**{field: value})
    result = calculate(**{field: value})
    assert not result["ok"]
    assert field in [error["field"] for error in result["errors"]]


def test_fingerprint_covers_assumptions_and_inputs_are_unchanged():
    target = {"platform": "yandex", "site": "global", "listing_currency": "CNY", "shipping_quote_mode": "manual",
              "shipping_currency": "CNY", "shipping_amount": 30, "return_rate_percent": 10}
    common = {"cost_cny": 100, "freight_cny": 5, "prep_fee_cny": 2}
    before = deepcopy(target)
    first = calculate_target_pricing(common, target, commission_resolver=official_commission)
    assert target == before
    assert first["return_loss"]["single_loss_cny"] == 137
    second = calculate_target_pricing(common, {**target, "return_disposition": "resell", "return_handling_cost_cny": 40}, commission_resolver=official_commission)
    assert second["return_loss"]["single_loss_cny"] == 77
    assert first["calculation_fingerprint"] != second["calculation_fingerprint"]
