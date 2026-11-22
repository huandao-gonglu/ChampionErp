from __future__ import annotations

import asyncio
from importlib.metadata import version
from pathlib import Path

import pytest
from pydantic_ai import Agent, FunctionToolset, RunContext, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from erp_web.services import ai_provider_catalog
from erp_web.services.ai_model_factory import (
    AiModelFactoryError,
    create_pydantic_model_binding,
    create_pydantic_model_binding_for_use_case,
    create_pydantic_probe_binding,
)
from erp_web.services.ai_pydantic_image_model import OpenAIImagesModel


def model_config(**overrides):
    config = {
        "id": "agent-model",
        "name": "Agent Model",
        "connection_type": "api",
        "provider": "OpenAI",
        "provider_id": "openai",
        "api_style": "openai_compatible",
        "base_url": "https://models.example.invalid/v1",
        "api_key": "sk-sensitive-test-value",
        "model": "example-model",
        "capabilities": ["chat", "json", "tool_calling"],
        "timeout_seconds": 30,
        "enabled": True,
    }
    config.update(overrides)
    return config


def test_public_provider_catalog_does_not_expose_generic_adapter_as_second_openai() -> (
    None
):
    providers = {
        item["id"]: item for item in ai_provider_catalog.public_provider_catalog()
    }

    assert set(providers) == {"openai", "anthropic", "google", "alibaba", "omlx"}
    assert providers["openai"]["label"] == "OpenAI（官方 / 兼容协议）"
    assert providers["openai"]["supported_api_styles"] == [
        "openai_compatible",
        "openai_responses",
    ]
    assert providers["anthropic"]["supported_api_styles"] == ["anthropic_messages"]
    assert providers["google"]["supported_api_styles"] == ["google_gemini"]


