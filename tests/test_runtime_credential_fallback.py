from __future__ import annotations

from erp_web.runtime_units import source_collect_1688_api
from erp_web.services import config_service


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
        assert resolved == saved
