# -*- coding: utf-8 -*-
from __future__ import annotations

"""店铺授权币种发现与持久化测试（迁移方案 §16.2/§9）。

覆盖：Yandex Business settings 币种解析与 wire 归一化、Ozon seller/info
币种锁定、Mercado Libre CBT 市场映射与 USD 合同币种、区域站点元数据、授权
测试响应契约（ok 与 publish_ready 分离）、人工选择接口与 save-settings 信任边界。
"""

from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.facades import auth_config_facade
from erp_web.marketplaces import config_http
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.marketplaces.yandex_currency import (
    yandex_internal_currency,
    yandex_wire_currency,
)
from erp_web.runtime_units import mercadolibre_auth, store_credentials
from erp_web.stores.config_store import sanitize_client_store_config
from tests.runtime_test_utils import temp_app_context
from tests.test_yandex_http import (
    API_TOKEN,
    _campaign_response,
    _install_auth_routes,
    _YandexHarness,
)


# ---------------------------------------------------------------------------
# wire 编码边界
# ---------------------------------------------------------------------------


def test_yandex_wire_currency_conversion_is_iso_boundary() -> None:
    assert yandex_wire_currency("RUB") == "RUR"
    assert yandex_wire_currency("rub") == "RUR"
    assert yandex_wire_currency("CNY") == "CNY"
    assert yandex_internal_currency("RUR") == "RUB"
    assert yandex_internal_currency("rur") == "RUB"
    assert yandex_internal_currency("CNY") == "CNY"


# ---------------------------------------------------------------------------
# Yandex：Business settings.currency 发现
# ---------------------------------------------------------------------------


def _yandex_harness(monkeypatch: pytest.MonkeyPatch) -> _YandexHarness:
    harness = _YandexHarness()
    harness.install(monkeypatch)
    return harness


def _yandex_fby_config() -> dict[str, Any]:
    return {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}


def test_yandex_auth_discovery_saves_business_currency_cny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness = _yandex_harness(monkeypatch)
    _install_auth_routes(
        harness,
        campaign=_campaign_response("FBY"),
        settings={
            "status": "OK",
            "result": {"settings": {"onlyDefaultPrice": False, "currency": "CNY"}},
        },
    )

    config = _yandex_fby_config()
    result = _test_yandex_auth(config, "")

    assert result["currency_discovery"] == {
        "supported": True,
        "currencies": ["CNY"],
        "source": "business_settings",
    }


def test_yandex_auth_discovery_normalizes_wire_rur_to_internal_rub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness = _yandex_harness(monkeypatch)
    _install_auth_routes(
        harness,
        campaign=_campaign_response("FBY"),
        settings={
            "status": "OK",
            "result": {"settings": {"onlyDefaultPrice": True, "currency": "RUR"}},
        },
    )

    result = _test_yandex_auth(_yandex_fby_config(), "")

    assert result["currency_discovery"]["currencies"] == ["RUB"]


def test_yandex_auth_discovery_marks_refresh_failed_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth
    from tests.test_yandex_http import _http_error

    harness = _yandex_harness(monkeypatch)
    _install_auth_routes(
        harness,
        campaign=_campaign_response("FBY"),
        settings=_http_error(429, "rate limited"),
    )

    result = _test_yandex_auth(_yandex_fby_config(), "")

    # Business settings 读取失败不影响授权校验本身，只阻断币种就绪。
    assert result["business_id"] == "222"
    discovery = result["currency_discovery"]
    assert discovery["supported"] is True
    assert discovery["error_code"] == "YANDEX_RATE_LIMITED"
    assert discovery["error_message"]
    assert "currencies" not in discovery or not discovery.get("currencies")


def test_yandex_auth_discovery_flags_missing_currency_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness = _yandex_harness(monkeypatch)
    _install_auth_routes(
        harness,
        campaign=_campaign_response("FBY"),
        settings={"status": "OK", "result": {"settings": {"onlyDefaultPrice": False}}},
    )

    result = _test_yandex_auth(_yandex_fby_config(), "")

    assert result["currency_discovery"]["error_code"] == "YANDEX_CURRENCY_MISSING"


