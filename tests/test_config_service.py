from __future__ import annotations

import json
from pathlib import Path

import pytest

from erp_web import app_config
from erp_web.services import ai_model_config, ai_prompt_templates, config_service
from desktop_runtime import browser_ai_runtime


def test_config_paths_are_project_local(app_dir: Path) -> None:
    cfg_dir = config_service.config_dir(app_dir)
    env_file = config_service.env_path(app_dir)

    assert cfg_dir.resolve().is_relative_to((app_dir / "config").resolve())
    assert env_file.parent == cfg_dir


def test_default_env_template_and_public_config(app_dir: Path) -> None:
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
        "anthropic",
        "google",
        "alibaba",
        "omlx",
    }
    assert all(item["supported_api_styles"] for item in public["providers"])
    assert all(
        item["model_discovery"]
        in {"openai_models", "anthropic_models", "google_models", "manual"}
        for item in public["providers"]
    )
    assert all("generation_capabilities" in model for model in public["ai_models"])
    assert "ai_use_case_prompts" not in public
    assert public["storage"]["config_dir"].startswith(str(app_dir / "config"))


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


def test_resolve_ai_model_inherits_default_general_model_without_list_fallback() -> None:
    """未单独覆盖子功能时，文本/类目/文案/翻译/套图规划统一继承 global.chat，不按列表顺序误选其他模型。"""
    config = {
        "ai_models": [
            {
                "id": "legacy_vision",
                "name": "旧全能模型",
                "connection_type": "api",
                "provider_id": "openai",
                "model": "gpt-4o",
                "capabilities": ["chat", "json", "tool_calling", "image_understanding"],
                "enabled": True,
            },
            {
                "id": "main_deepseek",
                "name": "pniccDeepSeek",
                "connection_type": "api",
                "provider_id": "openai",
                "model": "deepseek-chat",
                "capabilities": ["chat", "json", "tool_calling"],
                "enabled": True,
            },
        ],
        "ai_use_case_bindings": {
            "global.chat": {"model_id": "main_deepseek"},
        },
    }

    for use_case in ("global.chat", "category.product_match", "copy.generate", "text.translate"):
        assert ai_model_config.resolve_ai_model(config, use_case)["id"] == "main_deepseek"

    # image.plan 需要识图：不静默回落到排在第一位的 legacy_vision，而是明确提示缺少图片理解能力。
    with pytest.raises(RuntimeError) as missing_vision:
        ai_model_config.resolve_ai_model(config, "image.plan")
    assert "套图规划" in str(missing_vision.value)
    assert "pniccDeepSeek" in str(missing_vision.value)
    assert "图片理解" in str(missing_vision.value)
    assert "高级：按功能单独覆盖" in str(missing_vision.value)

    # 仅在显式单独覆写 image.plan 时走独立模型；其余仍继承 global.chat。
    overridden = {
        **config,
        "ai_use_case_bindings": {
            "global.chat": {"model_id": "main_deepseek"},
            "image.plan": {"model_id": "legacy_vision"},
        },
    }
    assert ai_model_config.resolve_ai_model(overridden, "image.plan")["id"] == "legacy_vision"
    assert ai_model_config.resolve_ai_model(overridden, "category.product_match")["id"] == "main_deepseek"

    # 切换默认通用模型后，所有未单独覆盖的子功能同步切换。
    switched = {
        **config,
        "ai_use_case_bindings": {
            "global.chat": {"model_id": "legacy_vision"},
        },
    }
    for use_case in ("global.chat", "category.product_match", "copy.generate", "text.translate", "image.plan"):
        assert ai_model_config.resolve_ai_model(switched, use_case)["id"] == "legacy_vision"


