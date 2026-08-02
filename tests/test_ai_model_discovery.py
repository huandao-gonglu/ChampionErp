from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_web.services import ai_model_discovery, ai_provider_catalog


class _FakeProvider:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.closed = False

        class ModelsApi:
            async def list(inner_self, *, timeout: float):
                del inner_self
                assert timeout == 12.0
                if error is not None:
                    raise error
                return response

        self.client = SimpleNamespace(models=ModelsApi())

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self.closed = True


def test_model_discovery_dispatches_through_catalog_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider(
        response=SimpleNamespace(
            data=[
                SimpleNamespace(id="model-b"),
                SimpleNamespace(id="model-a"),
                SimpleNamespace(id="model-b"),
                SimpleNamespace(id=""),
            ]
        )
    )

    def create_provider(provider_id, *, base_url, api_key):
        assert provider_id == ai_provider_catalog.PROVIDER_ID_OPENAI
        assert base_url == "https://models.example.invalid/v1"
        assert api_key == "secret"
        return provider

    monkeypatch.setattr(
        ai_provider_catalog,
        "create_pydantic_provider",
        create_provider,
    )

    result = ai_model_discovery.list_remote_models(
        "openai",
        "https://models.example.invalid/v1",
        "secret",
        12,
    )

    assert result == [
        {"id": "model-b", "name": "model-b"},
        {"id": "model-a", "name": "model-a"},
    ]
    assert provider.closed is True


def test_model_discovery_sanitizes_sdk_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecretStatusError(RuntimeError):
        status_code = 401

    provider = _FakeProvider(error=SecretStatusError("secret response body"))
    monkeypatch.setattr(
        ai_provider_catalog,
        "create_pydantic_provider",
        lambda *args, **kwargs: provider,
    )

    with pytest.raises(
        ai_model_discovery.AiModelDiscoveryError,
        match=r"HTTP 401",
    ) as caught:
        ai_model_discovery.list_remote_models(
            "openai",
            "https://api.openai.com/v1",
            "secret",
            12,
        )

    assert "secret response body" not in str(caught.value)
    assert provider.closed is True