# ---------------------------------------------------------------------------
# Ozon：seller/info company.currency 锁定
# ---------------------------------------------------------------------------


def _patched_store_config(config: dict[str, Any]):
    return (
        patch.object(get_context().config, "load_store_config", return_value=config),
        patch.object(get_context().config, "save_store_config"),
    )


def test_ozon_auth_test_locks_company_currency() -> None:
    config: dict[str, Any] = {
        "ozon": {"client_id": "client-1", "api_key": "api-key", "shop_name": ""}
    }
    load_patch, save_patch = _patched_store_config(config)
    with (
        load_patch,
        save_patch as save_config,
        patch.object(
            store_credentials,
            "summarize_store_auth_states",
            return_value={"ozon": {"status": "测试成功"}},
        ),
        patch.object(
            publisher,
            "fetch_ozon_seller_info",
            return_value={"company": {"currency": "cny"}},
        ),
        patch.object(publisher, "fetch_ozon_shop_name", return_value="OZON-SHOP"),
    ):
        result = store_credentials.test_store_auth("ozon")

    assert result["ok"] is True
    assert result["publish_ready"] is True
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["listing_currency"] == "CNY"
    assert currency_configuration["allowed_currencies"] == ["CNY"]
    assert currency_configuration["currency_mode"] == "locked"
    assert currency_configuration["currency_status"] == "ready"
    assert currency_configuration["currency_source"] == "account_api"
    assert currency_configuration["currency_verified_at"]
    assert "storeConfig" in result and "storeAuthSummary" in result

    store = config["ozon"]
    assert store["listing_currency"] == "CNY"
    assert store["currency_status"] == "ready"
    assert store["currency_fingerprint"].startswith("sha256:")
    assert "contract_currency" not in store
    save_config.assert_called_once_with(config)


def test_ozon_auth_test_missing_currency_marks_refresh_failed() -> None:
    config: dict[str, Any] = {
        "ozon": {"client_id": "client-1", "api_key": "api-key", "shop_name": ""}
    }
    load_patch, save_patch = _patched_store_config(config)
    with (
        load_patch,
        save_patch,
        patch.object(
            store_credentials,
            "summarize_store_auth_states",
            return_value={"ozon": {"status": "测试成功"}},
        ),
        patch.object(
            publisher, "fetch_ozon_seller_info", return_value={"company": {}}
        ),
        patch.object(publisher, "fetch_ozon_shop_name", return_value="OZON-SHOP"),
    ):
        result = store_credentials.test_store_auth("ozon")

    # ok 与 publish_ready 分离：授权成功但币种未就绪。
    assert result["ok"] is True
    assert result["publish_ready"] is False
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_status"] == "refresh_failed"
    assert currency_configuration["currency_error_code"] == "OZON_CURRENCY_MISSING"
    assert config["ozon"]["listing_currency"] == ""
    assert config["ozon"]["currency_status"] == "refresh_failed"


def test_ozon_auth_preview_test_does_not_persist_currency() -> None:
    saved: dict[str, Any] = {"ozon": {"client_id": "", "api_key": "", "shop_name": ""}}
    load_patch, save_patch = _patched_store_config(saved)
    with (
        load_patch,
        save_patch as save_config,
        patch.object(
            store_credentials,
            "summarize_store_auth_states",
            return_value={"ozon": {}},
        ),
        patch.object(
            publisher,
            "fetch_ozon_seller_info",
            return_value={"company": {"currency": "CNY"}},
        ),
        patch.object(publisher, "fetch_ozon_shop_name", return_value="OZON-SHOP"),
    ):
        result = store_credentials.test_store_auth(
            "ozon",
            config_override={"ozon": {"client_id": "client-9", "api_key": "key-9"}},
        )

    assert result["ok"] is True
    assert result["preview"] is True
    assert result["publish_ready"] is True
    # preview 不落库：保存配置段里没有币种状态。
    save_config.assert_not_called()
    assert saved["ozon"].get("listing_currency", "") == ""


