from __future__ import annotations

import json

import pytest

from erp_web.context import get_context
from erp_web.runtime_units import pricing_runtime


def _service_cfg(ttl_seconds: int = 3600) -> dict:
    return {
        "api_url": "https://rates.example/api",
        "timeout_seconds": 5,
        "cache_ttl_seconds": ttl_seconds,
    }


class _FakeRatesResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_fetch_writes_exchange_rates_table_and_restart_reuses_db_within_ttl(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeRatesResponse({"rates": {"CNY": 7.21, "MXN": 18.3, "RUB": 90.5}})

    monkeypatch.setattr(pricing_runtime.urllib.request, "urlopen", fake_urlopen)
    db = get_context().db

    first = get_context().exchange_rates.get_rates(_service_cfg(), force_refresh=False)

    assert first["ok"] is True
    assert first["cached"] is False
    assert first["rates"]["usd_cny_rate"] == 7.21
    stored = db.load_exchange_rates()
    assert stored["rates"]["USD/CNY"] == 7.21
    assert stored["rates"]["USD/MXN"] == 18.3
    assert stored["fetched_at"] == first["fetched_at"]

    # 新实例 = 重启后的空内存：TTL 内直接用表值，不再打外部 API。
    restarted = pricing_runtime.ExchangeRateService(db)
    second = restarted.get_rates(_service_cfg(), force_refresh=False)

    assert second["ok"] is True
    assert second["cached"] is True
    assert second["source"] == "exchange_rates_table"
    assert second["rates"]["usd_cny_rate"] == 7.21
    assert "stale" not in second
    assert len(calls) == 1


def test_fetch_failure_falls_back_to_latest_table_snapshot_marked_stale(monkeypatch) -> None:
    db = get_context().db
    db.save_exchange_rates({"USD/CNY": 7.0, "USD/MXN": 17.0, "USD/RUB": 80.0}, "2026-01-01T00:00:00Z")

    def failing_urlopen(request, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pricing_runtime.urllib.request, "urlopen", failing_urlopen)
    service = pricing_runtime.ExchangeRateService(db)

    result = service.get_rates(_service_cfg(), force_refresh=True)

    assert result["ok"] is True
    assert result["stale"] is True
    assert result["cached"] is True
    assert result["rates"]["usd_cny_rate"] == 7.0
    assert result["rates"]["mxn_usd_rate"] == 17.0
    assert result["rates"]["rub_cny_rate"] == round(80.0 / 7.0, 6)
    assert "实时汇率获取失败" in result["error"]


def test_fetch_failure_without_table_snapshot_reports_error(monkeypatch) -> None:
    def failing_urlopen(request, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pricing_runtime.urllib.request, "urlopen", failing_urlopen)
    service = pricing_runtime.ExchangeRateService(get_context().db)

    result = service.get_rates(_service_cfg(), force_refresh=True)

    assert result["ok"] is False
    assert "实时汇率获取失败" in result["error"]


def test_extract_usd_rates_supports_open_er_api_payload() -> None:
    rates = pricing_runtime._extract_usd_rates(
        {
            "result": "success",
            "base_code": "USD",
            "rates": {
                "CNY": 7.18,
                "MXN": 18.25,
                "RUB": 89.5,
            },
        }
    )

    assert rates == {"CNY": 7.18, "MXN": 18.25, "RUB": 89.5}


def test_extract_usd_rates_supports_conversion_rates_payload() -> None:
    rates = pricing_runtime._extract_usd_rates(
        {
            "result": "success",
            "base_code": "USD",
            "conversion_rates": {
                "CNY": 7.18,
                "MXN": 18.25,
                "RUB": 89.5,
            },
        }
    )

    assert rates == {"CNY": 7.18, "MXN": 18.25, "RUB": 89.5}


def test_live_batch_pricing_uses_fetched_rates_when_common_rates_are_empty(monkeypatch) -> None:
    def fake_fetch_pricing_exchange_rates(force_refresh: bool = False):
        return {
            "ok": True,
            "source": "test://rates",
            "fetched_at": "2026-07-19T00:00:00Z",
            "cached": False,
            "rates": {
                "usd_cny_rate": 6.7892,
                "mxn_usd_rate": 17.521375,
                "rub_usd_rate": 77.999985,
                "rub_cny_rate": 11.489603,
                "currency_usd_rates": {
                    "USD": 1,
                    "CNY": 6.7892,
                    "MXN": 17.521375,
                    "CLP": 942.61,
                    "RUB": 77.999985,
                },
            },
        }

    monkeypatch.setattr(pricing_runtime, "fetch_pricing_exchange_rates", fake_fetch_pricing_exchange_rates)

    # 发布币种唯一事实源是店铺授权配置：显式创建 ready 店铺配置后，
    # 各站点目标共用同一店铺币种（MXN）。
    from tests.runtime_test_utils import seed_store_currency

    seed_store_currency("mercadolibre", "MXN", identity={"user_id": "99"})

    result = pricing_runtime.calculate_price(
        {
            "exchange_rate_mode": "live",
            "common": {
                "purchase_cost": 94,
                "weight_kg": 0.3,
                "usd_cny_rate": "",
                "mxn_usd_rate": "",
                "rub_cny_rate": "",
            },
            "targets": [
                {"target_key": "mercadolibre:mlb", "platform": "mercadolibre", "site": "MLB", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlm", "platform": "mercadolibre", "site": "MLM", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlc", "platform": "mercadolibre", "site": "MLC", "commission_percent": 16, "target_margin_percent": 30},
            ],
        }
    )

    assert result["ok"] is True
    assert result["exchange_rate_mode"] == "live"
    assert result["exchange_rates"]["source"] == "test://rates"
    assert result["input"]["common"]["usd_cny_rate"] == 6.7892
    assert result["input"]["common"]["mxn_usd_rate"] == 17.521375
    assert [target["errors"] for target in result["results"]] == [[], [], []]
    assert [float(target["suggested_price"]["amount"]) > 0 for target in result["results"]] == [True, True, True]


@pytest.mark.parametrize(
    ("sites_to_sell", "bindings", "error_code", "expected_options"),
    [
        (
            [],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                }
            ],
            "MERCADOLIBRE_SITES_TO_SELL_REQUIRED",
            ["MLM:remote"],
        ),
        (
            [{"site_id": "MLM", "logistic_type": "drop_off"}],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                }
            ],
            "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED",
            ["MLM:remote"],
        ),
        (
            [{"site_id": "MLM", "logistic_type": "remote"}],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "business_model": "standard",
                    "pricing_model": "price",
                },
                {
                    "site_id": "MLB",
                    "logistic_type": "remote",
                    "business_model": "CBT CN Fulfillment Managed",
                    "pricing_model": "price",
                },
            ],
            "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED",
            [],
        ),
    ],
)
def test_cbt_pricing_blocks_invalid_sales_target_contract(
    sites_to_sell: list[dict[str, str]],
    bindings: list[dict[str, str]],
    error_code: str,
    expected_options: list[str],
) -> None:
    from tests.runtime_test_utils import seed_store_currency

    seed_store_currency(
        "mercadolibre",
        "USD",
        identity={
            "user_id": "99",
            "account_site_id": "CBT",
            "marketplace_bindings": bindings,
        },
    )

    result = pricing_runtime.calculate_price(
        {
            "exchange_rate_mode": "manual",
            "usd_cny_rate": 7,
            "mxn_usd_rate": 17,
            "common": {"purchase_cost": 100, "weight_kg": 0.5},
            "targets": [
                {
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "sites_to_sell": sites_to_sell,
                }
            ],
        }
    )

    assert result["ok"] is False
    assert result["error_code"] == error_code
    assert result["field"]
    assert result["next_action"]
    assert result["sales_target_options"] == expected_options