def test_browser_presets_remain_available_after_the_user_deletes_the_model(app_dir: Path, monkeypatch) -> None:
    from desktop_runtime.config import LocalConfig
    from desktop_runtime.dispatcher import dispatch
    from erp_web.services import desktop_model_config_service as desktop_config
    local_owner = LocalConfig(app_dir / 'desktop')
    monkeypatch.setattr(desktop_config, 'current_desktop_connection', lambda: 'test')
    original_prepare = desktop_config.prepare_local_model
    def prepare(model):
        from erp_shared.desktop import LOCAL_MODEL_FIELDS
        if model.get('connection_type') in {'browser', 'cli'}:
            local_owner.update({str(model.get('id')): {key: value for key, value in model.items() if key in LOCAL_MODEL_FIELDS}})
        return original_prepare(model)
    monkeypatch.setattr(desktop_config, 'prepare_local_model', prepare)
    monkeypatch.setattr(desktop_config, 'call_desktop', lambda operation, params=None, **kw: dispatch(local_owner, operation, params or {}))
    preset = config_service.public_ai_config(app_dir, {})["browser_presets"][0]
    assert preset["id"] == "gemini_canvas"
    assert preset["supported_capabilities"] == ["image_edit"]
    model = preset["model"]
    assert model["connection_type"] == "browser"
    assert model["browser_url"].startswith("https://gemini.google.com/share/")
    assert model["browser_mode"] == "managed_profile"
    assert model["capabilities"] == ["image_edit"]
    assert model["canvas_managed"] is True
    assert "capability_profiles" not in model and "api_key" not in model
    saved = config_service.merge_ai_config(app_dir, {}, {
        "ai_models": [*ai_model_config.default_ai_models(), model],
    })
    removed = config_service.merge_ai_config(app_dir, saved, {
        "ai_models": [item for item in saved["ai_models"] if item["id"] != model["id"]],
    })
    public = config_service.public_ai_config(app_dir, removed)
    assert model["id"] not in {item["id"] for item in public["ai_models"]}
    assert public["browser_presets"] == [preset]


def test_fixed_canvas_model_is_non_deletable_and_skips_auto_match(app_dir: Path) -> None:
    from erp_web.context import get_context
    store = get_context().config
    loaded = store.load_app_config()
    canvas_rows = [row for row in loaded["ai_models"] if row.get("canvas_managed")]
    assert len(canvas_rows) == 1
    assert canvas_rows[0]["id"] == ai_model_config.CANVAS_FIXED_MODEL_ID
    assert canvas_rows[0]["capabilities"] == ["image_edit"]
    # 未显式绑定时，自动匹配不误选固定本地 Canvas 模型。
    only_text_and_canvas = {
        "ai_models": [
            {"id": "text_only", "connection_type": "api", "provider_id": "openai", "model": "gpt-4o", "capabilities": ["chat"]},
            canvas_rows[0],
        ],
        "ai_use_case_bindings": {},
    }
    with pytest.raises(RuntimeError, match="没有可用 AI 模型"):
        ai_model_config.resolve_ai_model(only_text_and_canvas, "image.translate")
    explicit = {**only_text_and_canvas, "ai_use_case_bindings": {"image.translate": {"model_id": canvas_rows[0]["id"]}}}
    assert ai_model_config.resolve_ai_model(explicit, "image.translate")["id"] == canvas_rows[0]["id"]
    # 网页端不能删除本机模型；桌面端保存也不能删除内置 Gemini Canvas 模型。
    from erp_web.schemas.requests import RequestValidationError
    from erp_web.services.desktop_connection_service import bind_desktop_connection
    without_canvas = [row for row in loaded["ai_models"] if not row.get("canvas_managed")]
    with pytest.raises(RequestValidationError):
        store.save_client_app_config({"ai_models": without_canvas}, ai_only=True)
    with bind_desktop_connection("desktop-conn"):
        with pytest.raises(ai_model_config.SystemModelError, match="不能删除内置的 Gemini Canvas"):
            store.save_client_app_config({"ai_models": without_canvas}, ai_only=True)


def test_browser_presets_never_copy_personal_browser_settings(app_dir: Path) -> None:
    template = ai_model_config.browser_model_presets()[0]["model"]
    personal = {**template, "browser_proxy": "http://private-proxy.test:8123", "browser_profile": "personal"}
    public = config_service.public_ai_config(app_dir, {"ai_models": [personal]})
    preset = public["browser_presets"][0]
    assert preset["model"]["browser_proxy"] == ""
    assert preset["model"]["browser_profile"] == "canvas"
    assert "private-proxy" not in json.dumps(public["browser_presets"])
    preset["model"]["browser_proxy"] = "http://changed.test:8123"
    assert ai_model_config.browser_model_presets()[0]["model"]["browser_proxy"] == ""


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
            "category.product_match": "category_model",
        }
    )

    assert bindings["copy.generate"] == {
        "model_id": "copy_model",
        "timeout_override_seconds": 125,
    }
    assert bindings["category.product_match"] == {"model_id": "category_model"}


