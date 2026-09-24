"""批量 SKU 核价的请求数、规格隔离、失败复用与日志回归。"""
from collections import Counter
from copy import deepcopy
import logging

import pytest

from erp_web.context import get_context
from erp_web.international_shipping.platform_api import PlatformClient
from erp_web.runtime_units import pricing_runtime
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.schemas.requests import RequestValidationError
from erp_web.services.pricing_shipping import PricingShipping
from tests.runtime_test_utils import seed_store_currency
from test_international_shipping import COMMON, rules, target  # noqa: F401


@pytest.fixture
def batch_setup(rules, monkeypatch):
    seed_store_currency("ozon", "CNY", identity={"client_id": "1", "api_key": "test"})
    seed_store_currency("yandex", "RUB", mode="manual")
    monkeypatch.setattr(pricing_runtime, "PricingShipping", lambda _path, config: PricingShipping(rules, config))


def batch(count=198, *, mode="manual"):
    return {"items": [{
        "sku_id": f"sku-{index}",
        "input": {
            "exchange_rate_mode": mode,
            "common": {**COMMON, "weight_kg": str((index + 100) / 1000), "cost_cny": 20 + index / 100},
            "targets": [target(), target("yandex")],
        },
    } for index in range(count)]}


def count_queries(monkeypatch):
    counts = Counter()
    original = PlatformClient.call

    def call(client, path, **kwargs):
        counts[path] += 1
        return original(client, path, **kwargs)

    monkeypatch.setattr(PlatformClient, "call", call)
    return counts


def test_198_skus_share_channels_but_keep_distinct_quotes(batch_setup, monkeypatch, caplog):
    counts = count_queries(monkeypatch)
    with caplog.at_level(logging.INFO, logger="erp.pricing"):
        result = calculate_sku_prices(batch())
    assert len(result["items"]) == 198
    assert result["metrics"]["target_count"] == 396
    assert result["metrics"]["failed_sku_count"] == 0
    assert counts == {"/v2/warehouse/list": 1, "/v2/delivery-method/list": 1}
    amounts = [row["result"]["results"][0]["shipping_amount"] for row in result["items"]]
    assert len(set(amounts)) == 198
    assert "198/198" in caplog.text
    assert "Ozon 公共渠道查询" in caplog.text
    assert result["metrics"]["duration_ms"] >= result["metrics"]["ozon_discovery_ms"] >= 0
    # 下批重新读取店铺渠道，不把旧授权/渠道缓存到其他请求。
    calculate_sku_prices(batch(1))
    assert counts == {"/v2/warehouse/list": 2, "/v2/delivery-method/list": 2}


def test_shared_query_failure_is_not_retried_for_every_sku(batch_setup, monkeypatch):
    calls = []

    def fail(client, path, **kwargs):
        calls.append(path)
        raise ValueError("Ozon 物流接口不可用或响应无效")

    monkeypatch.setattr(PlatformClient, "call", fail)
    result = calculate_sku_prices(batch())
    assert calls == ["/v2/warehouse/list"]
    assert result["metrics"]["failed_sku_count"] == 198
    for row in result["items"]:
        ozon, yandex = row["result"]["results"]
        assert not ozon["ok"] and "物流接口不可用" in str(ozon["errors"])
        assert yandex["ok"]
    calculate_sku_prices(batch(1))
    assert len(calls) == 2


def test_rates_are_fetched_once_even_with_force_refresh(batch_setup, monkeypatch):
    calls = []

    def rates(force):
        calls.append(force)
        return {"ok": True, "rates": {"usd_cny_rate": 7, "mxn_usd_rate": 20, "rub_cny_rate": 12}}

    monkeypatch.setattr(pricing_runtime, "fetch_pricing_exchange_rates", rates)
    body = batch(3, mode="live")
    for item in body["items"]:
        item["input"]["force_exchange_rate_refresh"] = True
    original = deepcopy(body)
    result = calculate_sku_prices(body)
    assert calls == [True]
    assert result["metrics"]["failed_sku_count"] == 0
    assert body == original


def test_manual_shipping_does_not_query_channels(batch_setup, monkeypatch):
    counts = count_queries(monkeypatch)
    body = batch(2)
    for item in body["items"]:
        item["input"]["targets"] = [target(shipping_quote_mode="manual", shipping_amount=5)]
    result = calculate_sku_prices(body)
    assert not counts
    assert result["metrics"]["ozon_discovery_ms"] == 0
    assert result["metrics"]["failed_sku_count"] == 0


@pytest.mark.parametrize("body", [{}, {"items": []}, {"items": [None]}, {"items": [{"sku_id": "", "input": {}}]}, {"items": [batch(1)["items"][0]] * 2}])
def test_invalid_batch_rejected_before_external_calls(body, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("无效请求不得读取店铺或访问外部接口")

    monkeypatch.setattr(get_context().config, "load_store_config", fail)
    with pytest.raises(RequestValidationError):
        calculate_sku_prices(body)
