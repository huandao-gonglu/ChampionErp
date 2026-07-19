from __future__ import annotations

from erp_web import runtime as erp_web_app


def test_extract_usd_rates_supports_open_er_api_payload() -> None:
    rates = erp_web_app._extract_usd_rates(
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
    rates = erp_web_app._extract_usd_rates(
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

    monkeypatch.setattr(erp_web_app, "fetch_pricing_exchange_rates", fake_fetch_pricing_exchange_rates)

    result = erp_web_app.calculate_price(
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
                {"target_key": "mercadolibre:cbt", "platform": "mercadolibre", "site": "CBT", "currency": "USD", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlm", "platform": "mercadolibre", "site": "MLM", "currency": "MXN", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlc", "platform": "mercadolibre", "site": "MLC", "currency": "CLP", "commission_percent": 16, "target_margin_percent": 30},
            ],
        }
    )

    assert result["ok"] is True
    assert result["exchange_rate_mode"] == "live"
    assert result["exchange_rates"]["source"] == "test://rates"
    assert result["input"]["common"]["usd_cny_rate"] == 6.7892
    assert result["input"]["common"]["mxn_usd_rate"] == 17.521375
    assert [target["errors"] for target in result["results"]] == [[], [], []]
    assert [target["suggested_price"] > 0 for target in result["results"]] == [True, True, True]
