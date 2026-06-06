from __future__ import annotations

from pathlib import Path

from conftest import assert_no_old_path
from services import config_service


def test_config_paths_are_project_local(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    cfg_dir = config_service.config_dir(app_dir)
    env_file = config_service.env_path(app_dir)

    assert cfg_dir.resolve().is_relative_to((app_dir / "config").resolve())
    assert env_file.parent == cfg_dir
    assert_no_old_path(cfg_dir, old_path_markers)
    assert_no_old_path(env_file, old_path_markers)


def test_default_env_template_and_public_config(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    path = config_service.write_env_template(app_dir)
    public = config_service.public_ai_config(app_dir, {})

    assert path.exists()
    assert "api_key" not in public["text_ai"]
    assert "api_key" not in public["image_ai"]
    assert "api_key_configured" in public["text_ai"]
    assert public["storage"]["config_dir"].startswith(str(app_dir / "config"))
    assert_no_old_path(public, old_path_markers)


def test_merge_config_reads_key_from_config_not_code(app_dir: Path) -> None:
    merged = config_service.merge_ai_config(
        app_dir,
        {},
        {"text_ai": {"platform": "DeepSeek", "api_key": "test-key", "model": "deepseek-chat"}},
    )
    cfg = config_service.ai_config_from_sources(app_dir, merged)

    assert cfg["text_ai"]["api_key"] == "test-key"
    assert cfg["text_ai"]["model"] == "deepseek-chat"


def test_normalize_app_config_ignores_legacy_ai_aliases(app_dir: Path) -> None:
    import erp_web_app

    saved = erp_web_app.normalize_app_config(
        {
            "api_provider": "DeepSeek",
            "deepseek_api_key": "legacy-text-key",
            "deepseek_base_url": "https://legacy.deepseek.example",
            "text_ai_api_key": "legacy-text-key-2",
            "openai_api_key": "legacy-image-key",
            "openai_base_url": "https://legacy.openai.example/v1",
            "image_ai_api_key": "legacy-image-key-2",
        }
    )

    assert saved["text_ai"]["api_key"] == ""
    assert saved["text_ai"]["base_url"] == "https://api.deepseek.com"
    assert saved["image_ai"]["api_key"] == ""
    assert saved["image_ai"]["base_url"] == "https://api.openai.com/v1"
    for key in ("api_provider", "deepseek_api_key", "text_ai_api_key", "openai_api_key", "image_ai_api_key"):
        assert key not in saved


def test_ai_config_from_sources_ignores_legacy_aliases(app_dir: Path) -> None:
    cfg = config_service.ai_config_from_sources(
        app_dir,
        {
            "deepseek_api_key": "legacy-text-key",
            "text_ai_api_key": "legacy-text-key-2",
            "openai_api_key": "legacy-image-key",
            "image_ai_api_key": "legacy-image-key-2",
        },
    )

    assert cfg["text_ai"]["api_key"] == ""
    assert cfg["image_ai"]["api_key"] == ""
