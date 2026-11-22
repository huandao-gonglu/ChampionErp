from __future__ import annotations

import pytest

from erp_web.services import ai_generation_settings


def test_normalize_generation_settings_keeps_only_explicit_overrides() -> None:
    assert ai_generation_settings.normalize_generation_settings(
        {
            "temperature": "0",
            "max_output_tokens": "4096",
            "reasoning": {"effort": "low"},
        }
    ) == {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "reasoning": {"mode": "enabled", "effort": "low"},
    }


def test_normalize_generation_settings_rejects_conflicting_reasoning_fields() -> None:
    with pytest.raises(ValueError, match="关闭推理"):
        ai_generation_settings.normalize_generation_settings(
            {
                "reasoning": {
                    "mode": "disabled",
                    "budget_tokens": 1000,
                }
            }
        )


def test_alibaba_responses_maps_disabled_reasoning_and_overrides_raw_json() -> None:
    result = ai_generation_settings.pydantic_model_settings_payload(
        {
            "connection_type": "api",
            "provider_id": "alibaba",
            "api_style": "openai_responses",
            "extra": {
                "request_body": {
                    "enable_thinking": True,
                    "reasoning": {"summary": "auto", "effort": "high"},
                }
            },
        },
        {
            "temperature": 0,
            "max_output_tokens": 300,
            "reasoning": {"mode": "disabled"},
        },
    )

    assert result == {
        "temperature": 0.0,
        "max_tokens": 300,
        "extra_body": {"reasoning": {"effort": "none"}},
    }


def test_alibaba_chat_maps_thinking_switch_and_budget() -> None:
    result = ai_generation_settings.pydantic_model_settings_payload(
        {
            "connection_type": "api",
            "provider_id": "alibaba",
            "api_style": "openai_compatible",
        },
        {
            "reasoning": {
                "mode": "enabled",
                "budget_tokens": 1200,
            }
        },
    )

    assert result["extra_body"]["enable_thinking"] is True
    assert result["extra_body"]["thinking_budget"] == 1200
    assert "reasoning" not in result
    assert "reasoning_effort" not in result


def test_openai_chat_maps_unified_limit_to_pydantic_max_tokens() -> None:
    result = ai_generation_settings.pydantic_model_settings_payload(
        {
            "connection_type": "api",
            "provider_id": "openai",
            "api_style": "openai_compatible",
        },
        {"max_output_tokens": 1200},
    )

    assert result["max_tokens"] == 1200
    assert "max_completion_tokens" not in result
    assert "max_output_tokens" not in result


def test_deepseek_maps_reasoning_to_pydantic_thinking_and_strips_raw_fields() -> None:
    model = {
        "connection_type": "api",
        "provider_id": "deepseek",
        "api_style": "openai_compatible",
        "extra": {
            "request_body": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                "top_p": 0.9,
            }
        },
    }
    capabilities = ai_generation_settings.generation_capabilities(model)
    assert capabilities["reasoning"]["status"] == "supported"
    assert capabilities["reasoning"]["modes"] == ["disabled", "enabled"]
    assert capabilities["reasoning"]["efforts"] == ["minimal", "low", "medium", "high", "xhigh"]

    disabled = ai_generation_settings.pydantic_model_settings_payload(
        model, {"reasoning": {"mode": "disabled"}}
    )
    assert disabled == {"thinking": False, "extra_body": {"top_p": 0.9}}

    effort = ai_generation_settings.pydantic_model_settings_payload(
        model, {"reasoning": {"mode": "enabled", "effort": "low"}}
    )
    assert effort == {"thinking": "low", "extra_body": {"top_p": 0.9}}

    enabled = ai_generation_settings.pydantic_model_settings_payload(
        {**model, "thinking_enabled": True}, None
    )
    assert enabled == {"thinking": True, "extra_body": {"top_p": 0.9}}

    with pytest.raises(ValueError, match="DeepSeek 映射不支持统一的 reasoning.budget_tokens"):
        ai_generation_settings.pydantic_model_settings_payload(
            model, {"reasoning": {"mode": "enabled", "budget_tokens": 512}}
        )

    with pytest.raises(ValueError, match="DeepSeek 映射不支持 max 推理强度"):
        ai_generation_settings.pydantic_model_settings_payload(
            model, {"reasoning": {"mode": "enabled", "effort": "max"}}
        )


