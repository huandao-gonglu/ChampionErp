from __future__ import annotations

from pathlib import Path

from conftest import assert_no_old_path
from services import ai_model_config, config_service


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
    assert public["ai_models"]
    assert all("api_key" not in model for model in public["ai_models"])
    assert all("api_key_configured" in model for model in public["ai_models"])
    assert "copy.generate" in {item["id"] for item in public["ai_use_cases"]}
    assert public["storage"]["config_dir"].startswith(str(app_dir / "config"))
    assert_no_old_path(public, old_path_markers)


def test_merge_config_reads_key_from_config_not_code(app_dir: Path) -> None:
    merged = config_service.merge_ai_config(
        app_dir,
        {},
        {
            "ai_models": [
                {
                    "id": "copy_model",
                    "provider": "DeepSeek",
                    "api_key": "test-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ],
            "ai_use_case_bindings": {"copy.generate": {"model_id": "copy_model"}},
        },
    )
    cfg = ai_model_config.resolve_ai_model(merged, "copy.generate")

    assert cfg["api_key"] == "test-key"
    assert cfg["model"] == "deepseek-chat"


def test_merge_config_preserves_existing_model_key_when_public_payload_is_blank(app_dir: Path) -> None:
    current = {
        "ai_models": [
            {
                "id": "copy_model",
                "provider": "DeepSeek",
                "api_key": "saved-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "capabilities": ["chat", "json"],
            }
        ]
    }
    merged = config_service.merge_ai_config(
        app_dir,
        current,
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
    )

    assert merged["ai_models"][0]["api_key"] == "saved-key"


def test_normalize_app_config_migrates_legacy_ai_aliases(app_dir: Path) -> None:
    import erp_web_app

    saved = erp_web_app.normalize_app_config(
        {
            "api_provider": "DeepSeek",
            "deepseek_api_key": "legacy-text-key",
            "deepseek_base_url": "https://legacy.deepseek.example",
            "deepseek_model": "legacy-text-model",
            "text_ai_api_key": "legacy-text-key-2",
            "openai_api_key": "legacy-image-key",
            "openai_base_url": "https://legacy.openai.example/v1",
            "openai_model": "legacy-image-model",
            "image_ai_api_key": "legacy-image-key-2",
        }
    )

    assert saved["ai_models"][0]["api_key"] == "legacy-text-key-2"
    assert saved["ai_models"][0]["base_url"] == "https://legacy.deepseek.example"
    assert saved["ai_models"][0]["model"] == "legacy-text-model"
    assert saved["ai_models"][1]["api_key"] == "legacy-image-key-2"
    assert saved["ai_models"][1]["base_url"] == "https://legacy.openai.example/v1"
    assert saved["ai_models"][1]["model"] == "legacy-image-model"
    for key in ("api_provider", "deepseek_api_key", "text_ai_api_key", "openai_api_key", "image_ai_api_key"):
        assert key not in saved


def test_normalize_app_config_keeps_1688_api_credentials() -> None:
    import erp_web_app

    saved = erp_web_app.normalize_app_config(
        {
            "1688_api": {
                "app_key": "app-key-123456",
                "app_secret": "secret-abcdef",
                "access_token": "token-xyz",
                "base_url": "https://example.test/openapi",
                "method": "alibaba.product.get",
                "api_version": "1.0",
                "timeout_seconds": "30",
            }
        }
    )

    assert "enabled" not in saved["1688_api"]
    assert saved["1688_api"]["app_key"] == "app-key-123456"
    assert saved["1688_api"]["app_secret"] == "secret-abcdef"
    assert saved["1688_api"]["access_token"] == "token-xyz"
    assert saved["1688_api"]["base_url"] == "https://example.test/openapi"
    assert saved["1688_api"]["masked_app_secret"].startswith("secr")


def test_normalize_app_config_migrates_legacy_nested_ai_sections(app_dir: Path) -> None:
    import erp_web_app

    cfg = erp_web_app.normalize_app_config(
        {
            "text_ai": {
                "platform": "DeepSeek",
                "api_key": "legacy-text-key",
                "base_url": "https://legacy.deepseek.example",
                "model": "legacy-text-model",
            },
            "image_ai": {
                "platform": "OpenAI",
                "api_key": "legacy-image-key",
                "base_url": "https://legacy.openai.example/v1",
                "model": "legacy-image-model",
                "quality": "high",
            },
        }
    )

    assert cfg["ai_models"][0]["api_key"] == "legacy-text-key"
    assert cfg["ai_models"][0]["base_url"] == "https://legacy.deepseek.example"
    assert cfg["ai_models"][0]["model"] == "legacy-text-model"
    assert cfg["ai_models"][1]["api_key"] == "legacy-image-key"
    assert cfg["ai_models"][1]["base_url"] == "https://legacy.openai.example/v1"
    assert cfg["ai_models"][1]["model"] == "legacy-image-model"
    assert cfg["ai_models"][1]["quality"] == "high"
    assert "text_ai" not in cfg
    assert "image_ai" not in cfg


def test_normalize_app_config_uses_legacy_key_without_overwriting_canonical_model(app_dir: Path) -> None:
    import erp_web_app

    cfg = erp_web_app.normalize_app_config(
        {
            "ai_models": [
                {
                    "id": "default_text",
                    "provider": "New Provider",
                    "api_key": "",
                    "base_url": "https://new.example/v1",
                    "model": "new-model",
                    "capabilities": ["chat", "json"],
                }
            ],
            "text_ai_api_key": "legacy-text-key",
            "deepseek_base_url": "https://legacy.deepseek.example",
            "deepseek_model": "legacy-text-model",
        }
    )

    assert cfg["ai_models"][0]["api_key"] == "legacy-text-key"
    assert cfg["ai_models"][0]["base_url"] == "https://new.example/v1"
    assert cfg["ai_models"][0]["model"] == "new-model"
    assert "text_ai_api_key" not in cfg
