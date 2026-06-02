from __future__ import annotations

from pathlib import Path

from conftest import assert_no_old_path
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
