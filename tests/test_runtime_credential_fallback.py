from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.facades import collect_facade
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
    monkeypatch.setattr(source_collect_1688_api, "alibaba_runtime_config", lambda store: saved)
    public = config_service.public_app_config(tmp_path, {"1688_api": saved})
    assert "1688_api" not in public
    for incoming in ({}, {"app_key": "injected", "access_token": "other", "base_url": "https://attacker.test"}):
        resolved = source_collect_1688_api.resolve_1688_api_config(incoming)
        assert resolved == saved | {"sign_method": "md5"}