# ---------------------------------------------------------------------------
# Mercado Libre：CBT 市场映射与区域站点币种发现
# ---------------------------------------------------------------------------


def _run_mercadolibre_auth_test(
    *,
    profile: dict[str, Any] | Exception | None = None,
    site_listing: dict[str, Any] | Exception | None = None,
    marketplace_user: dict[str, Any] | Exception | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config: dict[str, Any] = {
        "mercadolibre": {
            "access_token": "ml-token",
            "site_id": "CBT",
            "shop_name": "",
        }
    }
    load_patch, save_patch = _patched_store_config(config)
    with (
        load_patch,
        save_patch,
        patch.object(
            store_credentials,
            "summarize_store_auth_states",
            return_value={"mercadolibre": {"status": "测试成功"}},
        ),
        patch.object(
            publisher,
            "fetch_mercadolibre_user_profile",
            side_effect=profile if isinstance(profile, Exception) else None,
            return_value=None if isinstance(profile, Exception) else profile,
        ),
        patch.object(
            publisher,
            "fetch_mercadolibre_site_listing",
            side_effect=site_listing if isinstance(site_listing, Exception) else None,
            return_value=(
                None if isinstance(site_listing, Exception) else site_listing
            ),
        ),
        patch.object(
            publisher,
            "fetch_mercadolibre_marketplace_user",
            side_effect=(
                marketplace_user if isinstance(marketplace_user, Exception) else None
            ),
            return_value=(
                None
                if isinstance(marketplace_user, Exception)
                else marketplace_user
                or {
                    "user_id": str(
                        profile.get("user_id") if isinstance(profile, dict) else ""
                    ),
                    "site_id": "CBT",
                    "marketplace_bindings": [
                        {
                            "seller_id": "991",
                            "site_id": "MLM",
                            "logistic_type": "remote",
                            "business_model": "",
                            "pricing_model": "",
                            "user_product": False,
                        }
                    ],
                }
            ),
        ),
    ):
        result = store_credentials.test_store_auth("mercadolibre")
    return result, config


def test_mercadolibre_auth_single_site_currency_is_locked() -> None:
    result, config = _run_mercadolibre_auth_test(
        profile={"user_id": "99", "nickname": "SHOP_MX", "site_id": "MLM"},
        site_listing={"id": "MLM", "currency": "MXN"},
    )

    assert result["publish_ready"] is True
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["listing_currency"] == "MXN"
    assert currency_configuration["currency_mode"] == "locked"
    assert currency_configuration["currency_source"] == "site_api"

    store = config["mercadolibre"]
    assert store["account_site_id"] == "MLM"
    assert store["user_id"] == "99"
    assert store["listing_currency"] == "MXN"
    assert store["currency_status"] == "ready"


def test_fetch_mercadolibre_marketplace_user_uses_bearer_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_request(method: str, url: str, token: str = "", **_: Any) -> dict[str, Any]:
        calls.append((method, url, token))
        return {
            "user_id": 99,
            "site_id": "cbt",
            "marketplaces": [
                {
                    "user_id": 991,
                    "site_id": "mlm",
                    "logistic_type": "REMOTE",
                },
                {
                    "user_id": 992,
                    "site_id": "mco",
                    "logistic_type": "FULFILLMENT",
                    "business_model": "CBT CN Fulfillment Managed",
                    "pricing_model": "NET_PROCEEDS",
                    "user_product": True,
                },
            ],
        }

    monkeypatch.setattr(config_http, "request_json", fake_request)

    result = config_http.fetch_mercadolibre_marketplace_user("99", "ml-token")

    assert calls == [
        (
            "GET",
            "https://api.mercadolibre.com/marketplace/users/99",
            "ml-token",
        )
    ]
    assert result == {
        "user_id": "99",
        "site_id": "CBT",
        "marketplace_bindings": [
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "",
                "pricing_model": "",
                "user_product": False,
            },
            {
                "seller_id": "992",
                "site_id": "MCO",
                "logistic_type": "fulfillment",
                "business_model": "CBT CN Fulfillment Managed",
                "pricing_model": "net_proceeds",
                "user_product": True,
            },
        ],
    }


