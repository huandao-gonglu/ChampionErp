from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.facades import collect_facade, logistics_facade
from erp_web.runtime_units import source_collect_1688_api
from erp_web.services import config_service


def test_collect_cookie_uses_saved_value_when_request_is_empty_or_masked(
    monkeypatch,
) -> None:
    saved_cookie = "saved-cookie-private-value"
    captured: list[str] = []

    monkeypatch.setattr(
        get_context().config,
        "load_app_config",
        lambda: {"alibaba_cookie": saved_cookie},
    )
    monkeypatch.setattr(
        get_context().products,
        "load_products_index",
        lambda: [],
    )

    def fake_collect(
        _url: str,
        _mode: str,
        cookie: str,
        _platform: str,
        _platforms: list[str] | None,
        _api_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        captured.append(cookie)
        return {"ok": True}

    monkeypatch.setattr(
        collect_facade,
        "collect_source_product",
        fake_collect,
    )
    masked_cookie = config_service.mask_secret(saved_cookie)

    for body in (
        {"url": "https://example.test/empty"},
        {"url": "https://example.test/masked", "cookie": masked_cookie},
    ):
        result, status = collect_facade.collect_source_payload(body)
        assert status == 200
        assert result["ok"] is True

    assert captured == [saved_cookie, saved_cookie]
    assert masked_cookie not in captured


def test_1688_config_uses_saved_truth_instead_of_public_masks(
    monkeypatch,
    tmp_path,
) -> None:
    saved = {
        "app_key": "saved-1688-app-key",
        "app_secret": "saved-1688-app-secret",
        "access_token": "saved-1688-access-token",
        "base_url": "https://saved.example.test/openapi",
        "method": "alibaba.product.get",
        "api_version": "1.0",
        "timeout_seconds": "30",
    }
    monkeypatch.setattr(
        get_context().config,
        "load_app_config",
        lambda: {"1688_api": saved},
    )
    public = config_service.public_app_config(
        tmp_path,
        {"1688_api": saved},
    )["1688_api"]

    resolved_after_refresh = source_collect_1688_api.resolve_1688_api_config({})
    resolved_from_public_form = (
        source_collect_1688_api.resolve_1688_api_config(public)
    )

    for resolved in (resolved_after_refresh, resolved_from_public_form):
        assert resolved["app_key"] == saved["app_key"]
        assert resolved["app_secret"] == saved["app_secret"]
        assert resolved["access_token"] == saved["access_token"]
    assert resolved_from_public_form["app_secret"] != public["app_secret"]


def test_yunexpress_test_uses_saved_truth_instead_of_public_masks(
    monkeypatch,
    tmp_path,
) -> None:
    saved = {
        "environment": "sandbox",
        "base_url": "https://openapi-sbx.yunexpress.cn",
        "app_id": "saved-yun-app-id",
        "app_secret": "saved-yun-app-secret",
        "source_key": "saved-yun-source-key",
    }
    captured: dict[str, Any] = {}

    class FakeYunExpressClient:
        def __init__(self, config: dict[str, Any]) -> None:
            captured.update(config)

        def request_access_token(self) -> dict[str, Any]:
            return {"access_token": "transient-token", "expires_in": 7200}

    monkeypatch.setattr(
        get_context().config,
        "load_app_config",
        lambda: {"yunexpress": saved},
    )
    monkeypatch.setattr(
        logistics_facade,
        "YunExpressClient",
        FakeYunExpressClient,
    )
    public = config_service.public_app_config(
        tmp_path,
        {"yunexpress": saved},
    )["yunexpress"]

    result = logistics_facade.test_yunexpress_config(public)

    assert result["ok"] is True
    assert captured["app_id"] == saved["app_id"]
    assert captured["app_secret"] == saved["app_secret"]
    assert captured["source_key"] == saved["source_key"]
    assert captured["app_secret"] != public["app_secret"]