def test_pydantic_ai_dependency_is_locked_to_verified_public_api() -> None:
    requirements = (
        (Path(__file__).resolve().parents[1] / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert requirements.count("pydantic-ai-slim[openai,anthropic,google]==2.44.0") == 1
    assert version("pydantic-ai-slim") == "2.44.0"
    assert all(
        item is not None
        for item in (
            Agent,
            AnthropicModel,
            AnthropicProvider,
            FunctionToolset,
            GoogleModel,
            GoogleProvider,
            RunContext,
            Tool,
            FunctionModel,
            TestModel,
            ModelSettings,
        )
    )
    assert TestModel().model_name == "test"


def test_factory_builds_openai_chat_model_with_custom_base_url_and_settings() -> None:
    binding = create_pydantic_model_binding(
        model_config(),
        generation_settings={
            "temperature": 0.15,
            "max_output_tokens": 900,
        },
        timeout_seconds=12,
        required_capabilities={"chat", "tool_calling"},
    )

    assert isinstance(binding.model, OpenAIChatModel)
    assert binding.model.model_name == "example-model"
    assert binding.model.base_url == "https://models.example.invalid/v1/"
    assert "openai_chat_supports_max_completion_tokens" not in binding.model.profile
    assert binding.provider_id == "openai"
    assert binding.model_settings == {
        "temperature": 0.15,
        "max_tokens": 900,
        "timeout": 12.0,
    }


def test_factory_builds_responses_model_and_unified_openai_thinking() -> None:
    binding = create_pydantic_model_binding(
        model_config(
            provider="OpenAI",
            provider_id="openai",
            api_style="openai_responses",
            model="gpt-5.2",
        ),
        generation_settings={
            "max_output_tokens": 1200,
            "reasoning": {"mode": "enabled", "effort": "high"},
        },
    )

    assert isinstance(binding.model, OpenAIResponsesModel)
    assert binding.model_settings["max_tokens"] == 1200
    assert binding.model_settings["thinking"] == "high"
    assert "parallel_tool_calls" not in binding.model_settings


def test_custom_responses_gateway_replays_failed_and_corrected_calls_with_distinct_names():
    model = create_pydantic_model_binding(model_config(api_style="openai_responses", model="deepseek-flash")).model
    name = "draft_attributes_read"
    wrong = f"{name}__{name}"
    history = [
        ModelResponse(parts=[ToolCallPart(wrong, {}, "failed_call")]),
        ModelRequest(parts=[RetryPromptPart(content="工具名无效", tool_name=wrong, tool_call_id="failed_call")]),
        ModelResponse(parts=[ToolCallPart(name, {}, "successful_call")]),
        ModelRequest(parts=[ToolReturnPart(name, {"ok": True}, "successful_call")]),
    ]
    definitions = [ToolDefinition(name=t, defer_loading=True) for t in (name, "draft_images_prepare")]
    _, params = model.prepare_request(None, ModelRequestParameters(
        function_tools=definitions, revealed_tool_names={name},
    ))
    tools, _ = model._get_responses_tool_choice({}, params)
    assert [tool["name"] for tool in tools] == [name]
    _, items = asyncio.run(model._map_messages(model.prepare_messages(history, params), {}, params))
    calls = [item for item in items if item["type"] == "function_call"]
    assert [(c["name"], c.get("namespace"), c["call_id"]) for c in calls] == [
        (wrong, None, "failed_call"), (name, None, "successful_call"),
    ]
    assert [item["call_id"] for item in items if item["type"] == "function_call_output"] == [
        "failed_call", "successful_call",
    ]


def test_alibaba_qwen_responses_downgrades_required_tool_choice_to_auto() -> None:
    binding = create_pydantic_model_binding(
        model_config(
            provider="Alibaba",
            provider_id="alibaba",
            api_style="openai_responses",
            model="qwen3.7-plus",
        ),
        required_capabilities={"chat", "json", "tool_calling"},
    )
    request_parameters = ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name="search_categories",
                parameters_json_schema={"type": "object"},
            )
        ],
        output_mode="tool",
        output_tools=[
            ToolDefinition(
                name="final_result",
                parameters_json_schema={"type": "object"},
            )
        ],
        allow_text_output=False,
    )

    _, tool_choice = binding.model._get_responses_tool_choice(
        binding.model_settings,
        request_parameters,
    )

    assert isinstance(binding.model, OpenAIResponsesModel)
    assert isinstance(binding.model.provider, AlibabaProvider)
    assert binding.model.profile["openai_supports_tool_choice_required"] is False
    assert tool_choice == "auto"


def test_factory_builds_focused_pydantic_image_model() -> None:
    binding = create_pydantic_model_binding(
        model_config(
            provider="OpenAI",
            provider_id="openai",
            model="gpt-image-1",
            capabilities=["image_generate", "image_edit"],
        ),
        required_capabilities=["image_edit"],
    )

    assert isinstance(binding.model, OpenAIImagesModel)
    assert binding.required_capabilities == ("image_edit",)
    assert binding.model_config["id"] == "agent-model"

    with pytest.raises(AiModelFactoryError, match="不能同时承担文本"):
        create_pydantic_model_binding(
            model_config(
                capabilities=["chat", "image_edit"],
            ),
            required_capabilities=["chat", "image_edit"],
        )


def test_factory_maps_alibaba_chat_reasoning_without_openai_thinking() -> None:
    binding = create_pydantic_model_binding(
        model_config(
            provider="Alibaba",
            provider_id="alibaba",
            extra={
                "request_body": {
                    "vendor_extension": "kept",
                    "enable_thinking": False,
                    "max_tokens": 10,
                }
            },
        ),
        generation_settings={
            "max_output_tokens": 800,
            "reasoning": {"mode": "enabled", "budget_tokens": 2048},
        },
    )

    assert "thinking" not in binding.model_settings
    assert isinstance(binding.model.provider, AlibabaProvider)
    assert binding.model.profile["json_schema_transformer"] is not None
    assert binding.model.profile["openai_chat_supports_max_completion_tokens"] is False
    assert binding.model_settings["max_tokens"] == 800
    assert binding.model_settings["extra_body"] == {
        "vendor_extension": "kept",
        "enable_thinking": True,
        "thinking_budget": 2048,
    }


