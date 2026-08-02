from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from erp_web.services.ai_agent_observability import (
    AI_WORK_REDACTED_VALUE,
    AI_WORK_TRUNCATED_VALUE,
    build_agent_transcript_observation,
    sanitize_ai_work_value,
)


def test_ai_work_projection_keeps_business_content_and_redacts_credentials() -> None:
    value = sanitize_ai_work_value(
        {
            "probe_token": "x789",
            "title": "Ventilador",
            "apiKey": "sk-private-value-12345678",
            "nested": {
                "access_token": "token-private",
                "note": "Authorization: Bearer abc.def.ghi",
            },
        }
    )

    assert value == {
        "probe_token": "x789",
        "title": "Ventilador",
        "apiKey": AI_WORK_REDACTED_VALUE,
        "nested": {
            "access_token": AI_WORK_REDACTED_VALUE,
            "note": f"Authorization={AI_WORK_REDACTED_VALUE}",
        },
    }


def test_agent_transcript_projection_keeps_failed_model_output_and_is_bounded() -> None:
    observation = build_agent_transcript_observation(
        [
            ModelRequest(parts=[UserPromptPart("匹配风扇类目")], instructions="先搜索类目"),
            ModelResponse(parts=[TextPart("MLM-INVENTED")]),
            ModelResponse(parts=[TextPart("x" * (40 * 1024))]),
        ]
    )

    assert observation["schema_version"] == "agent.transcript.v1"
    assert observation["messages"][0]["instructions"] == "先搜索类目"
    assert observation["messages"][1]["parts"][0]["content"] == "MLM-INVENTED"
    assert observation["messages"][2]["parts"][0]["content"].endswith(
        AI_WORK_TRUNCATED_VALUE
    )