def test_fetch_mercadolibre_marketplace_user_filters_parent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_http,
        "request_json",
        lambda *_args, **_kwargs: {
            "user_id": 99,
            "site_id": "CBT",
            "marketplaces": [
                {
                    "user_id": 99,
                    "site_id": "CBT",
                    "logistic_type": "remote",
                },
                {
                    "user_id": 991,
                    "site_id": "MLM",
                    "logistic_type": "remote",
                },
            ],
        },
    )

    result = config_http.fetch_mercadolibre_marketplace_user("99", "token")

    assert [item["site_id"] for item in result["marketplace_bindings"]] == ["MLM"]


def test_fetch_mercadolibre_site_listing_rejects_cbt_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Mock()
    monkeypatch.setattr(config_http, "request_json", request)

    with pytest.raises(
        PublishAdapterError,
        match="不能通过 /sites/CBT",
    ) as exc_info:
        config_http.fetch_mercadolibre_site_listing("cbt", "token")

    assert exc_info.value.code == "MERCADOLIBRE_SITE_SCOPE_INVALID"
    request.assert_not_called()


def test_mercadolibre_auth_multiple_currencies_require_selection() -> None:
    result, config = _run_mercadolibre_auth_test(
        profile={"user_id": "99", "nickname": "SHOP_MX", "site_id": "MLM"},
        site_listing={
            "id": "MLM",
            "currencies": [{"id": "USD"}, {"id": "MXN"}],
        },
    )

    assert result["ok"] is True
    assert result["publish_ready"] is False
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_mode"] == "selectable"
    assert currency_configuration["currency_status"] == "selection_required"
    assert currency_configuration["allowed_currencies"] == ["USD", "MXN"]
    assert config["mercadolibre"]["listing_currency"] == ""


def test_mercadolibre_cbt_uses_marketplace_bindings_and_contract_usd() -> None:
    marketplace_user = {
        "user_id": "99",
        "site_id": "CBT",
        "marketplace_bindings": [
            {
                "seller_id": "991",
                "site_id": "MCO",
                "logistic_type": "fulfillment",
                "business_model": "CBT CN Fulfillment Managed",
                "pricing_model": "net_proceeds",
                "user_product": True,
            }
        ],
    }
    store = {
        "access_token": "ml-token",
        "account_site_id": "CBT",
        "user_id": "99",
    }
    with (
        patch.object(
            publisher,
            "fetch_mercadolibre_marketplace_user",
            return_value=marketplace_user,
        ) as fetch_marketplace_user,
        patch.object(
            publisher, "fetch_mercadolibre_site_listing"
        ) as fetch_site,
    ):
        discovery = mercadolibre_auth.discover_mercadolibre_listing_currency(store)

    fetch_marketplace_user.assert_called_once_with("99", "ml-token")
    fetch_site.assert_not_called()
    assert discovery == {
        "supported": True,
        "currencies": ["USD"],
        "source": "global_selling_contract",
    }
    assert store["marketplace_bindings"] == marketplace_user["marketplace_bindings"]

    result, config = _run_mercadolibre_auth_test(
        profile={
            "user_id": "99",
            "nickname": "GLOBAL_STORE",
            "site_id": "CBT",
        },
        marketplace_user=marketplace_user,
    )
    assert result["ok"] is True
    assert result["publish_ready"] is True
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_mode"] == "locked"
    assert currency_configuration["currency_status"] == "ready"
    assert currency_configuration["listing_currency"] == "USD"
    assert currency_configuration["currency_source"] == "global_selling_contract"
    assert config["mercadolibre"]["marketplace_bindings"] == marketplace_user[
        "marketplace_bindings"
    ]