def test_factory_resolves_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_TEST_AGENT_API_KEY", "env-secret-value")
    monkeypatch.setenv("ERP_TEST_AGENT_BASE_URL", "https://env.example.invalid/v1")
    monkeypatch.setenv("ERP_TEST_AGENT_MODEL", "env-model")

    binding = create_pydantic_model_binding(
        model_config(
            api_key="",
            api_key_env="ERP_TEST_AGENT_API_KEY",
            base_url="",
            base_url_env="ERP_TEST_AGENT_BASE_URL",
            model="",
            model_env="ERP_TEST_AGENT_MODEL",
        )
    )

    assert binding.model.model_name == "env-model"
    assert binding.model.base_url == "https://env.example.invalid/v1/"
    assert "env-secret-value" not in repr(binding)


def test_factory_uses_curated_deepseek_provider() -> None:
    binding = create_pydantic_model_binding(
        model_config(
            provider_id="deepseek",
            provider="DeepSeek",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
    )

    assert isinstance(binding.model.provider, DeepSeekProvider)
    assert binding.provider_id == "deepseek"

    with pytest.raises(AiModelFactoryError, match="不支持 API 协议"):
        create_pydantic_model_binding(
            model_config(
                provider_id="deepseek",
                provider="DeepSeek",
                base_url="https://api.deepseek.com",
                api_style="openai_responses",
            )
        )

    with pytest.raises(AiModelFactoryError, match="官方地址"):
        create_pydantic_model_binding(
            model_config(
                provider_id="deepseek",
                provider="DeepSeek",
                base_url="https://proxy.example.invalid/v1",
            )
        )


def test_factory_resolves_frontend_neutral_use_case_binding(tmp_path: Path) -> None:
    config = {
        "ai_models": [model_config()],
        "ai_use_case_bindings": {
            "category.product_match": {
                "model_id": "agent-model",
                "timeout_override_seconds": 17,
                "generation": {
                    "temperature": 0.25,
                    "max_output_tokens": 640,
                },
            }
        },
    }

    binding = create_pydantic_model_binding_for_use_case(
        tmp_path,
        config,
        "category.product_match",
        timeout_seconds=99,
    )

    assert binding.model_id == "agent-model"
    assert binding.model_settings == {
        "temperature": 0.25,
        "max_tokens": 640,
        "timeout": 17.0,
    }


def test_use_case_factory_rejects_unknown_or_incompatible_binding(
    tmp_path: Path,
) -> None:
    with pytest.raises(AiModelFactoryError, match="未知 AI 功能"):
        create_pydantic_model_binding_for_use_case(
            tmp_path,
            {"ai_models": [model_config()]},
            "unknown.use_case",
        )

    with pytest.raises(AiModelFactoryError, match="不满足能力要求"):
        create_pydantic_model_binding_for_use_case(
            tmp_path,
            {
                "ai_models": [model_config(capabilities=["chat"])],
                "ai_use_case_bindings": {
                    "category.product_match": {"model_id": "agent-model"}
                },
            },
            "category.product_match",
        )


def test_factory_rejects_unsupported_connection_capability_and_protocol_override() -> (
    None
):
    with pytest.raises(AiModelFactoryError, match="只支持 API"):
        create_pydantic_model_binding(model_config(connection_type="cli"))

    with pytest.raises(AiModelFactoryError) as capability_error:
        create_pydantic_model_binding(
            model_config(capabilities=["chat", "json"]),
            required_capabilities={"tool_calling"},
        )
    assert capability_error.value.code == "AI_MODEL_TOOL_CALLING_UNSUPPORTED"

    with pytest.raises(AiModelFactoryError, match="Pydantic 请求协议字段"):
        create_pydantic_model_binding(
            model_config(extra={"request_body": {"tools": [{"type": "function"}]}})
        )


def test_probe_factory_does_not_require_a_predeclared_capability() -> None:
    config = model_config(capabilities=[])

    binding = create_pydantic_probe_binding(
        config,
        probe_capability="tool_calling",
    )

    assert isinstance(binding.model, OpenAIChatModel)
    assert binding.required_capabilities == ("tool_calling",)
    assert config["capabilities"] == []

    with pytest.raises(AiModelFactoryError) as runtime_error:
        create_pydantic_model_binding(
            config,
            required_capabilities=["tool_calling"],
        )
    assert runtime_error.value.code == "AI_MODEL_TOOL_CALLING_UNSUPPORTED"


def test_factory_rejects_invalid_config_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-must-never-appear"

    def fail_provider(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    monkeypatch.setattr(ai_provider_catalog, "create_pydantic_provider", fail_provider)
    with pytest.raises(AiModelFactoryError) as caught:
        create_pydantic_model_binding(model_config(api_key=secret))

    assert caught.value.code == "AI_MODEL_CONFIGURATION_INVALID"
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None

    with pytest.raises(AiModelFactoryError, match="API Key"):
        create_pydantic_model_binding(model_config(api_key="", api_key_env=""))


def _forced_tool_request_parameters() -> ModelRequestParameters:
    return ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name="search_categories",
                parameters_json_schema={"type": "object"},
            )
        ],
        output_mode="tool",
        output_tools=[
            ToolDefinition(
                name="final_result",
                parameters_json_schema={"type": "object"},
            )
        ],
        allow_text_output=False,
    )


