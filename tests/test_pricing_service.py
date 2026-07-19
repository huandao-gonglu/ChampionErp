from __future__ import annotations

import math

from erp_web.services import pricing_service


def _assert_finite_positive(result: dict, *keys: str) -> None:
    for key in keys:
        value = result.get(key)
        assert value not in (None, "")
        assert not (isinstance(value, float) and math.isnan(value))
        assert float(value) > 0


def test_mercadolibre_pricing_calculates_expected_breakdown() -> None:
    result = pricing_service.pricing_result(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 100,
            "domestic_freight": 10,
            "packaging_cost": 5,
            "other_cost": 3,
            "warehousing_cost": 2,
            "advertising_cost": 4,
            "other_platform_fee": 1,
            "ml_shipping_usd": 8,
            "weight_kg": 1.2,
            "length_cm": 30,
            "width_cm": 20,
            "height_cm": 15,
            "target_margin_percent": 25,
            "commission_percent": 15,
            "payment_fee_percent": 2,
            "usd_cny_rate": 7.25,
            "mxn_usd_rate": 17,
            "wb_commission_percent": 20,
            "russia_freight_rate": 12,
            "rub_cny_rate": 12,
        }
    )

    assert result["ok"] is True
    assert result["suggested_price_mxn"] == 739.83
    assert result["suggested_price_usd"] == 43.52
    assert result["sale_price_mxn"] == 739.83
    assert result["sale_price_usd"] == 43.52
    assert result["shipping_cost_usd"] == 8.0
    assert result["shipping_cost_cny"] == 58.0
    assert result["commission_cny"] == 47.33
    assert result["payment_fee_cny"] == 6.31
    assert result["total_cost_cny"] == 183.0
    assert result["net_revenue_cny"] == 203.88
    assert result["net_proceeds_usd"] == 28.12
    assert result["profit_cny"] == 78.88
    assert result["profit_usd"] == 10.88
    assert result["profit_percent"] == 25.0
    assert result["is_loss"] is False
    assert result["wb_price_rub"] == 3011
    assert result["breakdown"] == {
        "billable_weight_kg": 1.5,
        "billable_weight_g": 1500,
        "actual_weight_kg": 1.2,
        "volume_weight_kg": 1.5,
        "common_base_cny": 120.0,
        "ml_base_cny": 183.0,
        "cost_cny": 100.0,
        "freight_cny": 10.0,
        "prep_fee_cny": 5.0,
        "international_freight_cny": 0.0,
        "other_cost_cny": 3.0,
        "warehousing_cost_cny": 2.0,
        "advertising_cost_cny": 4.0,
        "other_platform_fee_cny": 1.0,
        "ml_shipping_usd": 8.0,
        "ml_shipping_cny": 58.0,
        "commission_cny": 47.33,
        "commission_percent": 15.0,
        "payment_fee_cny": 6.31,
        "payment_fee_percent": 2.0,
        "target_margin_percent": 25.0,
        "usd_cny_rate": 7.25,
        "mxn_usd_rate": 17.0,
        "desktop_formula": "price_usd = total_cost_cny / (1 - commission - payment_fee - margin) / USD_CNY",
    }