def test_mercadolibre_regional_site_request_carries_access_token() -> None:
    store = {
        "access_token": "ml-token",
        "account_site_id": "MLM",
        "marketplace_bindings": [{"site_id": "MCO"}],
    }
    with patch.object(
        publisher,
        "fetch_mercadolibre_site_listing",
        return_value={"id": "MLM", "currency": "MXN"},
    ) as fetch_site:
        discovery = mercadolibre_auth.discover_mercadolibre_listing_currency(store)

    fetch_site.assert_called_once_with("MLM", "ml-token")
    assert discovery == {
        "supported": True,
        "currencies": ["MXN"],
        "source": "site_api",
    }
    assert store["marketplace_bindings"] == []


def test_mercadolibre_cbt_mapping_failure_blocks_currency_readiness() -> None:
    result, config = _run_mercadolibre_auth_test(
        profile={"user_id": "99", "nickname": "GLOBAL_STORE", "site_id": "CBT"},
        marketplace_user=PublishAdapterError(
            "MERCADOLIBRE_SERVER_ERROR",
            "GET marketplace/users/99 failed: 503",
            retryable=True,
        ),
    )

    assert result["ok"] is True
    assert result["publish_ready"] is False
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_status"] == "refresh_failed"
    assert currency_configuration["currency_error_code"] == "MERCADOLIBRE_SERVER_ERROR"
    assert config["mercadolibre"]["marketplace_bindings"] == []


@pytest.mark.parametrize(
    "marketplace_user",
    [
        {
            "user_id": "another-user",
            "site_id": "CBT",
            "marketplace_bindings": [
                {
                    "seller_id": "991",
                    "site_id": "MLM",
                    "logistic_type": "remote",
                }
            ],
        },
        {
            "user_id": "99",
            "site_id": "MLM",
            "marketplace_bindings": [
                {
                    "seller_id": "991",
                    "site_id": "MLM",
                    "logistic_type": "remote",
                }
            ],
        },
    ],
)
def test_mercadolibre_cbt_rejects_mismatched_marketplace_parent(
    marketplace_user: dict[str, Any],
) -> None:
    store = {
        "access_token": "ml-token",
        "account_site_id": "CBT",
        "user_id": "99",
        "marketplace_bindings": [{"site_id": "MLB"}],
    }
    with patch.object(
        publisher,
        "fetch_mercadolibre_marketplace_user",
        return_value=marketplace_user,
    ):
        discovery = mercadolibre_auth.discover_mercadolibre_listing_currency(store)

    assert discovery["error_code"] == "MERCADOLIBRE_MARKETPLACE_PARENT_MISMATCH"
    assert store["marketplace_bindings"] == []


def test_mercadolibre_cbt_rejects_parent_site_as_sales_binding() -> None:
    store = {
        "access_token": "ml-token",
        "account_site_id": "CBT",
        "user_id": "99",
    }
    with patch.object(
        publisher,
        "fetch_mercadolibre_marketplace_user",
        return_value={
            "user_id": "99",
            "site_id": "CBT",
            "marketplace_bindings": [
                {
                    "seller_id": "99",
                    "site_id": "CBT",
                    "logistic_type": "remote",
                }
            ],
        },
    ):
        discovery = mercadolibre_auth.discover_mercadolibre_listing_currency(store)

    assert discovery["error_code"] == "MERCADOLIBRE_MARKETPLACE_BINDINGS_EMPTY"
    assert store["marketplace_bindings"] == []


