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
