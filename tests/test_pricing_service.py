from __future__ import annotations

import math

from services import pricing_service


def _assert_finite_positive(result: dict, *keys: str) -> None:
    for key in keys:
        value = result.get(key)
        assert value not in (None, "")
        assert not (isinstance(value, float) and math.isnan(value))
        assert float(value) > 0


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