def test_mercadolibre_cbt_bindings_and_fully_managed_fields_persist_to_auth_detail(
    tmp_path: Path,
) -> None:
    marketplace_user = {
        "user_id": "99",
        "site_id": "CBT",
        "marketplace_bindings": [
            {
                "seller_id": "992",
                "site_id": "MCO",
                "logistic_type": "fulfillment",
                "business_model": "CBT CN Fulfillment Managed",
                "pricing_model": "net_proceeds",
                "user_product": True,
            }
        ],
    }
    with temp_app_context(tmp_path):
        get_context().config.save_store_config(
            {"mercadolibre": {"access_token": "ml-token"}}
        )
        with (
            patch.object(
                publisher,
                "fetch_mercadolibre_user_profile",
                return_value={
                    "user_id": "99",
                    "nickname": "GLOBAL_STORE",
                    "site_id": "CBT",
                },
            ),
            patch.object(
                publisher,
                "fetch_mercadolibre_marketplace_user",
                return_value=marketplace_user,
            ),
            patch.object(
                publisher, "fetch_mercadolibre_site_listing"
            ) as fetch_site,
        ):
            result = store_credentials.test_store_auth("mercadolibre")

        fetch_site.assert_not_called()
        assert result["publish_ready"] is True
        auth_detail = get_context().db.get_store_auth("mercadolibre")["auth_detail"]
        assert auth_detail["marketplace_bindings"] == marketplace_user[
            "marketplace_bindings"
        ]
        assert auth_detail["listing_currency"] == "USD"
        assert auth_detail["currency_mode"] == "locked"
        assert auth_detail["currency_status"] == "ready"
        assert auth_detail["currency_source"] == "global_selling_contract"

        # 映射属于后端在线派生授权态，不得落到 store_config.json。
        static_config = publisher.load_store_config(
            get_context().paths.store_config_path
        )
        assert "marketplace_bindings" not in static_config["mercadolibre"]


def test_mercadolibre_auth_site_without_currency_requires_manual() -> None:
    result, _ = _run_mercadolibre_auth_test(
        profile={"user_id": "99", "nickname": "SHOP", "site_id": "MLA"},
        site_listing={"id": "MLA"},
    )

    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_mode"] == "manual"
    assert currency_configuration["currency_status"] == "manual_required"


def test_mercadolibre_auth_site_failure_marks_refresh_failed() -> None:
    result, config = _run_mercadolibre_auth_test(
        profile={"user_id": "99", "nickname": "SHOP", "site_id": "MLM"},
        site_listing=PublishAdapterError(
            "MERCADOLIBRE_SERVER_ERROR", "GET sites/MLM failed: 503", retryable=True
        ),
    )

    assert result["ok"] is True
    assert result["publish_ready"] is False
    currency_configuration = result["currency_configuration"]
    assert currency_configuration["currency_status"] == "refresh_failed"
    assert currency_configuration["currency_mode"] == "unresolved"
    assert config["mercadolibre"]["currency_error_code"] == "MERCADOLIBRE_SERVER_ERROR"


def test_mercadolibre_auth_users_me_failure_fails_auth_test() -> None:
    config: dict[str, Any] = {
        "mercadolibre": {"access_token": "ml-token", "shop_name": ""}
    }
    load_patch, save_patch = _patched_store_config(config)
    with (
        load_patch,
        save_patch,
        patch.object(
            store_credentials,
            "summarize_store_auth_states",
            return_value={"mercadolibre": {}},
        ),
        patch.object(
            publisher,
            "fetch_mercadolibre_user_profile",
            side_effect=PublishAdapterError(
                "MERCADOLIBRE_AUTH_FAILED", "GET users/me failed: 401"
            ),
        ),
    ):
        with pytest.raises(RuntimeError):
            store_credentials.test_store_auth("mercadolibre")

    # 授权失败：币种状态重置为 unresolved。
    assert config["mercadolibre"]["auth_status"] == "测试失败"
    assert config["mercadolibre"]["currency_status"] == "unresolved"
    assert config["mercadolibre"]["listing_currency"] == ""


# ---------------------------------------------------------------------------
# 人工选择接口（§9.2）
# ---------------------------------------------------------------------------