def test_ai_use_case_binding_normalizes_generation_overrides() -> None:
    bindings = ai_model_config.normalize_ai_use_case_bindings(
        {
            "text.translate": {
                "model_id": "qwen_model",
                "generation": {
                    "temperature": "0",
                    "max_output_tokens": "3000",
                    "reasoning": {"mode": "disabled"},
                },
            }
        }
    )

    assert bindings["text.translate"] == {
        "model_id": "qwen_model",
        "generation": {
            "temperature": 0.0,
            "max_output_tokens": 3000,
            "reasoning": {"mode": "disabled"},
        },
    }


def test_merge_ai_config_accepts_deepseek_reasoning_and_rejects_unsupported_fields(
    app_dir: Path,
) -> None:
    merged = config_service.merge_ai_config(
        app_dir,
        {},
        {
            "ai_models": [
                {
                    "id": "deepseek_model",
                    "connection_type": "api",
                    "provider": "DeepSeek",
                    "provider_id": "deepseek",
                    "model": "deepseek-flash",
                    "capabilities": ["chat", "json"],
                    "thinking_enabled": False,
                }
            ],
            "ai_use_case_bindings": {
                "text.translate": {
                    "model_id": "deepseek_model",
                    "generation": {"reasoning": {"mode": "disabled"}},
                }
            },
        },
    )
    assert merged["ai_models"][0]["thinking_enabled"] is False
    assert merged["ai_use_case_bindings"]["text.translate"]["generation"] == {
        "reasoning": {"mode": "disabled"}
    }

    with pytest.raises(ValueError, match="DeepSeek 映射不支持统一的 reasoning.budget_tokens"):
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
                        "model": "deepseek-flash",
                        "capabilities": ["chat", "json"],
                    }
                ],
                "ai_use_case_bindings": {
                    "text.translate": {
                        "model_id": "deepseek_model",
                        "generation": {"reasoning": {"mode": "enabled", "budget_tokens": 512}},
                    }
                },
            },
        )


def test_user_configuration_cannot_write_system_prompt_files(tmp_path: Path) -> None:
    from erp_web.schemas.requests import RequestValidationError
    with pytest.raises(RequestValidationError, match="系统维护") as error:
        config_service.merge_ai_config(tmp_path, {}, {
            "ai_use_case_prompts": {"global.chat": {"path": "config/prompts/global_chat.json", "system_prompt": "旧规则"}},
        })
    assert error.value.status_code == 403
    assert not (tmp_path / "config/prompts/global_chat.json").exists()


def test_legacy_configuration_remains_readable_but_no_longer_controls_prompts():
    old = {**app_config.default_app_config(), "ai_use_case_prompts": {"global.chat": {"path": "/old/prompt.json"}}}
    assert "ai_use_case_prompts" not in app_config.normalize_app_config(old)


def test_system_prompt_segments_and_installation_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ai_prompt_templates, "SYSTEM_PROMPT_DIR", tmp_path)
    path = tmp_path / "global_chat.json"
    path.write_text(json.dumps({"system": ["第一句。", "第二句。"], "user": "{$input_text}"}), encoding="utf-8")
    assert ai_prompt_templates.load_ai_use_case_prompt_pair("global.chat") == {"system": "第一句。第二句。", "user": "{$input_text}"}
    path.unlink()
    with pytest.raises(ValueError, match="应用安装文件"):
        ai_prompt_templates.load_ai_use_case_prompt_pair("global.chat")
    with pytest.raises(ValueError, match="没有系统提示词"):
        ai_prompt_templates.load_ai_use_case_prompt_pair("../other")


def test_image_prompt_uses_user_template_without_a_chat_system_message():
    prompt = ai_prompt_templates.load_ai_use_case_prompt_pair("image.translate")
    assert prompt["system"] == ""
    assert "language" in prompt["user"]


def test_merge_config_preserves_existing_model_key_when_public_payload_is_blank(
    app_dir: Path,
) -> None:
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


def test_merge_config_clears_saved_api_key_when_model_switches_to_cli(
    app_dir: Path,
    monkeypatch,
) -> None:
    from desktop_runtime.config import LocalConfig
    from desktop_runtime.dispatcher import dispatch
    from erp_web.services import desktop_model_config_service as desktop_config
    local_owner = LocalConfig(app_dir / 'desktop')
    monkeypatch.setattr(desktop_config, 'current_desktop_connection', lambda: 'test')
    original_prepare = desktop_config.prepare_local_model
    def prepare(model):
        from erp_shared.desktop import LOCAL_MODEL_FIELDS
        if model.get('connection_type') in {'browser', 'cli'}:
            local_owner.update({str(model.get('id')): {key: value for key, value in model.items() if key in LOCAL_MODEL_FIELDS}})
        return original_prepare(model)
    monkeypatch.setattr(desktop_config, 'prepare_local_model', prepare)
    monkeypatch.setattr(desktop_config, 'call_desktop', lambda operation, params=None, **kw: dispatch(local_owner, operation, params or {}))
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
    assert "command" not in merged["ai_models"][0]
    assert local_owner.read()["copy_model"]["command"] == "codex"


