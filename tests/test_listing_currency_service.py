from __future__ import annotations

import pytest

from erp_web.services.listing_currency_service import (
    STORE_CURRENCY_MANUAL_REQUIRED,
    STORE_CURRENCY_REFRESH_FAILED,
    STORE_CURRENCY_SELECTION_REQUIRED,
    STORE_CURRENCY_UNRESOLVED,
    CurrencySelectionError,
    StoreCurrencyNotReadyError,
    apply_currency_discovery,
    apply_currency_selection,
    empty_store_listing_currency,
    public_currency_configuration,
    require_store_listing_currency,
    reset_currency_state,
    store_identity_for_platform,
    store_listing_currency_from_auth,
)

# ---------------------------------------------------------------------------
# 店铺配置状态机（迁移方案 §16.1）
# ---------------------------------------------------------------------------


def test_single_remote_currency_becomes_locked_ready() -> None:
    state = apply_currency_discovery(
        "ozon",
        "123456",
        {"supported": True, "currencies": ["cny"], "source": "account_api"},
    )

    assert state["currency_mode"] == "locked"
    assert state["currency_status"] == "ready"
    assert state["listing_currency"] == "CNY"
    assert state["allowed_currencies"] == ["CNY"]
    assert state["currency_source"] == "account_api"
    assert state["currency_verified_at"]
    assert state["currency_fingerprint"].startswith("sha256:")


def test_multiple_currencies_without_selection_require_selection() -> None:
    state = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN"], "source": "site_api"},
    )

    assert state["currency_mode"] == "selectable"
    assert state["currency_status"] == "selection_required"
    assert state["listing_currency"] == ""
    assert state["allowed_currencies"] == ["USD", "MXN"]


def test_multiple_currencies_keep_valid_previous_selection() -> None:
    previous = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN"], "source": "site_api"},
    )
    selected = apply_currency_selection("mercadolibre", "999", previous, "mxn")

    refreshed = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN", "BRL"], "source": "site_api"},
        previous=selected,
    )

    assert refreshed["currency_status"] == "ready"
    assert refreshed["listing_currency"] == "MXN"


def test_multiple_currencies_clear_invalid_previous_selection() -> None:
    previous = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN"], "source": "site_api"},
    )
    selected = apply_currency_selection("mercadolibre", "999", previous, "MXN")

    refreshed = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "BRL"], "source": "site_api"},
        previous=selected,
    )

    assert refreshed["currency_status"] == "selection_required"
    assert refreshed["listing_currency"] == ""


def test_platform_without_query_capability_requires_manual() -> None:
    state = apply_currency_discovery("mercadolibre", "999", {"supported": False})

    assert state["currency_mode"] == "manual"
    assert state["currency_status"] == "manual_required"
    assert state["listing_currency"] == ""
    assert state["allowed_currencies"] == []


def test_supported_but_failed_request_marks_refresh_failed_not_manual() -> None:
    previous = apply_currency_discovery(
        "yandex",
        "777",
        {"supported": True, "currencies": ["CNY"], "source": "business_settings"},
    )

    failed = apply_currency_discovery(
        "yandex",
        "777",
        {"supported": True, "error_code": "HTTP_429", "error_message": "限流"},
        previous=previous,
    )

    assert failed["currency_status"] == "refresh_failed"
    assert failed["currency_mode"] == "locked"
    assert failed["listing_currency"] == "CNY"
    assert failed["currency_error_code"] == "HTTP_429"
    assert failed["currency_error_message"] == "限流"


def test_supported_but_empty_response_marks_refresh_failed() -> None:
    failed = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": []},
    )

    assert failed["currency_status"] == "refresh_failed"
    assert failed["currency_error_code"] == "CURRENCY_DISCOVERY_EMPTY"
    assert failed["currency_mode"] == "unresolved"
    assert failed["listing_currency"] == ""


def test_manual_selection_normalizes_case_and_validates_iso_code() -> None:
    manual_required = apply_currency_discovery("mercadolibre", "999", {"supported": False})

    state = apply_currency_selection("mercadolibre", "999", manual_required, " usd ")

    assert state["listing_currency"] == "USD"
    assert state["currency_mode"] == "manual"
    assert state["currency_status"] == "ready"
    assert state["currency_source"] == "manual"
    assert state["allowed_currencies"] == []

    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("mercadolibre", "999", manual_required, "USDT")
    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("mercadolibre", "999", manual_required, "")


def test_locked_mode_rejects_manual_edit() -> None:
    locked = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )

    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("ozon", "123", locked, "RUB")


def test_selectable_selection_must_belong_to_allowed_set() -> None:
    selectable = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN"], "source": "site_api"},
    )

    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("mercadolibre", "999", selectable, "BRL")


