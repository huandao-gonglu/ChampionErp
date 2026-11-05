"""真实核价入口的 Yandex 批量、规则求解、最终回执与失败隔离回归。"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

import pytest

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.context import get_context
from erp_web.runtime_units import pricing_runtime
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.services import pricing_commissions
from erp_web.services.pricing_service import has_official_commission
from erp_web.services.yandex_commission_batch import parse_rule
from tests.runtime_test_utils import seed_store_currency


def tariff(price, *, rate="27", kind="relative", parameters=True, minimum=None, maximum=None, **bounds):
    value = Decimal(rate)
    fee = price * value / 100 if kind == "relative" else value
    if minimum is not None:
        fee = max(fee, Decimal(minimum))
    if maximum is not None:
        fee = min(fee, Decimal(maximum))
    values = {"value": rate, "valueType": kind, "billingUnit": "item", "priceDependence": "NOT_DEPENDED", "priceFrom": "0", **bounds}
    if minimum is not None:
        values["minValue"] = minimum
    if maximum is not None:
        values["maxValue"] = maximum
    return {"type": "FEE", "amount": str(fee.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)),
            "currency": "CNY", "parameters": [{"name": k, "value": v} for k, v in values.items()] if parameters else []}


def batch(count=9, *, costs=None, manual=False):
    return {"items": [{
        "sku_id": f"规格-{i}",
        "input": {
            "exchange_rate_mode": "manual",
            "common": {"cost_cny": costs[i] if costs else 100 + i, "weight_kg": 1 + i / 100,
                       "length_cm": 20, "width_cm": 10, "height_cm": 5,
                       "usd_cny_rate": 7, "mxn_usd_rate": 20, "rub_cny_rate": 12},
            "targets": [{"platform": "yandex", "site": "global", "category_id": "123",
                         "shipping_quote_mode": "auto", "target_margin_percent": 30,
                         "pricing_mode": "manual" if manual else "margin",
                         **({"manual_price": {"amount": "300", "currency": "CNY"}} if manual else {})}],
        },
    } for i in range(count)]}


@pytest.fixture
def runtime(monkeypatch):
    seed_store_currency("yandex", "CNY", identity={"campaign_id": "987", "api_token": "test-secret"})
    config = get_context().config.load_store_config()
    config["yandex"]["campaign_id"] = "987"
    monkeypatch.setattr(get_context().config, "load_store_config", lambda: deepcopy(config))

    class Shipping:
        yandex_plans = [{'fingerprint': 'plan', 'publish_warehouse_id': 31}]
        module = SimpleNamespace(ozon_discovery_ms=0)

        def __call__(self, common, target):
            return {"amount": "7", "currency": "CNY", "minimum_price_cny": "0",
                    "billable_g": "1000", "evidence": {"source": "tariff_template",
                    "fulfillment_plan": {"fingerprint": "plan", "publish_warehouse_id": 31}}, "candidates": [{"amount_cny": "7", "fulfillment_plan": self.yandex_plans[0]}]}

    monkeypatch.setattr(pricing_runtime, "PricingShipping", lambda *_args, **_kwargs: Shipping())
    calls = []

    def install(fee=tariff, change=None):
        def request(method, endpoint, token, payload):
            assert method == "POST" and endpoint == "/v2/tariffs/calculate" and token == "test-secret"
            assert payload["parameters"] == {"campaignId": 987}
            calls.append(deepcopy(payload))
            response = {"result": {"offers": [
                {"offer": deepcopy(offer), "tariffs": [fee(Decimal(str(offer["price"])))]}
                for offer in payload["offers"]
            ]}}
            if change:
                change(response, len(calls))
            return response
        monkeypatch.setattr(pricing_commissions, "request_yandex_json", request)
        return calls
    return install


def assert_verified(result):
    assert result["metrics"]["failed_sku_count"] == 0
    for item in result["items"]:
        quote = item["result"]["results"][0]
        evidence = quote["calculation_basis"]["commission_evidence"]
        assert evidence["source"] == "platform_api"
        assert evidence["price"] == quote["applied_price"]
        assert Decimal(evidence["amount"]) == Decimal(str(quote["commission_cny"]))
        assert has_official_commission(quote["calculation_basis"])
        assert quote["margin_percent"] >= 30


def test_nine_skus_fetch_rules_then_verify_prices_in_two_requests(runtime):
    calls = runtime()
    source = batch()
    before = deepcopy(source)
    result = calculate_sku_prices(source)
    assert [len(call["offers"]) for call in calls] == [9, 9]
    assert source == before
    assert_verified(result)
    final = [item["result"]["results"][0]["applied_price"]["amount"] for item in result["items"]]
    assert [Decimal(str(offer["price"])) for offer in calls[-1]["offers"]] == list(map(Decimal, final))


def test_more_than_200_quotes_are_chunked_without_cross_sku_fee_reuse(runtime):
    calls = runtime()
    result = calculate_sku_prices(batch(201))
    assert [len(call["offers"]) for call in calls] == [200, 1, 200, 1]
    assert_verified(result)


def test_only_cross_tier_sku_needs_an_extra_batch(runtime):
    def tiered(price):
        return tariff(price, rate="10" if price < 200 else "27", priceDependence="DEPENDED",
                      priceFrom="0" if price < 200 else "200", priceTo="200" if price < 200 else "10000")
    calls = runtime(tiered)
    result = calculate_sku_prices(batch(2, costs=[100, 130]))
    assert [len(call["offers"]) for call in calls] == [2, 2, 1]
    assert_verified(result)


@pytest.mark.parametrize("options", [
    {"kind": "absolute", "rate": "30"},
    {"kind": "absolute", "rate": "200"},
    {"minimum": "80"},
    {"minimum": "200"},
    {"maximum": "20"},
    {"rate": "90", "maximum": "200"},
])
def test_fixed_minimum_and_maximum_fees_are_solved_locally(runtime, options):
    calls = runtime(lambda price: tariff(price, **options))
    result = calculate_sku_prices(batch())
    assert [len(call["offers"]) for call in calls] == [9, 9]
    assert_verified(result)


def test_rounded_margin_display_cannot_hide_an_actual_shortfall(runtime):
    calls = runtime()
    result = calculate_sku_prices(batch(1, costs=[93.09]))
    assert [len(call["offers"]) for call in calls] == [1, 1]
    quote = result["items"][0]["result"]["results"][0]
    assert quote["applied_price"]["amount"] == "232.78"
    assert quote["margin_percent"] == 30
    price = Decimal(quote["applied_price"]["amount"])
    fee = Decimal(quote["calculation_basis"]["commission_evidence"]["amount"])
    assert price - fee - Decimal("100.09") >= price * Decimal(".3")


@pytest.mark.parametrize("mode", ["margin", "markup", "manual"])
def test_all_modes_preserve_exact_final_profit_and_batch_counts(runtime, mode):
    calls = runtime(lambda price: tariff(price, minimum="80"))
    source = batch(manual=mode == "manual")
    for item in source["items"]:
        item["input"]["targets"][0].update(pricing_mode=mode, markup_percent=30, payment_fee_percent=2.5, other_fee_percent=1.75)
    for expected_calls in (1 if mode == "manual" else 2, 1):
        calls.clear()
        result = calculate_sku_prices(source)
        assert len(calls) == expected_calls
        for i, item in enumerate(result["items"]):
            quote = item["result"]["results"][0]
            assert quote["ok"]
            price = Decimal(quote["applied_price"]["amount"])
            fee = Decimal(quote["calculation_basis"]["commission_evidence"]["amount"])
            profit = price - fee - price * Decimal(".0425") - Decimal(107 + i)
            assert Decimal(str(quote["profit_cny"])) == profit.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if mode == "margin":
                assert profit >= price * Decimal(".3")
            elif mode == "markup":
                assert profit >= Decimal(107 + i) * Decimal(".3")
            else:
                assert price == 300


def test_missing_rules_still_batches_exact_price_queries(runtime):
    calls = runtime(lambda price: tariff(price, parameters=False))
    result = calculate_sku_prices(batch())
    assert 2 <= len(calls) <= 4
    assert all(len(call["offers"]) <= 9 for call in calls)
    assert_verified(result)


def test_manual_prices_need_one_batch_and_keep_user_prices(runtime):
    calls = runtime()
    result = calculate_sku_prices(batch(manual=True))
    assert len(calls) == 1
    assert_verified(result)
    assert {item["result"]["results"][0]["applied_price"]["amount"] for item in result["items"]} == {"300.00"}


def test_zero_fee_is_verified_once(runtime):
    calls = runtime(lambda price: tariff(price, rate="0"))
    result = calculate_sku_prices(batch())
    assert len(calls) == 1
    assert_verified(result)


def test_nonconverging_prices_stop_at_batch_budget_without_exposing_candidates(runtime):
    def moving_fee(price):
        receipt = tariff(price, parameters=False)
        # 让每次反算的目标售价比这次查询高一元，验证请求次数存在硬上限。
        receipt["amount"] = str(price * (Decimal(".7") - Decimal("107") / (price + 1)))
        return receipt
    calls = runtime(moving_fee)
    result = calculate_sku_prices(batch(1))
    assert len(calls) == 16
    assert result["metrics"]["failed_sku_count"] == 1
    quote = result["items"][0]["result"]["results"][0]
    assert quote["applied_price"]["amount"] == "0.00"
    assert quote["destination_results"] == []
    assert "未收敛" in str(quote["errors"])


def test_duplicate_inputs_share_exact_receipt_but_new_runs_fetch_again(runtime):
    calls = runtime()
    source = batch(2)
    source["items"][1]["input"] = deepcopy(source["items"][0]["input"])
    for _ in range(2):
        assert_verified(calculate_sku_prices(source))
    assert [len(call["offers"]) for call in calls] == [1, 1, 1]


@pytest.mark.parametrize("change", ["order", "count", "currency", "fee"])
def test_invalid_final_receipts_never_expose_local_estimates(runtime, change):
    def corrupt(body, call):
        if call != 2:
            return
        rows = body["result"]["offers"]
        if change == "order":
            rows.reverse()
        elif change == "count":
            rows.pop()
        elif change == "currency":
            rows[0]["tariffs"][0]["currency"] = "USD"
        else:
            rows[0]["tariffs"][0]["amount"] = "NaN"
    runtime(change=corrupt)
    result = calculate_sku_prices(batch())
    failures = [item for item in result["items"] if not item["result"]["ok"]]
    assert len(failures) == (9 if change in {"order", "count"} else 1)
    for item in failures:
        assert item["result"]["results"][0]["applied_price"]["amount"] == "0.00"
    for item in result["items"]:
        if item["result"]["ok"]:
            assert item["result"]["results"][0]["calculation_basis"]["commission_evidence"]["source"] == "platform_api"


def test_final_network_failure_is_not_retried_for_every_sku(runtime):
    def fail(_body, call):
        if call == 2:
            raise PublishAdapterError("YANDEX_NETWORK_TIMEOUT", "test-secret", retryable=True)
    calls = runtime(change=fail)
    result = calculate_sku_prices(batch())
    assert len(calls) == 2
    assert result["metrics"]["failed_sku_count"] == 9
    assert "test-secret" not in str(result)
    assert all(item["result"]["results"][0]["applied_price"]["amount"] == "0.00" for item in result["items"])


@pytest.mark.parametrize("change", ["missing", "unknown", "duplicate", "mismatch", "boundary"])
def test_incomplete_or_unproven_rules_are_not_used_locally(change):
    row = tariff(Decimal("100"))
    if change == "missing":
        row["parameters"] = []
    elif change == "unknown":
        row["parameters"].append({"name": "unrecognized", "value": "20"})
    elif change == "duplicate":
        row["parameters"].append({"name": "value", "value": "28"})
    elif change == "mismatch":
        row["amount"] = "28"
    else:
        row["parameters"].append({"name": "priceTo", "value": "100"})
    assert parse_rule(row, Decimal("100")) is None


def test_repeated_calculation_reuses_rules_but_always_verifies_final_prices(runtime):
    calls = runtime()
    source = batch()
    assert_verified(calculate_sku_prices(source))
    assert len(calls) == 2
    calls.clear()
    source['items'][0]['input']['common']['cost_cny'] = 130
    result = calculate_sku_prices(source)
    assert_verified(result)
    assert [len(call['offers']) for call in calls] == [9]
    assert [Decimal(str(offer['price'])) for offer in calls[0]['offers']] == [
        Decimal(item['result']['results'][0]['applied_price']['amount']) for item in result['items']]


@pytest.mark.parametrize('change', ['category', 'weight', 'account', 'expired'])
def test_rule_cache_scope_and_expiry(runtime, change):
    calls = runtime()
    source = batch(1)
    assert_verified(calculate_sku_prices(source))
    if change == 'category':
        source['items'][0]['input']['targets'][0]['category_id'] = '456'
    elif change == 'weight':
        source['items'][0]['input']['common']['weight_kg'] = 2
    elif change == 'account':
        from erp_web.services.yandex_commission_batch import YandexCommissionBatch
        cache = get_context().pricing_cache.rules
        original = {'campaign_id': '987', 'api_token': 'test-secret'}
        a = YandexCommissionBatch(original, None, rule_cache=cache)
        b = YandexCommissionBatch({**original, 'campaign_id': '988'}, None, rule_cache=cache)
        c = YandexCommissionBatch({**original, 'api_token': 'changed'}, None, rule_cache=cache)
        offer = calls[0]['offers'][0]
        assert len({item.rule_key(offer, 'CNY') for item in (a, b, c)}) == 3
        return
    else:
        cache = get_context().pricing_cache.rules
        now = cache.clock()
        cache.clock = lambda: now + 3601
    calls.clear()
    assert_verified(calculate_sku_prices(source))
    assert len(calls) == 2


def test_changed_official_fee_corrects_cached_rule_before_success(runtime):
    runtime()
    assert_verified(calculate_sku_prices(batch()))
    calls = runtime(lambda price: tariff(price, rate='30'))
    calls.clear()
    result = calculate_sku_prices(batch())
    assert_verified(result)
    assert len(calls) == 2
    assert all(item['result']['results'][0]['commission_percent'] == 30 for item in result['items'])


def test_warm_verification_failure_never_returns_cached_estimate(runtime):
    runtime()
    assert_verified(calculate_sku_prices(batch()))
    def fail(_body, _call):
        raise PublishAdapterError('YANDEX_NETWORK_TIMEOUT', '核验超时', retryable=True)
    calls = runtime(change=fail)
    calls.clear()
    result = calculate_sku_prices(batch())
    assert len(calls) == 1
    assert result['metrics']['failed_sku_count'] == 9
    assert all(item['result']['results'][0]['applied_price']['amount'] == '0.00' for item in result['items'])


@pytest.mark.parametrize("mode", ["margin", "markup", "manual"])
def test_return_loss_reuses_rules_and_keeps_final_batch_verification(runtime, mode):
    from time import perf_counter
    calls = runtime()
    source = batch(manual=mode == "manual")
    for index, item in enumerate(source["items"]):
        item["input"]["targets"][0].update(pricing_mode=mode, return_rate_percent=10,
            return_disposition="resell" if index % 2 else "discard", return_handling_cost_cny=20)
    started = perf_counter()
    cold = calculate_sku_prices(source)
    cold_ms = (perf_counter() - started) * 1000
    assert cold["metrics"]["failed_sku_count"] == 0
    assert [len(call["offers"]) for call in calls] == ([9] if mode == "manual" else [9, 9])
    calls.clear()
    started = perf_counter()
    warm = calculate_sku_prices(source)
    warm_ms = (perf_counter() - started) * 1000
    assert [len(call["offers"]) for call in calls] == [9]
    for before, after in zip(cold["items"], warm["items"]):
        old = before["result"]["results"][0]
        new = after["result"]["results"][0]
        assert old["applied_price"] == new["applied_price"]
        assert new["return_loss_cny"] > 0
        assert new["calculation_basis"]["commission_evidence"]["price"] == new["applied_price"]
    print(f"货损批量回归 {mode}: 9 SKU 首算 {cold_ms:.2f} ms，缓存重算 {warm_ms:.2f} ms（平台响应替身）")


def test_campaigns_keep_separate_fee_batches_receipts_and_rule_cache():
    from erp_web.services.yandex_commission_batch import YandexCommissionBatch, CommissionBatchPending
    calls = []
    def request(method, endpoint, token, body):
        campaign = body['parameters']['campaignId']
        calls.append(deepcopy(body))
        return {'result': {'offers': [{'offer': offer, 'tariffs': [tariff(Decimal(str(offer['price'])), rate='20' if campaign == 987 else '30')]}
                                     for offer in body['offers']]}}
    fees = YandexCommissionBatch({'api_token': 'test', 'campaign_id': '987'}, request, rule_cache=get_context().pricing_cache.rules)
    offer = {'categoryId': 123, 'price': 100, 'quantity': 1, 'weight': 1, 'length': 20, 'width': 10, 'height': 5}
    for campaign in ('987', '988', '988'):
        with pytest.raises(CommissionBatchPending):
            fees.quote(offer, 'CNY', campaign_id=campaign)
    fees.flush()
    assert [body['parameters']['campaignId'] for body in calls] == [987, 988]
    assert [len(body['offers']) for body in calls] == [1, 1]
    assert Decimal(fees.quote(offer, 'CNY', campaign_id='987')['amount']) == 20
    assert Decimal(fees.quote(offer, 'CNY', campaign_id='988')['amount']) == 30
    assert fees.rule_key(offer, 'CNY', '987') != fees.rule_key(offer, 'CNY', '988')