def _seed_currency_state(platform: str, auth_detail: dict[str, Any]) -> None:
    get_context().db.update_store_auth(platform, auth_detail=auth_detail)


def test_store_currency_manual_selection_normalizes_and_persists(tmp_path: Path) -> None:
    with temp_app_context(tmp_path):
        _seed_currency_state(
            "mercadolibre",
            {"currency_mode": "manual", "currency_status": "manual_required"},
        )

        payload, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "mercadolibre", "listing_currency": " usd "}
        )

        assert status == 200
        assert payload["ok"] is True
        assert payload["publish_ready"] is True
        assert payload["currencyConfiguration"]["listing_currency"] == "USD"
        assert payload["currencyConfiguration"]["currency_source"] == "manual"

        saved = get_context().config.load_store_config()["mercadolibre"]
        assert saved["listing_currency"] == "USD"
        assert saved["currency_mode"] == "manual"
        assert saved["currency_status"] == "ready"
        assert saved["currency_fingerprint"].startswith("sha256:")


def test_store_currency_manual_selection_rejects_invalid_iso(tmp_path: Path) -> None:
    with temp_app_context(tmp_path):
        _seed_currency_state(
            "mercadolibre",
            {"currency_mode": "manual", "currency_status": "manual_required"},
        )

        payload, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "mercadolibre", "listing_currency": "USDT"}
        )

        assert status == 400
        assert payload["ok"] is False


def test_store_currency_selection_locked_rejects_edit(tmp_path: Path) -> None:
    with temp_app_context(tmp_path):
        _seed_currency_state(
            "ozon",
            {
                "listing_currency": "CNY",
                "allowed_currencies": ["CNY"],
                "currency_mode": "locked",
                "currency_status": "ready",
                "currency_source": "account_api",
            },
        )

        payload, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "ozon", "listing_currency": "RUB"}
        )

        assert status == 400
        saved = get_context().config.load_store_config()["ozon"]
        assert saved["listing_currency"] == "CNY"


def test_store_currency_selection_selectable_requires_allowed_member(
    tmp_path: Path,
) -> None:
    with temp_app_context(tmp_path):
        _seed_currency_state(
            "mercadolibre",
            {
                "allowed_currencies": ["USD", "MXN"],
                "currency_mode": "selectable",
                "currency_status": "selection_required",
                "currency_source": "site_api",
            },
        )

        rejected, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "mercadolibre", "listing_currency": "BRL"}
        )
        assert status == 400

        accepted, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "mercadolibre", "listing_currency": "MXN"}
        )
        assert status == 200
        assert accepted["currencyConfiguration"]["listing_currency"] == "MXN"
        assert accepted["currencyConfiguration"]["currency_status"] == "ready"


def test_store_currency_selection_unresolved_rejected(tmp_path: Path) -> None:
    with temp_app_context(tmp_path):
        payload, status = auth_config_facade.store_currency_selection_payload(
            {"platform": "ozon", "listing_currency": "CNY"}
        )

        assert status == 400


# ---------------------------------------------------------------------------
# save-settings 信任边界（§9.3）
# ---------------------------------------------------------------------------


def test_sanitize_client_store_config_strips_derived_fields() -> None:
    sanitized = sanitize_client_store_config(
        {
            "ozon": {
                "client_id": "client-1",
                "api_key": "key-1",
                "category_id": "17028",
                "listing_currency": "USD",
                "allowed_currencies": ["USD"],
                "currency_mode": "locked",
                "currency_status": "ready",
                "currency_source": "spoofed",
                "currency_fingerprint": "sha256:fake",
                "auth_status": "测试成功",
                "account_site_id": "CBT",
                "marketplace_bindings": [
                    {
                        "seller_id": "forged",
                        "site_id": "MLM",
                        "logistic_type": "remote",
                    }
                ],
            },
            "listing": {"stock": "10", "currency_id": "MXN"},
        }
    )

    assert sanitized["ozon"] == {
        "client_id": "client-1",
        "api_key": "key-1",
        "category_id": "17028",
    }
    assert sanitized["listing"] == {"stock": "10"}


