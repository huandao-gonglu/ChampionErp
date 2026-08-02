from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import assert_no_old_path
from erp_web import app_config
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
    assert "model_quality_levels" not in public
    assert public["image_quality_options"] == ["auto", "low", "medium", "high"]
    assert {item["id"] for item in public["providers"]} == {
        "openai",
        "deepseek",
        "alibaba",
    }
    assert all(item["supported_api_styles"] for item in public["providers"])
    assert all(item["model_discovery"] in {"openai_models", "manual"} for item in public["providers"])
    assert all("generation_capabilities" in model for model in public["ai_models"])
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
                    "provider_id": "deepseek",
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


def test_ai_model_config_requires_explicit_provider_id() -> None:
    legacy = ai_model_config.normalize_ai_model(
        {
            "id": "legacy-deepseek",
            "provider": "DeepSeek",
            "provider_family": "generic_openai",
            "model": "deepseek-chat",
        }
    )
    qwen = ai_model_config.normalize_ai_model(
        {
            "id": "qwen",
            "provider_id": "alibaba",
            "model": "qwen-plus",
        }
    )

    assert legacy["provider_id"] == ""
    assert legacy["provider"] == ""
    assert legacy["base_url"] == ""
    assert qwen["provider_id"] == "alibaba"
    assert qwen["provider"] == "阿里云百炼 / Qwen"
    assert qwen["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert "provider_family" not in legacy
    assert "provider_family" not in qwen


def test_merge_ai_config_rejects_provider_outside_catalog(app_dir: Path) -> None:
    with pytest.raises(ValueError, match="未接入的 AI Provider"):
        config_service.merge_ai_config(
            app_dir,
            {},
            {
                "ai_models": [
                    {
                        "id": "unknown-provider",
                        "provider_id": "made_up_provider",
                        "base_url": "https://models.example.invalid/v1",
                        "model": "model",
                        "capabilities": ["chat"],
                    }
                ]
            },
        )


def test_merge_ai_config_rejects_api_model_without_provider_id(app_dir: Path) -> None:
    with pytest.raises(ValueError, match="未接入的 AI Provider：empty"):
        config_service.merge_ai_config(
            app_dir,
            {},
            {
                "ai_models": [
                    {
                        "id": "legacy-provider-fields",
                        "provider": "OpenAI-Compatible",
                        "provider_family": "generic_openai",
                        "base_url": "https://models.example.invalid/v1",
                        "model": "example-model",
                        "capabilities": ["chat"],
                    }
                ]
            },
        )


def test_merge_ai_config_rejects_unsupported_provider_protocol(app_dir: Path) -> None:
    with pytest.raises(ValueError, match="不支持 API 协议 openai_responses"):
        config_service.merge_ai_config(
            app_dir,
            {},
            {
                "ai_models": [
                    {
                        "id": "deepseek-responses",
                        "provider_id": "deepseek",
                        "api_style": "openai_responses",
                        "model": "deepseek-chat",
                        "capabilities": ["chat"],
                    }
                ]
            },
        )


def test_ai_use_case_binding_keeps_timeout_override_and_legacy_model_id() -> None:
    bindings = ai_model_config.normalize_ai_use_case_bindings(
        {
            "copy.generate": {
                "model_id": "copy_model",
                "timeout_override_seconds": "125",
            },
            "category.attribute_fill": "category_model",
        }
    )

    assert bindings["copy.generate"] == {
        "model_id": "copy_model",
        "timeout_override_seconds": 125,
    }
    assert bindings["category.attribute_fill"] == {"model_id": "category_model"}


def test_ai_use_case_binding_normalizes_generation_overrides() -> None:
    bindings = ai_model_config.normalize_ai_use_case_bindings(
        {
            "category.attribute_translation": {
                "model_id": "qwen_model",
                "generation": {
                    "temperature": "0",
                    "max_output_tokens": "3000",
                    "reasoning": {"mode": "disabled"},
                },
            }
        }
    )

    assert bindings["category.attribute_translation"] == {
        "model_id": "qwen_model",
        "generation": {
            "temperature": 0.0,
            "max_output_tokens": 3000,
            "reasoning": {"mode": "disabled"},
        },
    }


def test_merge_ai_config_rejects_reasoning_for_unmapped_deepseek_profile(app_dir: Path) -> None:
    with pytest.raises(ValueError, match="无法安全转换推理参数"):
        config_service.merge_ai_config(
            app_dir,
            {},
            {
                "ai_models": [
                    {
                        "id": "deepseek_model",
                        "connection_type": "api",
                        "provider": "DeepSeek",
                        "provider_id": "deepseek",
                        "model": "deepseek-reasoner",
                        "capabilities": ["chat", "json"],
                    }
                ],
                "ai_use_case_bindings": {
                    "category.attribute_translation": {
                        "model_id": "deepseek_model",
                        "generation": {"reasoning": {"mode": "disabled"}},
                    }
                },
            },
        )


def test_merge_config_writes_ai_use_case_prompt_files(tmp_path: Path) -> None:
    merged = config_service.merge_ai_config(
        tmp_path,
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
    written = json.loads((tmp_path / "config/prompts/copy_generate.json").read_text(encoding="utf-8"))
    assert written["description"] == "文案生成提示词"
    assert written["system"] == "System from settings"
    assert written["user"] == "User prompt {$language}"
    research_written = json.loads((tmp_path / "config/prompts/research_web_search.json").read_text(encoding="utf-8"))
    assert research_written["description"] == "AI 选品搜索默认模板"
    assert research_written["system"] == "Research system"
    assert research_written["user"] == "Research user {$marketId}"


def test_merge_config_preserves_existing_model_key_when_public_payload_is_blank(app_dir: Path) -> None:
    current = {
        "ai_models": [
            {
                "id": "copy_model",
                "provider": "DeepSeek",
                "provider_id": "deepseek",
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
                    "provider_id": "deepseek",
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
            "quality": "high",
            "size": "1024x1024",
        },
        2,
    )

    assert "quality" not in text_model
    assert "size" not in text_model
    assert "quality_level" not in text_model
    assert text_model["timeout_seconds"] == "30"
    assert "quality_level" not in image_model
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
    assert models[0]["provider"] == ""
    assert models[0]["provider_id"] == ""
    assert models[0]["base_url"] == ""
    assert models[0]["model"] == ""
    assert models[0]["model_env"] == ""
    assert models[1]["id"] == "custom_image"
    assert models[1]["provider"] == ""
    assert models[1]["provider_id"] == ""
    assert models[1]["base_url"] == ""
    assert models[1]["model"] == ""
    assert "quality_level" not in models[1]


def test_normalize_ai_model_keeps_empty_capabilities_empty() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "untested_model",
            "name": "未测试模型",
            "capabilities": [],
        }
    )

    assert model["capabilities"] == []


def test_normalize_ai_model_keeps_strategy_profiles_without_wire_payload() -> None:
    model = ai_model_config.normalize_ai_model(
        {
            "id": "responses_model",
            "api_style": "openai_responses",
            "capabilities": ["chat", "json", "web_search"],
            "capability_profiles": {
                "json": {
                    "version": 1,
                    "tested": True,
                    "connection_type": "api",
                    "api_style": "openai_responses",
                    "request_body": {"text": {"format": {"type": "json_object"}}},
                },
                "web_search": {
                    "version": 2,
                    "tested": True,
                    "connection_type": "api",
                    "api_style": "openai_responses",
                    "request_mode": "openai_tools",
                    "tested_at": "2026-08-02T00:00:00+00:00",
                    "probe_version": "web_search.v2",
                    "request_body": {"tools": [{"type": "web_search"}]},
                },
                "unknown": {"tested": True, "request_body": {"ignored": True}},
            },
        }
    )

    assert set(model["capability_profiles"]) == {"json", "web_search"}
    assert "request_body" not in model["capability_profiles"]["json"]
    assert "request_body" not in model["capability_profiles"]["web_search"]
    assert model["capability_profiles"]["web_search"]["request_mode"] == "openai_tools"
    assert model["capability_profiles"]["web_search"]["version"] == 2
    assert model["capability_profiles"]["web_search"]["probe_version"] == "web_search.v2"


def test_normalize_ai_model_invalidates_versioned_proof_after_connection_change() -> None:
    raw_model = {
        "id": "fingerprinted",
        "connection_type": "api",
        "provider_id": "openai",
        "base_url": "https://models.example.invalid/v1",
        "api_key": "secret",
        "model": "model-a",
        "capabilities": ["chat"],
    }
    fingerprint = ai_model_config.model_configuration_fingerprint(
        ai_model_config.normalize_ai_model(raw_model)
    )
    raw_model["capability_profiles"] = {
        "chat": {
            "version": 2,
            "tested": True,
            "configuration_fingerprint": fingerprint,
            "probe_version": "chat.v2",
        }
    }

    current = ai_model_config.normalize_ai_model(raw_model)
    changed = ai_model_config.normalize_ai_model(
        {**raw_model, "model": "model-b"}
    )

    assert current["capabilities"] == ["chat"]
    assert "chat" in current["capability_profiles"]
    assert current["capability_profiles"]["chat"]["configuration_fingerprint"] == fingerprint
    assert changed["capabilities"] == []
    assert "capability_profiles" not in changed


def test_app_config_rejects_ai_protocol_fields_in_extra_request_body() -> None:
    with pytest.raises(ValueError, match="不得覆盖 Pydantic 请求协议字段"):
        app_config.normalize_app_config(
            {
                "ai_models": [
                    {
                        "id": "unsafe_model",
                        "connection_type": "api",
                        "provider": "OpenAI",
                        "provider_id": "openai",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "test-key",
                        "model": "test-model",
                        "capabilities": ["chat", "json"],
                        "extra": {
                            "request_body": {
                                "tools": [{"type": "function"}],
                                "stream": False,
                            }
                        },
                    }
                ]
            }
        )


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
                "provider_id": "deepseek",
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
                    "provider_id": "deepseek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                },
                {
                    "id": "copy_model_copy",
                    "copy_source_id": "copy_model",
                    "provider": "DeepSeek",
                    "provider_id": "deepseek",
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


def test_normalize_app_config_rejects_legacy_ai_aliases(
    app_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="已退役的 AI 配置字段"):
        app_config.normalize_app_config(
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


def test_normalize_app_config_keeps_1688_api_credentials() -> None:
    saved = app_config.normalize_app_config(
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
    saved = app_config.normalize_app_config(
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


def test_normalize_app_config_rejects_yunexpress_camel_case_fields() -> None:
    with pytest.raises(ValueError, match="已退役的 camelCase 字段"):
        app_config.normalize_app_config(
            {
                "yunexpress": {
                    "appId": "legacy-app-id",
                    "appSecret": "legacy-secret",
                }
            }
        )


def test_normalize_app_config_rejects_legacy_nested_ai_sections(
    app_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="已退役的 AI 配置字段"):
        app_config.normalize_app_config(
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


def test_normalize_app_config_rejects_legacy_ai_keys_alongside_canonical_model(
    app_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="已退役的 AI 配置字段"):
        app_config.normalize_app_config(
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


def test_normalize_app_config_rejects_retired_pricing_key() -> None:
    with pytest.raises(ValueError, match="packaging"):
        app_config.normalize_app_config(
            {"pricing_defaults": {"packaging": "9"}}
        )


def test_pricing_fields_do_not_backfill_other_canonical_fields() -> None:
    normalized = app_config.normalize_app_config(
        {
            "pricing_defaults": {
                "commission_percent": "13",
                "target_margin_percent": "41",
                "currency_rate": "7",
                "packaging_cost": "8",
                "domestic_freight": "9",
                "payment_fee_percent": "3",
            }
        }
    )
    pricing = normalized["pricing_defaults"]

    assert pricing["commission_percent"] == "13"
    assert pricing["target_margin_percent"] == "41"
    assert pricing["currency_rate"] == "7"
    assert pricing["packaging_cost"] == "8"
    assert pricing["domestic_freight"] == "9"
    assert pricing["payment_fee_percent"] == "3"
    assert pricing["default_target_margin_percent"] == "30"
    assert pricing["default_currency_rate"] == "1"
    assert pricing["default_packaging_cost"] == "0"
    assert pricing["default_domestic_freight"] == "0"
    assert pricing["mercadolibre_commission_percent"] == "20"
    assert pricing["wildberries_commission_percent"] == "20"
    assert pricing["ozon_commission_percent"] == "20"
    assert pricing["mercadolibre_payment_fee_percent"] == "0"
