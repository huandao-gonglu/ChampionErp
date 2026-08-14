from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.test import TestModel

from erp_web.services import ai_gateway_probe, ai_model_probe_service
from erp_web.services.ai_model_factory import PydanticModelBinding


def _model() -> dict[str, object]:
    return {
        "id": "probe-model",
        "name": "Probe Model",
        "connection_type": "api",
        "provider_id": "openai",
        "provider": "OpenAI",
        "api_style": "openai_compatible",
        "base_url": "https://models.example.invalid/v1",
        "api_key": "secret",
        "model": "probe-model",
        "capabilities": [],
    }


def _binding(model: dict[str, object], capability: str) -> PydanticModelBinding:
    return PydanticModelBinding(
        model=TestModel(),
        model_settings={},
        model_id="probe-model",
        model_name="probe-model",
        provider_id="openai",
        provider_family="openai",
        api_style="openai_compatible",
        model_config=model,
        required_capabilities=(capability,),
    )


def _install_probe_binding(monkeypatch, model: dict[str, object]) -> None:
    def create_binding(_model, *, probe_capability, **kwargs):
        del _model, kwargs
        return _binding(model, probe_capability)

    monkeypatch.setattr(
        ai_model_probe_service,
        "create_pydantic_probe_binding",
        create_binding,
    )


def _run(
    tmp_path: Path,
    model: dict[str, object],
    capability: str,
) -> dict[str, object]:
    return ai_model_probe_service.probe_model_capabilities(
        model,
        "ignored",
        "ignored",
        [capability],
        10,
        app_dir=tmp_path,
    )


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_edit_probe_fixture_is_a_decodable_red_png() -> None:
    source = ai_model_probe_service._image_edit_probe_bytes()

    with Image.open(BytesIO(source)) as image:
        image.load()
        assert image.format == "PNG"
        assert image.size == (1254, 1254)
        red, green, blue = image.convert("RGB").getpixel((0, 0))

    assert red > green
    assert red > blue


def test_chat_and_json_probes_validate_nonce_without_declared_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def request_for_probe(**kwargs):
        token = kwargs["messages"][-1]["content"]
        return ModelResponse(parts=[TextPart(token)])

    def request_json_for_probe(**kwargs):
        challenge = json.loads(kwargs["messages"][-1]["content"])
        return ai_gateway_probe._json_probe_expected_data(
            challenge["probe_token"]
        ), ""

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        request_for_probe,
    )
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        request_json_for_probe,
    )

    chat = _run(tmp_path, model, "chat")
    structured = _run(tmp_path, model, "json")

    assert chat["results"]["chat"]["status"] == "supported"
    assert structured["results"]["json"]["status"] == "supported"
    profile = structured["results"]["json"]["capability_profile"]
    assert profile["version"] == 2
    assert profile["probe_version"] == "json.v3"
    assert len(profile["configuration_fingerprint"]) == 64
    assert "secret" not in json.dumps(profile)
    assert model["capabilities"] == []


def test_json_probe_requires_exact_array_transformation() -> None:
    token = "nonce"
    expected = ai_gateway_probe._json_probe_expected_data(token)

    ai_gateway_probe._validate_json_probe_data(expected, token)

    invalid_results = (
        ai_gateway_probe._json_probe_challenge_payload(token),
        {"probe_token": token, "ok": True},
        {"probe_token": token, "result": list(reversed(expected["result"]))},
        {"probe_token": token, "result": [str(item) for item in expected["result"]]},
        {**expected, "explanation": "done"},
        {"probe_token": "wrong", "result": expected["result"]},
    )
    for invalid in invalid_results:
        with pytest.raises(ai_gateway_probe.CapabilityProbeUnsupported):
            ai_gateway_probe._validate_json_probe_data(invalid, "nonce")


def test_json_probe_challenge_has_mixed_parity_and_variable_rules() -> None:
    first = ai_gateway_probe._json_probe_challenge_payload("x789")
    second = ai_gateway_probe._json_probe_challenge_payload("x790")

    assert set(first) == {"numbers", "rules", "probe_token"}
    assert first["probe_token"] == "x789"
    assert len(first["numbers"]) == 5
    assert sum(number % 2 == 0 for number in first["numbers"]) == 2
    assert sum(number % 2 != 0 for number in first["numbers"]) == 3
    assert first["rules"]["remove_even"] is True
    assert first["rules"]["sort"] == "ascending"
    assert first != second


