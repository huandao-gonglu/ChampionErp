from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest
from pydantic_ai import Agent, FunctionToolset, RunContext, Tool
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from erp_web.services import ai_model_factory, ai_provider_catalog
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


def test_public_provider_catalog_does_not_expose_generic_adapter_as_second_openai() -> None:
    providers = {
        item["id"]: item
        for item in ai_provider_catalog.public_provider_catalog()
    }

    assert set(providers) == {"openai", "deepseek", "alibaba"}
    assert providers["openai"]["label"] == "OpenAI"
    assert providers["openai"]["supported_api_styles"] == [
        "openai_compatible",
        "openai_responses",
    ]


def test_pydantic_ai_dependency_is_locked_to_verified_public_api() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()

    assert requirements.count("pydantic-ai-slim[openai]==2.22.0") == 1
    assert version("pydantic-ai-slim") == "2.22.0"
    assert all(
        item is not None
        for item in (
            Agent,
            FunctionToolset,
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
        "parallel_tool_calls": False,
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
    assert binding.model_settings["parallel_tool_calls"] is False


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


def test_factory_resolves_environment_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_factory_rejects_retired_provider_retry_override() -> None:
    with pytest.raises(AiModelFactoryError, match="provider_max_retries 已退役"):
        create_pydantic_model_binding(
            model_config(extra={"provider_max_retries": 3})
        )


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
        "parallel_tool_calls": False,
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


def test_factory_rejects_unsupported_connection_capability_and_protocol_override() -> None:
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


def test_factory_rejects_unmapped_deepseek_reasoning_instead_of_silent_downgrade() -> None:
    with pytest.raises(AiModelFactoryError, match="无法安全转换推理参数"):
        create_pydantic_model_binding(
            model_config(
                provider="DeepSeek",
                provider_id="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-reasoner",
            ),
            generation_settings={
                "reasoning": {"mode": "enabled", "effort": "medium"}
            },
        )