def test_merge_config_clears_saved_api_key_when_model_switches_to_browser(
    app_dir: Path,
    monkeypatch,
) -> None:
    from desktop_runtime.config import LocalConfig
    from desktop_runtime.dispatcher import dispatch
    from erp_web.services import desktop_model_config_service as desktop_config
    local_owner = LocalConfig(app_dir / 'desktop')
    monkeypatch.setattr(desktop_config, 'current_desktop_connection', lambda: 'test')
    original_prepare = desktop_config.prepare_local_model
    def prepare(model):
        from erp_shared.desktop import LOCAL_MODEL_FIELDS
        if model.get('connection_type') in {'browser', 'cli'}:
            local_owner.update({str(model.get('id')): {key: value for key, value in model.items() if key in LOCAL_MODEL_FIELDS}})
        return original_prepare(model)
    monkeypatch.setattr(desktop_config, 'prepare_local_model', prepare)
    monkeypatch.setattr(desktop_config, 'call_desktop', lambda operation, params=None, **kw: dispatch(local_owner, operation, params or {}))
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
    assert "browser_profile" not in merged["ai_models"][0]
    assert local_owner.read()["copy_model"]["browser_profile"] == "default"


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
    assert seeded[0]["provider_id"] == "openai"
    assert seeded[0]["provider"] == "OpenAI（官方 / 兼容协议）"
    assert seeded[0]["model"] == "deepseek-chat"
    assert seeded[1]["id"] == "default_image"
    assert seeded[1]["connection_type"] == "api"
    assert seeded[1]["provider_id"] == "openai"
    assert seeded[1]["provider"] == "OpenAI（官方 / 兼容协议）"
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
    assert (
        model["capability_profiles"]["web_search"]["probe_version"] == "web_search.v2"
    )


def test_normalize_ai_model_invalidates_versioned_proof_after_connection_change() -> (
    None
):
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
    changed = ai_model_config.normalize_ai_model({**raw_model, "model": "model-b"})

    assert current["capabilities"] == ["chat"]
    assert "chat" in current["capability_profiles"]
    assert (
        current["capability_profiles"]["chat"]["configuration_fingerprint"]
        == fingerprint
    )
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
            "capabilities": [
                "chat",
                "json",
                "web_search",
                "image_generate",
                "image_edit",
            ],
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
    assert model["capabilities"] == [
        "chat",
        "json",
        "web_search",
        "image_generate",
        "image_edit",
    ]
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
    assert browser_ai_runtime.browser_ai_profile_dir("/tmp/champion", model) == Path(
        "/tmp/champion/browser_profile/ai/chatgpt/default"
    )


def test_public_ai_config_exposes_connection_types_and_cli_tools(app_dir: Path) -> None:
    public = config_service.public_ai_config(app_dir, {})

    assert public["connection_types"] == ["api", "cli", "browser"]
    assert public["browser_modes"] == ["managed_profile", "existing_browser"]
    assert any(
        tool["value"] == "codex" and tool["command"] == "codex"
        for tool in public["cli_tools"]
    )
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
    assert "base_url" not in saved["1688_api"]
    assert saved["1688_api"]["timeout_seconds"] == "30"


def test_normalize_app_config_rejects_retired_pricing_key() -> None:
    with pytest.raises(ValueError, match="packaging"):
        app_config.normalize_app_config({"pricing_defaults": {"packaging": "9"}})


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

    assert "commission_percent" not in pricing
    assert pricing["target_margin_percent"] == "41"
    assert pricing["currency_rate"] == "7"
    assert pricing["packaging_cost"] == "8"
    assert pricing["domestic_freight"] == "9"
    assert pricing["payment_fee_percent"] == "3"
    assert pricing["default_target_margin_percent"] == "30"
    assert pricing["default_currency_rate"] == "1"
    assert pricing["default_packaging_cost"] == "0"
    assert pricing["default_domestic_freight"] == "0"
    assert "mercadolibre_commission_percent" not in pricing
    assert "wildberries_commission_percent" not in pricing
    assert "ozon_commission_percent" not in pricing
    assert pricing["mercadolibre_payment_fee_percent"] == "0"