def test_unmapped_provider_rejects_reasoning_instead_of_silently_dropping_it() -> None:
    with pytest.raises(ValueError, match="无法安全转换推理参数"):
        ai_generation_settings.pydantic_model_settings_payload(
            {
                "connection_type": "api",
                "provider_id": "unknown",
                "api_style": "openai_compatible",
            },
            {"reasoning": {"mode": "disabled"}},
        )


def test_profile_payload_merges_deepseek_flash_gap_and_probed_overrides() -> None:
    ds_chat = ai_generation_settings.pydantic_openai_chat_profile_payload(
        "deepseek", "deepseek-flash"
    )
    assert ds_chat == {
        "openai_chat_supports_max_completion_tokens": False,
        "supports_thinking": True,
        "openai_reasoning_enabled_by_default": True,
        "openai_supports_forced_tool_choice_with_thinking": False,
    }

    proxy_chat = ai_generation_settings.pydantic_openai_chat_profile_payload(
        "openai",
        "custom-thinking-v1",
        capability_profiles={
            "chat": {"tested": True, "observed_thinking": True},
            "tool_calling": {"tested": True, "supports_tool_choice_required": False},
        },
    )
    assert proxy_chat == {
        "openai_supports_tool_choice_required": False,
        "supports_thinking": True,
        "openai_reasoning_enabled_by_default": True,
    }

    proxy_responses = ai_generation_settings.pydantic_openai_responses_profile_payload(
        "openai",
        "custom-thinking-v1",
        capability_profiles={
            "chat": {"tested": True, "observed_thinking": True},
            "tool_calling": {"tested": True, "supports_tool_choice_required": False},
        },
    )
    assert proxy_responses == {
        "openai_supports_tool_choice_required": False,
        "supports_thinking": True,
        "openai_reasoning_enabled_by_default": True,
        "openai_supports_reasoning": True,
    }


def test_anthropic_and_google_map_unified_thinking_and_budget_tokens() -> None:
    anthropic_model = {
        "connection_type": "api",
        "provider_id": "anthropic",
        "api_style": "anthropic_messages",
    }
    google_model = {
        "connection_type": "api",
        "provider_id": "google",
        "api_style": "google_gemini",
    }

    for model, budget_key in (
        (anthropic_model, "anthropic_thinking"),
        (google_model, "google_thinking_config"),
    ):
        caps = ai_generation_settings.generation_capabilities(model)
        assert caps["reasoning"]["status"] == "supported"
        assert caps["reasoning"]["supports_budget_tokens"] is True
        assert caps["reasoning"]["efforts"] == ["minimal", "low", "medium", "high", "xhigh"]

        assert ai_generation_settings.pydantic_model_settings_payload(
            model, {"reasoning": {"mode": "disabled"}}
        ) == {"thinking": False}
        assert ai_generation_settings.pydantic_model_settings_payload(
            model, {"reasoning": {"mode": "enabled", "effort": "high"}}
        ) == {"thinking": "high"}
        budget_payload = ai_generation_settings.pydantic_model_settings_payload(
            model, {"reasoning": {"mode": "enabled", "budget_tokens": 4096}}
        )
        assert budget_key in budget_payload
        assert "thinking" not in budget_payload

        with pytest.raises(ValueError, match="不支持 max 推理强度"):
            ai_generation_settings.pydantic_model_settings_payload(
                model, {"reasoning": {"mode": "enabled", "effort": "max"}}
            )
        with pytest.raises(ValueError, match="不能同时设置推理强度和推理预算"):
            ai_generation_settings.pydantic_model_settings_payload(
                model, {"reasoning": {"mode": "enabled", "effort": "low", "budget_tokens": 1024}}
            )


def test_non_api_generation_settings_are_explicitly_unsupported() -> None:
    capabilities = ai_generation_settings.generation_capabilities(
        {"connection_type": "cli", "provider_id": "openai"}
    )

    assert capabilities["status"] == "unsupported"
    assert capabilities["reasoning"]["status"] == "unsupported"
