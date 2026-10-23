"""官方佣金的协议、价格校准、失败阻断与旧报价失效回归。"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.draft_pricing import PricingTargetPatch
from erp_web.services import pricing_commissions as api
from erp_web.services import pricing_service as pricing
from tests.pricing_test_utils import official_commission


COMMON = {"cost_cny": 35, "weight_kg": .3, "length_cm": 20, "width_cm": 10, "height_cm": 5,
          "usd_cny_rate": 7, "mxn_usd_rate": 20, "rub_cny_rate": 12}
TARGET = {"platform": "yandex", "site": "global", "category_id": "123", "listing_currency": "CNY",
          "shipping_quote_mode": "manual", "shipping_currency": "CNY", "shipping_amount": 7,
          "target_margin_percent": 30}
CONFIG = {"yandex": {"api_token": "secret", "campaign_id": "987"},
          "ozon": {"client_id": "42", "api_key": "secret"},
          "mercadolibre": {"access_token": "secret", "user_id": "321"}}


def yandex_response(payload, amount=20, currency="CNY"):
    return {"status": "OK", "result": {"offers": [{"offer": deepcopy(payload["offers"][0]), "tariffs": [
        {"type": "FEE", "amount": amount, "currency": currency, "parameters": []},
        {"type": "DELIVERY_TO_CUSTOMER", "amount": 500, "currency": currency},
    ]}]}}


@pytest.mark.parametrize("currency,receipt_currency", [("CNY", "CNY"), ("RUB", "RUR")])
def test_yandex_uses_campaign_currency_without_conflicting_parameters(monkeypatch, currency, receipt_currency):
    calls = []
    def request(method, endpoint, token, payload):
        calls.append((method, endpoint, token, payload))
        return yandex_response(payload, 10, receipt_currency)
    monkeypatch.setattr(api, "request_yandex_json", request)
    fees = api.PricingCommissions(CONFIG)
    quote = fees(COMMON, {**TARGET, "listing_currency": currency}, {"amount": "50", "currency": currency})
    payload = calls[0][3]
    assert payload["parameters"] == {"campaignId": 987}
    assert payload["offers"][0]["price"] == 50
    assert Decimal(quote["percent"]) == 20
    assert Decimal(quote["evidence"]["amount"]) == 10
    assert "secret" not in str(quote)
    assert payload["offers"][0]["weight"] == .3
    fees(COMMON, {**TARGET, "listing_currency": currency}, {"amount": "50", "currency": currency})
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_offer", "wrong_currency", "nan", "negative"])
def test_yandex_rejects_ambiguous_or_invalid_receipts(monkeypatch, change):
    def request(_method, _endpoint, _token, payload):
        body = yandex_response(payload)
        offer = body["result"]["offers"][0]
        if change == "missing": offer["tariffs"] = []
        elif change == "duplicate": offer["tariffs"] *= 2
        elif change == "wrong_offer": offer["offer"]["categoryId"] = 999
        elif change == "wrong_currency": offer["tariffs"][0]["currency"] = "USD"
        elif change == "nan": offer["tariffs"][0]["amount"] = "NaN"
        elif change == "negative": offer["tariffs"][0]["amount"] = -1
        return body
    monkeypatch.setattr(api, "request_yandex_json", request)
    with pytest.raises(ValueError):
        api.PricingCommissions(CONFIG)(COMMON, TARGET, {"amount": "50", "currency": "CNY"})


def test_authorization_failure_is_shared_within_batch_and_never_leaks_token(monkeypatch):
    calls = []
    def request(*args):
        calls.append(args)
        raise PublishAdapterError("AUTH_FAILED", "secret", retryable=False)
    monkeypatch.setattr(api, "request_yandex_json", request)
    fees = api.PricingCommissions(CONFIG)
    for amount in (40, 50, 60):
        with pytest.raises(ValueError, match="AUTH_FAILED") as caught:
            fees(COMMON, TARGET, {"amount": str(amount), "currency": "CNY"})
        assert "secret" not in str(caught.value)
    assert len(calls) == 1


def test_parameter_error_keeps_reason_without_leaking_token_or_stopping_other_requests(monkeypatch):
    from erp_web.marketplaces.yandex_http import YandexApiError

    calls = []
    def request(_method, _endpoint, _token, payload):
        calls.append(payload)
        if payload["offers"][0]["categoryId"] == 123:
            raise YandexApiError("YANDEX_HTTP_FAILED", 'Forbidden to use "campaignId" with "currency"; secret', http_status=400)
        return yandex_response(payload, 10)
    monkeypatch.setattr(api, "request_yandex_json", request)
    fees = api.PricingCommissions(CONFIG)
    for _ in range(2):
        with pytest.raises(ValueError, match='Forbidden to use "campaignId" with "currency"') as caught:
            fees(COMMON, TARGET, {"amount": "50", "currency": "CNY"})
        assert "secret" not in str(caught.value)
    quote = fees(COMMON, {**TARGET, "category_id": "456"}, {"amount": "50", "currency": "CNY"})
    assert Decimal(quote["percent"]) == 20
    assert len(calls) == 2


def test_mercado_includes_logistics_and_does_not_add_fixed_fee_twice(monkeypatch):
    calls = []
    def request(method, url, token):
        calls.append((method, url, token))
        return {"listing_type_id": "gold_pro", "currency_id": "MXN", "sale_fee_amount": 25,
                "sale_fee_details": {"percentage_fee": 20, "fixed_fee": 5}}
    monkeypatch.setattr(api, "request_json", request)
    target = {**TARGET, "platform": "mercadolibre", "site": "MLM", "category_id": "MLM123",
              "listing_type_id": "gold_pro", "logistic_type": "drop_off"}
    quote = api.PricingCommissions(CONFIG)(COMMON, target, {"amount": "100", "currency": "MXN"})
    assert Decimal(quote["percent"]) == 25
    query = parse_qs(urlsplit(calls[0][1]).query)
    assert query["category_id"] == ["MLM123"]
    assert query["logistic_type"] == ["drop_off"]
    assert query["billable_weight"] == ["300.0"]


def test_cbt_category_uses_official_cbt_page_without_fabricated_regional_category(monkeypatch):
    def category_request(method, url, token):
        assert method == "GET" and url == "https://api.mercadolibre.com/categories/CBT123"
        return {"id": "CBT123", "children_categories": [], "settings": {"catalog_domain": "CBT-TEST"}}
    monkeypatch.setattr(api, "request_json", category_request)
    config = deepcopy(CONFIG)
    config["mercadolibre"]["marketplace_bindings"] = [{"site_id": "MLM", "logistic_type": "remote", "seller_id": "333"}]
    target = {**TARGET, "platform": "mercadolibre", "site": "CBT", "category_id": "CBT123",
              "sites_to_sell": [{"site_id": "MLM", "logistic_type": "remote", "listing_type_id": "gold_pro"}]}
    fees = api.PricingCommissions(config)
    calls = []
    def query(category, domain, site, logistic, listing_type, amount, convert):
        assert domain == "CBT-TEST"
        calls.append((category, site, logistic, listing_type, amount))
        return Decimal(10), {"source": "official_fee_page", "endpoint": "https://global-selling.mercadolibre.com/landing/api/categories/price/CBT123/CBT-TEST?sites=MLM"}
    monkeypatch.setattr(fees.mercado_tariffs, "quote", query)
    result = fees(COMMON, target, {"amount": "50", "currency": "USD"})
    assert calls == [("CBT123", "MLM", "remote", "gold_pro", Decimal(50))]
    assert Decimal(result["percent"]) == 20


def test_ozon_never_uses_existing_product_commission_for_prelisting(monkeypatch):
    monkeypatch.setattr(api, "request_json", lambda *_: pytest.fail("新品核价不得借用已上架商品佣金"))
    target = {**TARGET, "platform": "ozon"}
    with pytest.raises(ValueError, match="上架前"):
        api.PricingCommissions(CONFIG)(COMMON, target, {"amount": "50", "currency": "CNY"})


def test_official_rate_ignores_legacy_inputs_and_checks_final_price():
    calls = []
    def query(common, target, price):
        calls.append(dict(price))
        return official_commission(common, {**target, "commission_percent": 25}, price)
    result = pricing.calculate_target_pricing({**COMMON, "commission_rate": 1}, {**TARGET, "commission_percent": 1}, commission_resolver=query)
    assert result["ok"]
    assert result["commission_percent"] == 25
    assert result["applied_price"] == calls[-1]
    assert pricing.has_official_commission(result["calculation_basis"])


def test_fixed_charge_is_included_and_final_quote_matches_published_price():
    def query(common, target, price):
        quote = official_commission(common, target, price)
        amount = Decimal(price["amount"])
        fee = amount * Decimal("0.1") + 5
        quote["percent"] = str(fee / amount * 100)
        quote["evidence"]["amount"] = str(fee)
        return quote
    result = pricing.calculate_target_pricing(COMMON, TARGET, commission_resolver=query)
    assert result["ok"]
    evidence = result["calculation_basis"]["commission_evidence"]
    price = Decimal(result["applied_price"]["amount"])
    # 按真正发布的分价核验，不用显示时已舍入的百分比判断是否达标。
    assert 0 <= price * Decimal(".7") - Decimal(evidence["amount"]) - Decimal("42") <= Decimal(".01")
    assert Decimal(result["applied_price"]["amount"]) == Decimal(evidence["price"]["amount"])


@pytest.mark.parametrize("mode", ["margin", "markup"])
def test_cent_rounded_official_fee_settles_with_actual_published_price(mode):
    calls = []
    def query(common, target, price):
        calls.append(dict(price))
        amount = Decimal(price["amount"])
        fee = (amount * Decimal("0.27")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        quote = official_commission(common, target, price)
        quote["percent"] = str(fee / amount * 100)
        quote["evidence"]["amount"] = str(fee)
        return quote
    result = pricing.calculate_target_pricing({**COMMON, "cost_cny": 14.5}, {**TARGET, "pricing_mode": mode}, commission_resolver=query)
    assert result["ok"]
    assert len(calls) <= 4
    basis = result["calculation_basis"]
    assert pricing.has_official_commission(basis)
    price = Decimal(result["applied_price"]["amount"])
    fee = Decimal(basis["commission_evidence"]["amount"])
    assert basis["commission_evidence"]["price"] == result["applied_price"]
    assert fee == (price * Decimal("0.27")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert Decimal(str(result["commission_cny"])) == fee
    cost = Decimal(str(result["total_cost_cny"]))
    required_profit = price * Decimal("0.3") if mode == "margin" else cost * Decimal("0.3")
    assert 0 <= price - fee - cost - required_profit <= Decimal("0.01")


def test_missing_or_nonconverging_official_quote_never_exposes_applicable_price():
    result = pricing.calculate_target_pricing(COMMON, {**TARGET, "commission_percent": 20})
    assert not result["ok"] and result["applied_price"]["amount"] == "0.00"
    assert result["destination_results"] == []
    def oscillate(common, target, price):
        percent = 0 if Decimal(price["amount"]) > 80 else 25
        return official_commission(common, {**target, "commission_percent": percent}, price)
    result = pricing.calculate_target_pricing(COMMON, TARGET, commission_resolver=oscillate)
    assert not result["ok"] and result["applied_price"]["amount"] == "0.00"
    assert "反复变化" in result["errors"][0]["message"]


def test_manual_price_still_requires_official_fee_and_zero_fee_is_valid():
    target = {**TARGET, "pricing_mode": "manual", "manual_price": {"amount": "50", "currency": "CNY"}}
    assert not pricing.calculate_target_pricing(COMMON, target)["ok"]
    result = pricing.calculate_target_pricing(COMMON, target, commission_resolver=lambda c, t, p: official_commission(c, {**t, "commission_percent": 0}, p))
    assert result["ok"] and result["commission_cny"] == 0
    assert pricing.has_official_commission(result["calculation_basis"])


def test_manual_shipping_still_queries_each_cbt_country_independently():
    countries = []
    def query(common, target, price):
        country = target["sites_to_sell"][0]["site_id"]
        countries.append(country)
        return official_commission(common, {**target, "commission_percent": 10 if country == "MLM" else 25}, price)
    target = {**TARGET, "platform": "mercadolibre", "site": "CBT", "listing_currency": "USD",
              "sites_to_sell": [{"site_id": site, "logistic_type": "remote"} for site in ("MLM", "MLC")],
              "destination_pricing_modes": [{"site_id": site, "logistic_type": "remote", "pricing_model": "price"} for site in ("MLM", "MLC")]}
    result = pricing.pricing_result({"common": COMMON, "targets": [target]}, commission_resolver=query)["results"][0]
    assert result["ok"] and set(countries) == {"MLM", "MLC"}
    assert {row["commission_percent"] for row in result["destination_results"]} == {10, 25}
    assert pricing.has_official_commission(result["calculation_basis"])


def test_manual_commission_is_removed_from_request_contract():
    with pytest.raises(ValidationError): PricingTargetPatch(commission_percent=20)


def test_existing_product_commission_is_never_valid_prelisting_evidence():
    quote = pricing.calculate_target_pricing(COMMON, TARGET, commission_resolver=official_commission)
    basis = deepcopy(quote["calculation_basis"])
    basis["commission_evidence"]["endpoint"] = "/v5/product/info/prices"
    assert not pricing.has_official_commission(basis)