def test_factory_maps_deepseek_thinking_and_downgrades_forced_tool_choice() -> None:
    request_parameters = _forced_tool_request_parameters()

    # deepseek-flash 默认开启思考：强制 tool_choice 自动降级为 auto。
    default_binding = create_pydantic_model_binding(
        model_config(
            provider="DeepSeek",
            provider_id="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
        )
    )
    assert isinstance(default_binding.model, OpenAIChatModel)
    assert isinstance(default_binding.model.provider, DeepSeekProvider)
    assert default_binding.model.profile["supports_thinking"] is True
    assert default_binding.model.profile["openai_reasoning_enabled_by_default"] is True
    assert default_binding.model.profile["openai_supports_forced_tool_choice_with_thinking"] is False
    _, default_tool_choice = default_binding.model._get_tool_choice(
        default_binding.model_settings, request_parameters
    )
    assert default_tool_choice == "auto"

    # deepseek-flash 关闭思考：下发 reasoning_effort='none'，并恢复强制 tool_choice='required'。
    off_binding = create_pydantic_model_binding(
        model_config(
            provider="DeepSeek",
            provider_id="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            thinking_enabled=False,
        )
    )
    assert off_binding.model_settings["thinking"] is False
    off_settings, off_params = off_binding.model.prepare_request(
        off_binding.model_settings, request_parameters
    )
    assert off_binding.model._translate_thinking(off_settings or {}, off_params) == "none"
    _, off_tool_choice = off_binding.model._get_tool_choice(off_settings or {}, off_params)
    assert off_tool_choice == "required"

    # deepseek-flash 指定推理强度：走原生 thinking 并维持 tool_choice='auto'。
    low_binding = create_pydantic_model_binding(
        model_config(
            provider="DeepSeek",
            provider_id="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
        ),
        generation_settings={"reasoning": {"mode": "enabled", "effort": "low"}},
    )
    assert low_binding.model_settings["thinking"] == "low"
    low_settings, low_params = low_binding.model.prepare_request(
        low_binding.model_settings, request_parameters
    )
    assert low_binding.model._translate_thinking(low_settings or {}, low_params) == "low"
    _, low_tool_choice = low_binding.model._get_tool_choice(low_settings or {}, low_params)
    assert low_tool_choice == "auto"

    # deepseek-reasoner 原生始终开启思考：允许开启，拒绝关闭，且降级 tool_choice。
    reasoner_on = create_pydantic_model_binding(
        model_config(
            provider="DeepSeek",
            provider_id="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-reasoner",
            thinking_enabled=True,
        )
    )
    _, reasoner_tool_choice = reasoner_on.model._get_tool_choice(
        reasoner_on.model_settings, request_parameters
    )
    assert reasoner_tool_choice == "auto"

    with pytest.raises(AiModelFactoryError, match="不支持关闭 Thinking"):
        create_pydantic_model_binding(
            model_config(
                provider="DeepSeek",
                provider_id="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-reasoner",
                thinking_enabled=False,
            )
        )