def test_mercadolibre_pricing_estimates_shipping_from_volume_weight() -> None:
    result = pricing_service.pricing_result(
        {
            "purchase_cost": 45,
            "weight_kg": 0.2,
            "length_cm": 60,
            "width_cm": 40,
            "height_cm": 30,
            "target_margin_percent": 30,
            "commission_percent": 16,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    assert result["ok"] is True
    assert result["breakdown"]["actual_weight_kg"] == 0.2
    assert result["breakdown"]["volume_weight_kg"] == 12.0
    assert result["breakdown"]["billable_weight_kg"] == 12.0
    assert result["shipping_cost_usd"] == 37.8
    assert result["shipping_cost_cny"] == 272.16
    assert result["total_cost_cny"] == 317.16
    assert result["suggested_price_mxn"] == 1468.33
    assert result["profit_percent"] == 30.0


def test_actual_sale_price_can_show_loss_against_suggested_price() -> None:
    result = pricing_service.pricing_result(
        {
            "purchase_cost": 100,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "sale_price_mxn": 200,
            "target_margin_percent": 30,
            "commission_percent": 16,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    assert result["ok"] is True
    assert result["suggested_price_mxn"] == 576.3
    assert result["sale_price_mxn"] == 200.0
    assert result["sale_price_usd"] == 11.11
    assert result["profit_cny"] == -57.28
    assert result["profit_percent"] == -71.6
    assert result["is_loss"] is True


def test_mercadolibre_pricing_returns_user_visible_fields() -> None:
    result = pricing_service.pricing_result(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 30,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "target_margin_percent": 30,
            "commission_percent": 16,
            "international_shipping_usd": 3.4,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    _assert_finite_positive(
        result,
        "suggested_price_mxn",
        "suggested_price_usd",
        "reverse_price_mxn",
        "reverse_price_usd",
        "shipping_cost_usd",
        "commission_cny",
        "total_cost_cny",
        "net_revenue_cny",
        "profit_cny",
    )
    assert "profit_percent" in result
    assert result["profit_percent"] not in (None, "")
    assert result["breakdown"]["billable_weight_kg"] > 0
    assert result["breakdown"]["volume_weight_kg"] > 0
    assert isinstance(result["breakdown"], dict)
    assert result["suggested_price_mxn"] > 0
    assert result["ok"] is True


def test_reverse_price_from_target_net_income() -> None:
    result = pricing_service.pricing_result(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 30,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "target_net_proceeds_usd": 10,
            "commission_percent": 16,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    assert result["mode"] == "reverse_net_proceeds"
    _assert_finite_positive(result, "reverse_price_mxn", "reverse_price_usd", "net_proceeds_usd")


def test_reverse_price_from_target_profit_calculates_expected_sale_price() -> None:
    result = pricing_service.pricing_result(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 30,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "target_profit_cny": 25,
            "commission_percent": 16,
            "payment_fee_percent": 2,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    assert result["mode"] == "reverse_profit"
    assert result["sale_price_mxn"] == 242.32
    assert result["sale_price_usd"] == 13.46
    assert result["profit_cny"] == 25.0
    assert result["profit_usd"] == 3.47
    assert result["profit_percent"] == 25.79


def test_reverse_price_from_target_net_income_calculates_expected_sale_price() -> None:
    result = pricing_service.pricing_result(
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "purchase_cost": 30,
            "weight_kg": 0.5,
            "length_cm": 20,
            "width_cm": 15,
            "height_cm": 10,
            "target_net_proceeds_usd": 10,
            "commission_percent": 16,
            "payment_fee_percent": 2,
            "usd_cny_rate": 7.2,
            "mxn_usd_rate": 18,
        }
    )

    assert result["mode"] == "reverse_net_proceeds"
    assert result["sale_price_mxn"] == 294.15
    assert result["sale_price_usd"] == 16.34
    assert result["reverse_price_mxn"] == 294.15
    assert result["reverse_price_usd"] == 16.34
    assert result["net_proceeds_usd"] == 10.0
    assert result["net_revenue_cny"] == 72.0
    assert result["profit_cny"] == 42.0
    assert result["breakdown"]["reverse_net_formula"] == "price_usd = (target_net_income_usd + shipping_usd) / (1 - commission - payment_fee)"


def test_pricing_reports_validation_errors_for_missing_required_inputs() -> None:
    result = pricing_service.pricing_result(
        {
            "purchase_cost": 0,
            "weight_kg": 0,
            "length_cm": 0,
            "width_cm": 0,
            "height_cm": 0,
            "target_margin_percent": 90,
            "commission_percent": 15,
            "usd_cny_rate": 0,
            "mxn_usd_rate": 0,
        }
    )

    assert result["ok"] is False
    assert result["errors"] == [
        {"field": "cost_cny", "message": "采购成本缺失"},
        {"field": "weight_or_dimensions", "message": "重量或尺寸缺失"},
        {"field": "usd_cny_rate", "message": "USD/CNY 汇率缺失"},
        {"field": "mxn_usd_rate", "message": "MXN/USD 汇率缺失"},
        {"field": "margin_percent", "message": "目标利润率 + Mercado Libre 佣金 + 支付手续费不能大于等于 100%"},
    ]
    assert result["precheck_errors"] == result["errors"]


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
                {"target_key": "mercadolibre:cbt", "platform": "mercadolibre", "site": "CBT", "currency": "USD", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlm", "platform": "mercadolibre", "site": "MLM", "currency": "MXN", "commission_percent": 16, "target_margin_percent": 30},
                {"target_key": "mercadolibre:mlc", "platform": "mercadolibre", "site": "MLC", "currency": "CLP", "commission_percent": 16, "target_margin_percent": 30},
            ],
        }
    )

    assert result["ok"] is True
    assert [item["errors"] for item in result["results"]] == [[], [], []]
    assert result["results"][0]["suggested_price"] > 0
    assert result["results"][1]["suggested_price"] > 0
    assert result["results"][2]["suggested_price"] > 0
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
                    "currency": "MXN",
                    "commission_percent": 16,
                    "target_margin_percent": 30,
                    "shipping_cost_usd": 0,
                    "applied_price": 0,
                }
            ],
        }
    )

    target = result["results"][0]
    assert target["ok"] is True
    assert target["shipping_cost_usd"] > 0
    assert target["suggested_price"] > 0
    assert target["applied_price"] == target["suggested_price"]
    assert target["margin_percent"] == 30.0
