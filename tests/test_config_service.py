from __future__ import annotations

import json
from pathlib import Path

from conftest import assert_no_old_path
from erp_web.services import ai_model_config, browser_ai_runtime, config_service


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
    assert public["model_quality_levels"] == ["fast", "balanced", "high_quality"]
    assert public["image_quality_options"] == ["auto", "low", "medium", "high"]
    assert "copy.generate" in public["ai_use_case_prompts"]
    assert "research.web_search" in public["ai_use_case_prompts"]
    assert public["ai_use_case_prompts"]["copy.generate"]["user_prompt"]
    assert public["ai_use_case_prompts"]["research.web_search"]["user_prompt"]
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


def test_merge_config_writes_ai_use_case_prompt_files(app_dir: Path) -> None:
    merged = config_service.merge_ai_config(
        app_dir,
        {},
        {
            "ai_use_case_prompts": {
                "copy.generate": {
                    "path": "config/prompts/copy_generate.json",
                    "description": "文案生成提示词",
                    "system_prompt": "System from settings",
                    "user_prompt": "User prompt {$language}",
                },
                "research.web_search": {
                    "path": "config/prompts/research_web_search.json",
                    "description": "AI 选品搜索默认模板",
                    "system_prompt": "Research system",
                    "user_prompt": "Research user {$marketId}",
                },
            }
        },
    )

    assert merged["ai_use_case_prompts"]["copy.generate"]["path"] == "config/prompts/copy_generate.json"
    assert merged["ai_use_case_prompts"]["research.web_search"]["path"] == "config/prompts/research_web_search.json"
    written = json.loads((app_dir / "config/prompts/copy_generate.json").read_text(encoding="utf-8"))
    assert written["description"] == "文案生成提示词"
    assert written["system"] == "System from settings"
    assert written["user"] == "User prompt {$language}"
    research_written = json.loads((app_dir / "config/prompts/research_web_search.json").read_text(encoding="utf-8"))
    assert research_written["description"] == "AI 选品搜索默认模板"
    assert research_written["system"] == "Research system"
    assert research_written["user"] == "Research user {$marketId}"


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


def test_merge_config_clears_saved_api_key_when_model_switches_to_cli(app_dir: Path) -> None:
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
                    "connection_type": "cli",
                    "provider": "Codex CLI",
                    "cli_tool": "codex",
                    "command": "codex",
                    "api_key": "",
                    "base_url": "",
                    "model": "",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
    )

    assert merged["ai_models"][0]["connection_type"] == "cli"
    assert merged["ai_models"][0]["api_key"] == ""
    assert merged["ai_models"][0]["command"] == "codex"


def test_merge_config_clears_saved_api_key_when_model_switches_to_browser(app_dir: Path) -> None:
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
                    "connection_type": "browser",
                    "provider": "浏览器 AI",
                    "browser_provider": "chatgpt",
                    "browser_profile": "default",
                    "api_key": "",
                    "base_url": "",
                    "model": "",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
    )

    assert merged["ai_models"][0]["connection_type"] == "browser"
    assert merged["ai_models"][0]["api_key"] == ""
    assert merged["ai_models"][0]["browser_provider"] == "chatgpt"
    assert merged["ai_models"][0]["browser_profile"] == "default"


def test_normalize_ai_model_keeps_quality_only_for_image_models() -> None:
    text_model = ai_model_config.normalize_ai_model(
        {
            "id": "text_model",
            "model": "gpt-5.5",
            "capabilities": ["chat", "json", "web_search"],
            "quality": "high",
            "size": "1024x1024",
            "timeout_seconds": "30",
        },
        2,
    )
    image_model = ai_model_config.normalize_ai_model(
        {
            "id": "image_model",
            "model": "gpt-image-1",
            "capabilities": ["image_generate"],
            "quality_level": "fast",
            "quality": "high",
            "size": "1024x1024",
        },
        2,
    )

    assert "quality" not in text_model
    assert "size" not in text_model
    assert text_model["quality_level"] == "high_quality"
    assert text_model["timeout_seconds"] == "30"
    assert image_model["quality_level"] == "fast"
    assert image_model["quality"] == "high"
    assert image_model["size"] == "1024x1024"


def test_normalize_ai_models_seeds_defaults_only_for_empty_config() -> None:
    seeded = ai_model_config.normalize_ai_models(None)

    assert seeded[0]["id"] == "default_text"
    assert seeded[0]["connection_type"] == "api"
    assert seeded[0]["provider"] == "DeepSeek"
    assert seeded[0]["model"] == "deepseek-chat"
    assert seeded[1]["id"] == "default_image"
    assert seeded[1]["connection_type"] == "api"
    assert seeded[1]["provider"] == "OpenAI"
    assert seeded[1]["model"] == "gpt-image-1"


def test_normalize_ai_model_does_not_inherit_positional_defaults() -> None:
    models = ai_model_config.normalize_ai_models(
        [
            {
                "id": "custom_text",
                "name": "自定义文本模型",
                "capabilities": ["chat", "json"],
            },
            {
                "id": "custom_image",
                "name": "自定义图片模型",
                "capabilities": ["image_generate"],
            },
        ]
    )

    assert models[0]["id"] == "custom_text"
    assert models[0]["provider"] == "OpenAI-Compatible"
    assert models[0]["base_url"] == ""
    assert models[0]["model"] == ""
    assert models[0]["model_env"] == ""
    assert models[1]["id"] == "custom_image"
    assert models[1]["provider"] == "OpenAI-Compatible"
    assert models[1]["base_url"] == ""
    assert models[1]["model"] == ""
    assert models[1]["quality_level"] == "balanced"