def test_factory_reuses_deepseek_profile_on_custom_openai_gateway_like_newapi() -> None:
    request_parameters = _forced_tool_request_parameters()
    newapi_dsflash = create_pydantic_model_binding(
        model_config(
            provider="OpenAI",
            provider_id="openai",
            base_url="http://127.0.0.1:3000/v1",
            model="deepseek-flash",
        )
    )
    assert isinstance(newapi_dsflash.model, OpenAIChatModel)
    assert newapi_dsflash.model.profile["openai_chat_thinking_field"] == "reasoning_content"
    assert newapi_dsflash.model.profile["openai_chat_send_back_thinking_parts"] == "field"
    assert newapi_dsflash.model.profile["supports_thinking"] is True
    assert newapi_dsflash.model.profile["openai_reasoning_enabled_by_default"] is True
    assert newapi_dsflash.model.profile["openai_supports_forced_tool_choice_with_thinking"] is False
    _, tool_choice = newapi_dsflash.model._get_tool_choice(
        newapi_dsflash.model_settings, request_parameters
    )
    assert tool_choice == "auto"

    # 在 New API 托管的 deepseek-flash 上显式关闭 Thinking 也应正常工作。
    newapi_off = create_pydantic_model_binding(
        model_config(
            provider="OpenAI",
            provider_id="openai",
            base_url="http://127.0.0.1:3000/v1",
            model="deepseek-flash",
            thinking_enabled=False,
        )
    )
    off_settings, off_params = newapi_off.model.prepare_request(
        newapi_off.model_settings, request_parameters
    )
    assert newapi_off.model._translate_thinking(off_settings or {}, off_params) == "none"
    _, off_tool_choice = newapi_off.model._get_tool_choice(off_settings or {}, off_params)
    assert off_tool_choice == "required"


def test_factory_applies_probed_profile_overrides_and_probe_ignores_stale_target() -> None:
    request_parameters = _forced_tool_request_parameters()
    cfg = model_config(
        provider="OpenAI",
        provider_id="openai",
        base_url="https://proxy.example.invalid/v1",
        model="custom-thinking-v1",
        capability_profiles={
            "chat": {"tested": True, "version": 2, "observed_thinking": True},
            "tool_calling": {
                "tested": True,
                "version": 2,
                "supports_tool_choice_required": False,
            },
        },
    )
    binding = create_pydantic_model_binding(cfg)
    assert binding.model.profile["openai_supports_tool_choice_required"] is False
    assert binding.model.profile["supports_thinking"] is True
    assert binding.model.profile["openai_reasoning_enabled_by_default"] is True
    _, tool_choice = binding.model._get_tool_choice(
        binding.model_settings, request_parameters
    )
    assert tool_choice == "auto"

    # 重测 tool_calling 时必须忽略旧的 tool_calling 档案，以便重新探测服务端真实行为。
    probe_binding = create_pydantic_probe_binding(cfg, probe_capability="tool_calling")
    assert probe_binding.model.profile.get("openai_supports_tool_choice_required") is not False
    assert probe_binding.model.profile["supports_thinking"] is True


