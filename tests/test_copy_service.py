from __future__ import annotations

import io
import json
from pathlib import Path
import urllib.error

from conftest import assert_no_old_path
import erp_web_app
import erp_web.runtime as erp_runtime
from services import ai_gateway, ai_model_config, copy_service


def test_generate_copy_without_api_key_returns_warning(app_dir: Path) -> None:
    product = {
        "name": "Manual test organizer",
        "materials": ["PP"],
        "selling_points": ["Foldable", "For home storage"],
        "source_text": "Use for organizing household items.",
    }
    result = copy_service.generate_copy(
        str(app_dir),
        product,
        {
            "ai_models": [
                {
                    "id": "copy_model",
                    "provider": "DeepSeek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
        target_market="mercadolibre",
        language="Spanish (Mexico)",
    )

    assert result["ok"] is True
    assert result["warning"]
    assert "API Key" in result["warning"]
    assert result["target_market"] == "mercadolibre"
    assert result["language"] == "Spanish (Mexico)"
    assert result["provider"] == "DeepSeek"
    assert result["copy"]["title"]


def test_prompt_contains_manual_product_data_and_mexico_spanish() -> None:
    prompt = copy_service.build_copy_prompt(
        {
            "name": "Manual organizer",
            "materials": ["PP"],
            "selling_points": ["For kitchen", "Reusable"],
            "source_text": "Manual text only, no crawler required.",
        },
        "mercadolibre",
        "Spanish (Mexico)",
        "rewrite",
    )

    assert "Spanish (Mexico)" in prompt
    assert "Mercado Libre Mexico" in prompt
    assert "Manual organizer" in prompt
    assert "Manual text only" in prompt


def test_copy_service_does_not_hardcode_keys(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    source = (app_dir / "services" / "copy_service.py").read_text(encoding="utf-8", errors="ignore")
    assert "sk-" not in source
    assert_no_old_path(source, old_path_markers)


def test_ai_model_connection_uses_model_config(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"deepseek-chat"},{"id":"deepseek-reasoner"}]}'

    def fake_urlopen(request, timeout):
        calls.append({
            "url": request.full_url,
            "auth": request.get_header("Authorization"),
            "ua": request.get_header("User-agent") or request.get_header("User-Agent"),
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
            "probe_capabilities": False,
        },
    )

    assert result["ok"] is True
    assert result["available_models"] == [
        {"id": "deepseek-chat", "label": "deepseek-chat"},
        {"id": "deepseek-reasoner", "label": "deepseek-reasoner"},
    ]
    assert calls == [{
        "url": "https://api.deepseek.com/models",
        "auth": "Bearer test-key",
        "ua": ai_model_config.AI_HTTP_USER_AGENT,
        "timeout": 60,
    }]


def test_ai_model_connection_uses_saved_key_when_public_payload_omits_secret(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"deepseek-chat"}]}'

    def fake_urlopen(request, timeout):
        calls.append({
            "url": request.full_url,
            "auth": request.get_header("Authorization"),
            "ua": request.get_header("User-agent") or request.get_header("User-Agent"),
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(
        erp_runtime,
        "load_app_config",
        lambda: {
            "ai_models": [
                {
                    "id": "copy_model",
                    "name": "Copy Model",
                    "provider": "DeepSeek",
                    "api_key": "saved-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
    )
    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "",
            "api_key_configured": True,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
            "probe_capabilities": False,
        },
    )

    assert result["ok"] is True
    assert calls == [{
        "url": "https://api.deepseek.com/models",
        "auth": "Bearer saved-key",
        "ua": ai_model_config.AI_HTTP_USER_AGENT,
        "timeout": 60,
    }]


def test_ai_model_probe_reports_unsupported_capabilities(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request, timeout):
        calls.append({"url": request.full_url, "timeout": timeout})
        if request.full_url.endswith("/models"):
            return FakeResponse(b'{"data":[{"id":"deepseek-chat"}]}')
        body = json.loads((request.data or b"{}").decode("utf-8"))
        if body.get("response_format"):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b"unsupported response_format"),
            )
        return FakeResponse(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
        },
    )

    assert result["ok"] is True
    assert result["supported_capabilities"] == ["chat"]
    assert result["unsupported_capabilities"] == ["json"]
    assert result["capability_results"]["json"]["ok"] is False
    assert calls == [
        {"url": "https://api.deepseek.com/models", "timeout": 60},
        {"url": "https://api.deepseek.com/chat/completions", "timeout": 60},
        {"url": "https://api.deepseek.com/chat/completions", "timeout": 60},
    ]


def test_assign_upc_writes_current_product_and_returns_full_payload(tmp_path: Path) -> None:
    original = {
        "APP_DIR": erp_web_app.APP_DIR,
        "DATA_DIR": erp_web_app.DATA_DIR,
        "CONFIG_DIR": erp_web_app.CONFIG_DIR,
        "APP_CONFIG_PATH": erp_web_app.APP_CONFIG_PATH,
        "LEGACY_APP_CONFIG_PATHS": erp_web_app.LEGACY_APP_CONFIG_PATHS,
    }
    try:
        erp_web_app.APP_DIR = tmp_path
        erp_web_app.DATA_DIR = tmp_path / "data"
        erp_web_app.CONFIG_DIR = tmp_path / "config"
        erp_web_app.APP_CONFIG_PATH = erp_web_app.CONFIG_DIR / "app_config.json"
        erp_web_app.LEGACY_APP_CONFIG_PATHS = (tmp_path / "app_config.json", tmp_path / "dist" / "app_config.json")
        (tmp_path / "upc_pool.json").write_text('{"values":["725272000007"],"used":[]}', encoding="utf-8")
        erp_web_app.save_product({"name": "UPC test product", "drafts": {"mercadolibre": {"enabled": True}}})

        result = erp_web_app.assign_upc()

        assert result["ok"] is True
        assert result["upc"] == "725272000007"
        assert result["product"]["upc"] == "725272000007"
        assert result["product"]["drafts"]["mercadolibre"]["upc"] == "725272000007"
        assert isinstance(result["productsIndex"], list)
    finally:
        for name, value in original.items():
            setattr(erp_web_app, name, value)
