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


def test_deepseek_rejects_unmapped_reasoning_instead_of_silently_dropping_it() -> None:
    with pytest.raises(ValueError, match="无法安全转换推理参数"):
        ai_generation_settings.pydantic_model_settings_payload(
            {
                "connection_type": "api",
                "provider_id": "deepseek",
                "api_style": "openai_compatible",
            },
            {"reasoning": {"mode": "disabled"}},
        )


def test_non_api_generation_settings_are_explicitly_unsupported() -> None:
    capabilities = ai_generation_settings.generation_capabilities(
        {"connection_type": "cli", "provider_id": "openai"}
    )

    assert capabilities["status"] == "unsupported"
    assert capabilities["reasoning"]["status"] == "unsupported"
