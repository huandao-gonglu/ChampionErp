from __future__ import annotations

from erp_web.services.listing_currency_service import resolve_listing_currency


def test_mercadolibre_listing_currency_is_locked_by_site() -> None:
    result = resolve_listing_currency("mercadolibre", "MLB", {})

    assert result["mode"] == "site_locked"
    assert result["listing_currency"] == "BRL"
    assert result["source"] == "site_rule"


def test_ozon_listing_currency_is_locked_by_verified_account() -> None:
    result = resolve_listing_currency(
        "ozon",
        "global",
        {
            "contract_currency": "cny",
            "currency_source": "account_api",
            "currency_verified_at": "2026-08-07T00:00:00Z",
        },
    )

    assert result["mode"] == "account_locked"
    assert result["listing_currency"] == "CNY"
    assert result["allowed_currencies"] == ["CNY"]


def test_ozon_never_falls_back_to_market_currency() -> None:
    result = resolve_listing_currency("ozon", "global", {})

    assert result["mode"] == "unresolved"
    assert result["listing_currency"] == ""
    assert result["source"] == "account_api_required"
