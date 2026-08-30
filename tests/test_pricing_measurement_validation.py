from __future__ import annotations

from types import SimpleNamespace

from erp_web.runtime_units import publish_validation
from erp_web.services.pricing_service import pricing_calculation_fingerprint


def _draft_with_basis(basis: dict[str, str]) -> dict:
    return {
        "platform": "mercadolibre",
        "site": "CBT",
        "listing_currency": "USD",
        "sites_to_sell": [],
        "package_dimensions": {
            "length_cm": "5.5",
            "width_cm": "6",
            "height_cm": "16",
            "weight_kg": "0.182",
        },
        "selected_pricing": {
            "listing_currency": "USD",
            "applied_price": {"amount": "18.00", "currency": "USD"},
            "calculation_basis": basis,
            "calculation_fingerprint": pricing_calculation_fingerprint(basis),
        },
    }


def test_same_precise_package_measurements_do_not_invalidate_pricing(monkeypatch) -> None:
    store_state = {
        "listing_currency": "USD",
        "currency_status": "ready",
        "currency_fingerprint": "store-fingerprint",
    }
    context = SimpleNamespace(
        config=SimpleNamespace(
            load_store_config=lambda: {"mercadolibre": {}},
        )
    )
    monkeypatch.setattr(publish_validation, "get_context", lambda: context)
    monkeypatch.setattr(
        publish_validation,
        "store_listing_currency_from_auth",
        lambda *_args, **_kwargs: store_state,
    )
    monkeypatch.setattr(
        publish_validation,
        "store_listing_currency_ready",
        lambda _state: True,
    )
    basis = {
        "listing_currency": "USD",
        "currency_fingerprint": "store-fingerprint",
        "length_cm": "5.5",
        "width_cm": "6",
        "height_cm": "16",
        "weight_kg": "0.182",
        "sites_to_sell": [],
    }

    errors = publish_validation._selected_price_errors({}, _draft_with_basis(basis))

    assert errors == []


def test_old_rounded_weight_basis_remains_stale(monkeypatch) -> None:
    store_state = {
        "listing_currency": "USD",
        "currency_status": "ready",
        "currency_fingerprint": "store-fingerprint",
    }
    context = SimpleNamespace(
        config=SimpleNamespace(
            load_store_config=lambda: {"mercadolibre": {}},
        )
    )
    monkeypatch.setattr(publish_validation, "get_context", lambda: context)
    monkeypatch.setattr(
        publish_validation,
        "store_listing_currency_from_auth",
        lambda *_args, **_kwargs: store_state,
    )
    monkeypatch.setattr(
        publish_validation,
        "store_listing_currency_ready",
        lambda _state: True,
    )
    basis = {
        "listing_currency": "USD",
        "currency_fingerprint": "store-fingerprint",
        "length_cm": "5.5",
        "width_cm": "6",
        "height_cm": "16",
        "weight_kg": "0.18",
        "sites_to_sell": [],
    }

    errors = publish_validation._selected_price_errors({}, _draft_with_basis(basis))

    assert errors[0]["code"] == "PRICING_STALE"
    assert "重量或包装尺寸已变化" in errors[0]["message"]
