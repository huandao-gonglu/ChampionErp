from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from conftest import assert_no_old_path
import erp_web_app
from services import config_service, copy_service


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
        {"text_ai": {"platform": "DeepSeek", "api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}},
        target_market="mercadolibre",
        language="Spanish (Mexico)",
    )

    assert result["ok"] is True
    assert result["warning"]
    assert "API Key" in result["warning"]
    assert result["target_market"] == "mercadolibre"
    assert result["language"] == "Spanish (Mexico)"
    assert result["provider"] == "DeepSeek"
    assert result["nvidia_deprecated"] is True
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


def test_ai_config_hides_nvidia_and_does_not_hardcode_keys(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    cfg = config_service.ai_config_from_sources(app_dir, {"text_ai": {"platform": "NVIDIA", "api_key": ""}})
    assert cfg["text_ai"]["platform"] == "DeepSeek"
    assert "NVIDIA" in cfg["providers"]["deprecated"]
    source = (app_dir / "services" / "copy_service.py").read_text(encoding="utf-8", errors="ignore")
    assert "sk-" not in source
    assert_no_old_path(source, old_path_markers)


def test_ai_channel_accepts_nested_text_ai_config() -> None:
    calls: list[dict] = []

    class Models:
        @staticmethod
        def list() -> list:
            return []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.models = Models()

    with patch.dict("sys.modules", {"openai": type("OpenAIModule", (), {"OpenAI": FakeOpenAI})}):
        result = erp_web_app.test_ai_channel(
            "text",
            {
                "text_ai": {
                    "platform": "DeepSeek",
                    "api_key": "test-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                }
            },
        )

    assert result["ok"] is True
    assert calls == [{"api_key": "test-key", "base_url": "https://api.deepseek.com"}]


def test_assign_upc_writes_current_product_and_returns_full_payload(tmp_path: Path) -> None:
    original = {
        "APP_DIR": erp_web_app.APP_DIR,
        "DATA_DIR": erp_web_app.DATA_DIR,
        "APP_CONFIG_PATH": erp_web_app.APP_CONFIG_PATH,
        "DIST_APP_CONFIG_PATH": erp_web_app.DIST_APP_CONFIG_PATH,
    }
    try:
        erp_web_app.APP_DIR = tmp_path
        erp_web_app.DATA_DIR = tmp_path / "data"
        erp_web_app.APP_CONFIG_PATH = tmp_path / "app_config.json"
        erp_web_app.DIST_APP_CONFIG_PATH = tmp_path / "dist" / "app_config.json"
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