def test_internal_probe_assertion_is_inconclusive_not_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    result = _run(tmp_path, model, "json")

    probe = result["results"]["json"]
    assert probe["status"] == "inconclusive"
    assert probe["error_code"] == "CAPABILITY_PROBE_INTERNAL_ERROR"


def test_web_search_probe_persists_the_verified_request_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        lambda **kwargs: (
            {
                "can_access_web": True,
                "source_url": "https://weather.example.invalid/chengdu",
                "location": "成都",
                "date": ai_gateway_probe._web_search_probe_date_iso(),
                "weather": "晴",
                "temperature": "25 C",
                "evidence": "实时天气页面",
            },
            "web_search_options",
        ),
    )

    report = _run(tmp_path, model, "web_search")

    result = report["results"]["web_search"]
    assert result["status"] == "supported"
    assert result["request_mode"] == "web_search_options"
    assert result["capability_profile"]["request_mode"] == "web_search_options"


def test_function_call_probe_requires_a_complete_exact_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    calls = 0
    token = "function-probe-token"
    monkeypatch.setattr(ai_gateway_probe.secrets, "token_hex", lambda _: token)

    def request_for_probe(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["allow_text_output"] is True
        schema = kwargs["function_tools"][0].parameters_json_schema
        assert schema == {
            "type": "object",
            "properties": {
                "probe_token": {
                    "type": "string",
                    "description": "原样传入用户消息里的 probe_token。",
                }
            },
            "required": ["probe_token"],
        }
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="noop",
                        args={"probe_token": token},
                        tool_call_id="probe-call",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(f"probe-complete:{token}")])

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        request_for_probe,
    )

    report = _run(tmp_path, model, "tool_calling")

    result = report["results"]["tool_calling"]
    assert calls == 2
    assert result["status"] == "supported"
    assert result["capability_profile"]["strategy"] == "function_tool_round_trip"


def test_wrong_function_tool_name_is_not_a_false_positive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        lambda **kwargs: ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="unexpected_tool",
                    args={},
                    tool_call_id="wrong-call",
                )
            ]
        ),
    )

    report = _run(tmp_path, model, "tool_calling")

    result = report["results"]["tool_calling"]
    assert result["status"] == "unsupported"
    assert result["error_code"] == "CAPABILITY_PROBE_UNSUPPORTED"
    assert "unexpected_tool" in result["error"]


def test_image_generate_and_edit_probes_validate_real_image_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    generated = base64.b64encode(_png_bytes((0, 0, 255))).decode()
    edited = base64.b64encode(_png_bytes((0, 0, 255))).decode()
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "generate_images_for_probe",
        lambda **kwargs: [{"b64_json": generated}],
    )
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "edit_image_for_probe",
        lambda **kwargs: [{"b64_json": edited, "source_id": "probe"}],
    )

    generated_report = _run(tmp_path, model, "image_generate")
    edited_report = _run(tmp_path, model, "image_edit")

    assert generated_report["results"]["image_generate"]["status"] == "supported"
    assert edited_report["results"]["image_edit"]["status"] == "supported"
    assert "conversation_id" not in edited_report["results"]["image_edit"]


def test_transient_provider_failure_is_inconclusive_instead_of_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def fail(**kwargs):
        raise ModelHTTPError(429, "probe-model", {"error": "rate limited"})

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        fail,
    )

    report = _run(tmp_path, model, "chat")

    result = report["results"]["chat"]
    assert report["unsupported"] == []
    assert report["inconclusive"] == ["chat"]
    assert result["status"] == "inconclusive"
    assert result["retryable"] is True
    assert "conversation_id" not in result


def test_provider_error_keeps_safe_diagnostics_in_result_and_log(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def fail(**kwargs):
        raise ModelHTTPError(
            400,
            "probe-model",
            {
                "error": {
                    "code": "invalid_tool_schema",
                    "message": "authorization=secret-value is invalid",
                },
                "request_id": "request-123",
            },
        )

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        fail,
    )

    with caplog.at_level("WARNING", logger=ai_gateway_probe.__name__):
        report = _run(tmp_path, model, "chat")

    error = report["results"]["chat"]["error"]
    assert "code=invalid_tool_schema" in error
    assert "request_id=request-123" in error
    assert "secret-value" not in error
    assert "code=invalid_tool_schema" in caplog.text
    assert "request_id=request-123" in caplog.text
    assert "secret-value" not in caplog.text