def test_normalize_ai_model_keeps_empty_capabilities_empty() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "untested_model",
            "name": "未测试模型",
            "capabilities": [],
        }
    )

    assert model["capabilities"] == []


def test_normalize_ai_model_supports_cli_connection() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "codex_cli_text",
            "name": "Codex CLI 文本模型",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "",
            "model": "",
            "api_key": "should-not-survive",
            "capabilities": ["chat", "json", "web_search", "image_generate", "image_edit"],
            "timeout_seconds": "180",
        },
        2,
    )

    assert model["connection_type"] == "cli"
    assert model["cli_tool"] == "codex"
    assert model["command"] == "codex"
    assert model["provider"] == "Codex CLI"
    assert model["model"] == ""
    assert model["api_key"] == ""
    assert model["capabilities"] == ["chat", "json", "web_search", "image_generate", "image_edit"]
    assert model["timeout_seconds"] == "180"
    assert model["sandbox"] == "read-only"


def test_normalize_cli_model_does_not_inherit_default_api_model_name() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "default_text",
            "name": "默认文本模型",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "codex",
            "model": "",
            "base_url": "",
            "api_key_env": "",
            "capabilities": ["chat", "json", "web_search"],
        },
        0,
    )

    assert model["connection_type"] == "cli"
    assert model["provider"] == "Codex CLI"
    assert model["model"] == ""
    assert model["model_env"] == ""
    assert model["base_url"] == ""
    assert model["api_key_env"] == ""
    assert model["capabilities"] == ["chat", "json", "web_search"]


def test_normalize_browser_model_does_not_inherit_default_api_fields() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "default_text",
            "name": "浏览器文本模型",
            "connection_type": "browser",
            "provider": "",
            "browser_provider": "",
            "browser_profile": "default",
            "browser_port": "9444",
            "model": "",
            "base_url": "https://api.deepseek.com",
            "api_key": "should-not-survive",
            "api_key_env": "DEEPSEEK_API_KEY",
            "capabilities": ["chat", "json", "web_search", "image_generate"],
        },
        0,
    )

    assert model["connection_type"] == "browser"
    assert model["provider"] == "浏览器 AI"
    assert model["browser_provider"] == "chatgpt"
    assert model["browser_mode"] == "managed_profile"
    assert model["browser_profile"] == "default"
    assert model["browser_port"] == "9444"
    assert model["model"] == ""
    assert model["model_env"] == ""
    assert model["base_url"] == ""
    assert model["api_key"] == ""
    assert model["api_key_env"] == ""
    assert model["capabilities"] == ["chat", "json", "web_search", "image_generate"]
    assert browser_ai_runtime.browser_ai_profile_dir("/tmp/champion", model) == Path("/tmp/champion/browser_profile/ai/chatgpt/default")


def test_public_ai_config_exposes_connection_types_and_cli_tools(app_dir: Path) -> None:
    public = config_service.public_ai_config(app_dir, {})

    assert public["connection_types"] == ["api", "cli", "browser"]
    assert public["browser_modes"] == ["managed_profile", "existing_browser"]
    assert any(tool["value"] == "codex" and tool["command"] == "codex" for tool in public["cli_tools"])
    assert "effective_capabilities" not in public["ai_models"][0]


def test_merge_config_copies_saved_model_key_from_source_model(app_dir: Path) -> None:
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
                },
                {
                    "id": "copy_model_copy",
                    "copy_source_id": "copy_model",
                    "provider": "DeepSeek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-reasoner",
                    "capabilities": ["chat", "json"],
                },
            ]
        },
    )

    assert merged["ai_models"][0]["api_key"] == "saved-key"
    assert merged["ai_models"][1]["api_key"] == "saved-key"
    assert merged["ai_models"][1]["model"] == "deepseek-reasoner"
    assert "copy_source_id" not in merged["ai_models"][1]


def test_normalize_app_config_migrates_legacy_ai_aliases(app_dir: Path) -> None:
    from erp_web import runtime as erp_web_app

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
    from erp_web import runtime as erp_web_app

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


def test_normalize_app_config_keeps_yunexpress_credentials() -> None:
    from erp_web import runtime as erp_web_app

    saved = erp_web_app.normalize_app_config(
        {
            "yunexpress": {
                "environment": "production",
                "base_url": "https://openapi.example.test",
                "app_id": "app-id-123456",
                "app_secret": "secret-abcdef",
                "source_key": "source-key-xyz",
                "product_code": "S1002",
                "label_type": "pdf",
                "weight_unit": "kg",
                "size_unit": "cm",
                "timeout_seconds": "30",
            }
        }
    )

    assert saved["yunexpress"]["environment"] == "production"
    assert saved["yunexpress"]["base_url"] == "https://openapi.example.test"
    assert saved["yunexpress"]["app_id"] == "app-id-123456"
    assert saved["yunexpress"]["app_secret"] == "secret-abcdef"
    assert saved["yunexpress"]["source_key"] == "source-key-xyz"
    assert saved["yunexpress"]["product_code"] == "S1002"
    assert saved["yunexpress"]["label_type"] == "PDF"
    assert saved["yunexpress"]["weight_unit"] == "KG"
    assert saved["yunexpress"]["size_unit"] == "CM"
    assert saved["yunexpress"]["masked_app_secret"].startswith("secr")
    assert saved["yunexpress"]["status"] == "已配置"


def test_normalize_app_config_migrates_legacy_nested_ai_sections(app_dir: Path) -> None:
    from erp_web import runtime as erp_web_app

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
    from erp_web import runtime as erp_web_app

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