def test_selection_rejected_when_refresh_failed_or_unresolved() -> None:
    failed = apply_currency_discovery(
        "yandex",
        "777",
        {"supported": True, "error_code": "HTTP_500", "error_message": "服务异常"},
    )
    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("yandex", "777", failed, "RUB")

    with pytest.raises(CurrencySelectionError):
        apply_currency_selection("ozon", "", empty_store_listing_currency(), "CNY")


def test_identity_or_credential_change_resets_ready_state() -> None:
    ready = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )
    assert ready["currency_status"] == "ready"

    cleared = reset_currency_state("ozon", "456")
    assert cleared["currency_status"] == "unresolved"
    assert cleared["listing_currency"] == ""
    assert cleared["currency_fingerprint"] != ready["currency_fingerprint"]


def test_fingerprint_stable_across_timestamps_and_sensitive_to_changes() -> None:
    first = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )
    second = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )
    assert first["currency_fingerprint"] == second["currency_fingerprint"]

    other_store = apply_currency_discovery(
        "ozon",
        "999",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )
    assert other_store["currency_fingerprint"] != first["currency_fingerprint"]

    other_currency = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["RUB"], "source": "account_api"},
    )
    assert other_currency["currency_fingerprint"] != first["currency_fingerprint"]

    selectable = apply_currency_discovery(
        "mercadolibre",
        "999",
        {"supported": True, "currencies": ["USD", "MXN"], "source": "site_api"},
    )
    assert selectable["currency_fingerprint"] != first["currency_fingerprint"]


def test_projection_from_auth_detail_enforces_invariants() -> None:
    state = store_listing_currency_from_auth(
        "ozon",
        "123",
        {
            "listing_currency": "cny",
            "allowed_currencies": ["CNY"],
            "currency_mode": "locked",
            "currency_status": "ready",
            "currency_source": "account_api",
            "currency_verified_at": "2026-08-23T12:00:00Z",
        },
    )

    assert state["listing_currency"] == "CNY"
    assert state["currency_status"] == "ready"
    assert state["currency_fingerprint"].startswith("sha256:")

    broken = store_listing_currency_from_auth(
        "ozon",
        "123",
        {"listing_currency": "", "currency_mode": "locked", "currency_status": "ready"},
    )
    assert broken["currency_status"] == "unresolved"


def test_require_store_listing_currency_error_codes() -> None:
    ready_store = {
        "client_id": "123",
        "listing_currency": "CNY",
        "allowed_currencies": ["CNY"],
        "currency_mode": "locked",
        "currency_status": "ready",
        "currency_source": "account_api",
    }
    state = require_store_listing_currency("ozon", ready_store)
    assert state["listing_currency"] == "CNY"

    for store, expected_code in (
        ({"client_id": "1"}, STORE_CURRENCY_UNRESOLVED),
        (
            {
                "client_id": "1",
                "currency_mode": "selectable",
                "currency_status": "selection_required",
                "allowed_currencies": ["USD", "MXN"],
            },
            STORE_CURRENCY_SELECTION_REQUIRED,
        ),
        (
            {
                "currency_mode": "manual",
                "currency_status": "manual_required",
            },
            STORE_CURRENCY_MANUAL_REQUIRED,
        ),
        (
            {
                "listing_currency": "CNY",
                "currency_mode": "locked",
                "currency_status": "refresh_failed",
                "currency_error_code": "HTTP_500",
                "currency_error_message": "服务异常",
            },
            STORE_CURRENCY_REFRESH_FAILED,
        ),
    ):
        with pytest.raises(StoreCurrencyNotReadyError) as excinfo:
            require_store_listing_currency("ozon", store)
        assert excinfo.value.code == expected_code


def test_store_identity_for_platform() -> None:
    assert store_identity_for_platform("ozon", {"client_id": " 12 "}) == "12"
    assert (
        store_identity_for_platform("yandex", {"campaign_id": "5", "business_id": "7"})
        == "7"
    )
    assert store_identity_for_platform("yandex", {"campaign_id": "5"}) == "5"
    assert store_identity_for_platform("mercadolibre", {"user_id": "99"}) == "99"
    assert store_identity_for_platform("mercadolibre", {"seller_id": "88"}) == "88"


def test_public_currency_configuration_shape() -> None:
    state = apply_currency_discovery(
        "ozon",
        "123",
        {"supported": True, "currencies": ["CNY"], "source": "account_api"},
    )
    public = public_currency_configuration(state)

    assert set(public) == {
        "listing_currency",
        "allowed_currencies",
        "currency_mode",
        "currency_status",
        "currency_source",
        "currency_verified_at",
        "currency_error_code",
        "currency_error_message",
    }
    assert "currency_fingerprint" not in public