def test_save_settings_never_accepts_client_currency_state(tmp_path: Path) -> None:
    with temp_app_context(tmp_path):
        payload, status = auth_config_facade.save_settings_payload(
            {
                "storeConfig": {
                    "mercadolibre": {
                        "app_id": "app-1",
                        "app_secret": "secret-1",
                        "listing_currency": "USD",
                        "currency_status": "ready",
                        "currency_mode": "manual",
                        "allowed_currencies": ["USD"],
                        "auth_status": "测试成功",
                        "marketplace_bindings": [
                            {
                                "seller_id": "forged",
                                "site_id": "MLM",
                                "logistic_type": "remote",
                            }
                        ],
                    }
                }
            }
        )

        assert status == 200
        saved = get_context().config.load_store_config()["mercadolibre"]
        assert saved["app_id"] == "app-1"
        assert saved.get("listing_currency", "") == ""
        assert saved.get("currency_status", "") == ""
        assert saved.get("currency_mode", "") == ""
        assert saved.get("allowed_currencies", []) == []
        assert saved.get("marketplace_bindings", []) == []
        # 客户端伪造的“测试成功”被拒绝；有凭据未测试时为默认展示态。
        assert saved.get("auth_status", "") == "已保存，未测试"


# ---------------------------------------------------------------------------
# 预览测试与已保存配置测试的落库边界（修复“Yandex 币种不落库”）
# ---------------------------------------------------------------------------


def _stub_ozon_tester(monkeypatch: pytest.MonkeyPatch, currency: str) -> None:
    def fake_tester(config: dict[str, Any], scope: str) -> dict[str, Any]:
        store = config.setdefault("ozon", {})
        store["shop_name"] = "OZON-SHOP"
        store["auth_status"] = "测试成功"
        store["auth_checked_at"] = "2026-08-23T12:00:00Z"
        store["auth_masked_account"] = "client-1"
        return {
            "currency_discovery": {
                "supported": True,
                "currencies": [currency],
                "source": "account_api",
            }
        }

    monkeypatch.setattr(store_credentials, "_test_ozon_auth", fake_tester)


def test_preview_with_unchanged_credentials_persists_currency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 已保存凭据后，前端“测试授权并读取发布货币”会回传与已保存配置一致的
    # 凭据；后端必须把它当作已保存配置测试并落库币种，而不是 preview。
    with temp_app_context(tmp_path):
        get_context().config.save_store_config(
            {"ozon": {"client_id": "client-1", "api_key": "key-1"}}
        )
        _stub_ozon_tester(monkeypatch, "CNY")

        result = store_credentials.test_store_auth(
            "ozon",
            config_override={"ozon": {"client_id": "client-1", "api_key": "key-1"}},
        )

        assert result["ok"] is True
        assert result.get("preview") is not True
        assert result["publish_ready"] is True
        saved = get_context().config.load_store_config()["ozon"]
        assert saved["listing_currency"] == "CNY"
        assert saved["currency_status"] == "ready"
        assert saved["currency_mode"] == "locked"


def test_preview_with_changed_credentials_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 凭据被改动（未保存）时仍是 preview：只返回预览，不落库币种。
    with temp_app_context(tmp_path):
        get_context().config.save_store_config(
            {"ozon": {"client_id": "client-1", "api_key": "key-1"}}
        )
        _stub_ozon_tester(monkeypatch, "CNY")

        result = store_credentials.test_store_auth(
            "ozon",
            config_override={"ozon": {"client_id": "client-1", "api_key": "NEW-KEY"}},
        )

        assert result["ok"] is True
        assert result.get("preview") is True
        saved = get_context().config.load_store_config()["ozon"]
        assert saved.get("listing_currency", "") == ""
        assert saved.get("currency_status", "") == ""
