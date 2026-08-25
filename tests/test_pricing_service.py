from __future__ import annotations

import math

from erp_web.services import pricing_service


def _assert_finite_positive(result: dict, *keys: str) -> None:
    for key in keys:
        value = result.get(key)
        assert value not in (None, "")
        assert not (isinstance(value, float) and math.isnan(value))
        assert float(value) > 0


def test_batch_pricing_keeps_live_rates_when_common_rates_are_empty() -> None:
    result = pricing_service.pricing_result(
        {
            "usd_cny_rate": 6.7892,
            "mxn_usd_rate": 17.521375,
            "rub_cny_rate": 11.489603,
            "currency_usd_rates": {"USD": 1, "MXN": 17.521375, "CLP": 942.61},
            "common": {
                "purchase_cost": 94,
                "weight_kg": 0.3,
                "usd_cny_rate": "",
                "mxn_usd_rate": "",
                "rub_cny_rate": "",
            },
            "targets": [
                {"target_key": "mercadolibre:cbt", "platform": "mercadolibre", "site": "CBT", "listing_currency": "USD", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlm", "platform": "mercadolibre", "site": "MLM", "listing_currency": "MXN", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlc", "platform": "mercadolibre", "site": "MLC", "listing_currency": "CLP", "commission_percent": 16, "target_margin_percent": 30},
            ],
        }
    )

    assert result["ok"] is True
    assert [item["errors"] for item in result["results"]] == [[], [], []]
    assert float(result["results"][0]["suggested_price"]["amount"]) > 0
    assert float(result["results"][1]["suggested_price"]["amount"]) > 0
    assert float(result["results"][2]["suggested_price"]["amount"]) > 0
    assert result["results"][0]["shipping_cost_usd"] > 0
    assert result["results"][0]["applied_price"] == result["results"][0]["suggested_price"]
    assert result["results"][1]["applied_price"] == result["results"][1]["suggested_price"]


def test_batch_pricing_treats_zero_applied_price_as_use_suggested_price() -> None:
    result = pricing_service.pricing_result(
        {
            "common": {
                "purchase_cost": 94,
                "weight_kg": 0.3,
                "usd_cny_rate": 6.7892,
                "mxn_usd_rate": 17.521375,
            },
            "targets": [
                {
                    "target_key": "mercadolibre:mlm",
                    "platform": "mercadolibre",
                    "site": "MLM",
                    "listing_currency": "MXN",
                    "commission_percent": 16,
                    "target_margin_percent": 30,
                    "shipping_cost_usd": 0,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is True
    assert target["shipping_cost_usd"] > 0
    assert float(target["suggested_price"]["amount"]) > 0
    assert target["applied_price"] == target["suggested_price"]
    assert target["margin_percent"] == 30.0


def test_invalid_percentage_budget_does_not_generate_extreme_price() -> None:
    result = pricing_service.pricing_result(
        {
            "common": {
                "purchase_cost": 100,
                "usd_cny_rate": 7,
                "rub_cny_rate": 12,
            },
            "targets": [
                {
                    "target_key": "ozon:global",
                    "platform": "ozon",
                    "site": "global",
                    "listing_currency": "RUB",
                    "commission_percent": 50,
                    "payment_fee_percent": 10,
                    "target_margin_percent": 40,
                    "pricing_mode": "margin",
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "CNY",
                    "shipping_amount": 20,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is False
    assert target["suggested_price"] == {"amount": "0.00", "currency": "RUB"}
    assert target["applied_price"] == {"amount": "0.00", "currency": "RUB"}
    assert target["profit_cny"] == 0
    assert target["errors"] == [
        {"field": "target_margin_percent", "message": "平台费用合计 + 目标销售利润率必须小于 100%"},
    ]


def test_cost_markup_can_be_one_hundred_percent_with_single_shipping_quote() -> None:
    result = pricing_service.pricing_result(
        {
            "common": {
                "purchase_cost": 100,
                "domestic_freight": 10,
                "packaging_cost": 5,
                "other_cost": 5,
                "usd_cny_rate": 7,
            },
            "targets": [
                {
                    "target_key": "mercadolibre:cbt",
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "listing_currency": "USD",
                    "commission_percent": 20,
                    "payment_fee_percent": 2,
                    "other_fee_percent": 3,
                    "pricing_mode": "markup",
                    "markup_percent": 100,
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "USD",
                    "shipping_amount": 10,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is True
    assert target["shipping_currency"] == "USD"
    assert target["shipping_amount"] == 10
    assert target["shipping_cost_cny"] == 70
    assert target["total_cost_cny"] == 190
    assert target["suggested_price"] == {"amount": "72.38", "currency": "USD"}
    assert target["profit_cny"] == 190
    assert target["margin_percent"] == 37.5


def test_cbt_pricing_basis_canonicalizes_sales_targets_into_fingerprint() -> None:
    common = {
        "purchase_cost": 100,
        "usd_cny_rate": 7,
    }
    target = {
        "target_key": "mercadolibre:cbt",
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "commission_percent": 16,
        "target_margin_percent": 30,
        "shipping_quote_mode": "manual",
        "shipping_currency": "USD",
        "shipping_amount": 10,
        "sitesToSell": [
            {"siteId": "mlm", "logisticType": "REMOTE"},
            {"site_id": "MLB", "logistic_type": "remote"},
            {"site_id": "MLM", "logistic_type": "remote"},
        ],
    }

    first = pricing_service.pricing_result(
        {"common": common, "targets": [target]}
    )["results"][0]
    second = pricing_service.pricing_result(
        {
            "common": common,
            "targets": [
                {
                    **target,
                    "sitesToSell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                }
            ],
        }
    )["results"][0]

    assert first["calculation_basis"]["sites_to_sell"] == [
        {"site_id": "MLB", "logistic_type": "remote"},
        {"site_id": "MLM", "logistic_type": "remote"},
    ]
    assert (
        first["calculation_fingerprint"]
        != second["calculation_fingerprint"]
    )


def test_russia_market_requires_a_manual_shipping_quote() -> None:
    result = pricing_service.pricing_result(
        {
            "common": {"purchase_cost": 100, "usd_cny_rate": 7, "rub_cny_rate": 12},
            "targets": [
                {
                    "target_key": "yandex:global",
                    "platform": "yandex",
                    "site": "global",
                    "listing_currency": "RUB",
                    "pricing_mode": "margin",
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "CNY",
                    "shipping_amount": 0,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is False
    assert target["suggested_price"] == {"amount": "0.00", "currency": "RUB"}
    assert {"field": "shipping_amount", "message": "物流报价金额必须大于 0"} in target["errors"]


def test_cny_shipping_quote_is_not_treated_as_a_second_editable_amount() -> None:
    result = pricing_service.pricing_result(
        {
            "common": {"purchase_cost": 50, "usd_cny_rate": 7, "rub_cny_rate": 12},
            "targets": [
                {
                    "target_key": "ozon:global",
                    "platform": "ozon",
                    "site": "global",
                    "listing_currency": "RUB",
                    "commission_percent": 20,
                    "target_margin_percent": 30,
                    "pricing_mode": "margin",
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "CNY",
                    "shipping_amount": 100,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is True
    assert target["shipping_currency"] == "CNY"
    assert target["shipping_amount"] == 100
    assert target["shipping_cost_cny"] == 100
    assert target["suggested_price"] == {"amount": "3600.00", "currency": "RUB"}