def test_factory_builds_native_anthropic_and_google_models_with_thinking() -> None:
    anthropic_binding = create_pydantic_model_binding(
        model_config(
            provider="Anthropic（Claude）",
            provider_id="anthropic",
            api_style="anthropic_messages",
            base_url="https://proxy.example.invalid/anthropic",
            model="claude-sonnet-4-6",
            thinking_enabled=True,
        )
    )
    assert isinstance(anthropic_binding.model, AnthropicModel)
    assert isinstance(anthropic_binding.model.provider, AnthropicProvider)
    assert anthropic_binding.model_settings["thinking"] is True

    anthropic_budget = create_pydantic_model_binding(
        model_config(
            provider_id="anthropic",
            api_style="anthropic_messages",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4-6",
        ),
        generation_settings={"reasoning": {"mode": "enabled", "budget_tokens": 4096}},
    )
    assert anthropic_budget.model_settings["anthropic_thinking"] == {
        "type": "enabled",
        "budget_tokens": 4096,
    }

    google_binding = create_pydantic_model_binding(
        model_config(
            provider="Google Gemini",
            provider_id="google",
            api_style="google_gemini",
            base_url="https://proxy.example.invalid/gemini",
            model="gemini-2.5-flash",
            thinking_enabled=False,
        )
    )
    assert isinstance(google_binding.model, GoogleModel)
    assert isinstance(google_binding.model.provider, GoogleProvider)
    assert google_binding.model_settings["thinking"] is False

    google_budget = create_pydantic_model_binding(
        model_config(
            provider_id="google",
            api_style="google_gemini",
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
        ),
        generation_settings={"reasoning": {"mode": "enabled", "budget_tokens": 2048}},
    )
    assert google_budget.model_settings["google_thinking_config"] == {
        "include_thoughts": True,
        "thinking_budget": 2048,
    }

    with pytest.raises(AiModelFactoryError, match="不支持关闭 Thinking"):
        create_pydantic_model_binding(
            model_config(
                provider_id="google",
                api_style="google_gemini",
                base_url="https://generativelanguage.googleapis.com",
                model="gemini-2.5-pro",
                thinking_enabled=False,
            )
        )


def test_custom_openai_gateway_reuses_pydantic_family_profiles_safely() -> None:
    kimi = create_pydantic_model_binding(
        model_config(
            provider_id="openai",
            base_url="https://gateway.example.invalid/v1",
            model="moonshotai/kimi-k2.5",
            thinking_enabled=True,
        )
    )
    assert kimi.model.profile["supports_thinking"] is True

    qwen_coder = create_pydantic_model_binding(
        model_config(
            provider_id="openai",
            base_url="https://gateway.example.invalid/v1",
            model="qwen-3-coder-plus",
        )
    )
    assert qwen_coder.model.profile["openai_supports_tool_choice_required"] is False

    for proxy_model in ("grok-3", "anthropic/claude-sonnet-4-6", "google/gemini-2.5-flash"):
        binding = create_pydantic_model_binding(
            model_config(
                provider_id="openai",
                base_url="https://gateway.example.invalid/v1",
                model=proxy_model,
            )
        )
        assert binding.model.profile["supports_thinking"] is True
        assert WebSearchTool not in binding.model.profile.get("supported_native_tools", frozenset())

    grok4 = create_pydantic_model_binding(
        model_config(
            provider_id="openai",
            base_url="https://gateway.example.invalid/v1",
            model="grok-4",
        )
    )
    assert WebSearchTool not in grok4.model.profile.get("supported_native_tools", frozenset())


def test_image_plan_selects_a_vision_model_and_preserves_an_explicit_binding(tmp_path):
    text = model_config()
    vision = model_config(id="vision", capabilities=["chat", "json", "image_understanding"])
    config = {"ai_models": [text, vision]}
    binding = create_pydantic_model_binding_for_use_case(tmp_path, config, "image.plan")
    assert binding.model_id == "vision"
    assert isinstance(binding.model, OpenAIChatModel)

    config["ai_use_case_bindings"] = {"image.plan": {"model_id": text["id"]}}
    with pytest.raises(AiModelFactoryError, match="image_understanding"):
        create_pydantic_model_binding_for_use_case(tmp_path, config, "image.plan")
