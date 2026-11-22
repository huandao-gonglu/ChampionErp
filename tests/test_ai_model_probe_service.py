from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageChops
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from erp_web.services import ai_gateway_probe, ai_model_probe_service, ai_provider_catalog
from erp_web.services import ai_gateway_browser_provider, ai_gateway_cli_provider
from erp_web.services.ai_model_errors import AIHTTPError
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


def test_chat_reply_and_json_nonce_are_validated_without_declared_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def request_for_probe(**kwargs):
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
        return ModelResponse(parts=[TextPart("Hello! How can I help?")])

    def request_json_for_probe(**kwargs):
        assert kwargs["messages"] == ai_gateway_probe._json_probe_default_messages()
        return {"status": "ok", "details": [1, 2, 3]}, ""

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
    assert (
        chat["results"]["chat"]["capability_profile"]["probe_version"]
        == "chat.v3"
    )
    assert structured["results"]["json"]["status"] == "supported"
    profile = structured["results"]["json"]["capability_profile"]
    assert profile["version"] == 2
    assert profile["probe_version"] == "json.v4"
    assert len(profile["configuration_fingerprint"]) == 64
    assert "secret" not in json.dumps(profile)
    assert model["capabilities"] == []


def test_chat_probe_accepts_any_non_empty_reply() -> None:
    ai_gateway_probe._validate_chat_probe_text("Hello! How can I help?")

    with pytest.raises(ai_gateway_probe.CapabilityProbeUnsupported):
        ai_gateway_probe._validate_chat_probe_text("  \n  ")


def test_json_probe_only_requires_a_json_object() -> None:
    ai_gateway_probe._validate_json_probe_data({})
    ai_gateway_probe._validate_json_probe_data(
        {"status": "ok", "details": [1, 2, 3]}
    )

    with pytest.raises(ai_gateway_probe.CapabilityProbeUnsupported):
        ai_gateway_probe._validate_json_probe_data([])  # type: ignore[arg-type]


def test_json_probe_ignores_user_messages_and_uses_system_test(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    custom_messages = [
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": "Return a custom JSON object."},
    ]
    _install_probe_binding(monkeypatch, model)

    def request_json_for_probe(**kwargs):
        assert kwargs["messages"] == ai_gateway_probe._json_probe_default_messages()
        return {"custom": True}, ""

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        request_json_for_probe,
    )

    result = ai_model_probe_service.test_api_model(
        tmp_path,
        model,
        {"probe_only_capability": "json", "probe_messages": custom_messages},
    )

    assert result["supported_capabilities"] == ["json"]


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


@pytest.mark.parametrize("connection_type", ["cli", "browser"])
def test_local_json_probe_ignores_user_test_messages(
    tmp_path: Path,
    monkeypatch,
    connection_type: str,
) -> None:
    model = {**_model(), "connection_type": connection_type, "cli_tool": "codex"}
    raw = {
        "probe_only_capability": "json",
        "probe_messages": [{"role": "user", "content": "用户自定义测试"}],
    }
    calls = []
    if connection_type == "cli":
        def run_text(app_dir, model, prompt, timeout):
            calls.append(prompt)
            return '{"ok": true}'

        monkeypatch.setattr(ai_gateway_cli_provider, "_run_codex_cli_text", run_text)
        monkeypatch.setattr(
            ai_gateway_cli_provider,
            "call_desktop",
            lambda *args, **kwargs: {"command": "codex", "command_path": "/test/codex"},
        )
        provider = ai_gateway_cli_provider.CodexCliProvider()
    else:
        from types import SimpleNamespace

        page = SimpleNamespace(
            text='{"ok": true}', ready=True, browser_url="https://example.invalid",
            profile_dir=str(tmp_path), port=19335,
        )

        def chat_result(app_dir, model, messages, timeout, **kwargs):
            calls.append(json.dumps(messages))
            return page

        monkeypatch.setattr(ai_gateway_browser_provider, "_browser_chat_result", chat_result)
        monkeypatch.setattr(
            ai_gateway_browser_provider.browser_ai_runtime,
            "open_browser_ai_page",
            lambda *args, **kwargs: page,
        )
        provider = ai_gateway_browser_provider.BrowserAiProvider()

    result = provider.test_model(tmp_path, model, raw)

    assert result["supported_capabilities"] == ["json"]
    assert len(calls) == 1
    assert "用户自定义测试" not in calls[0]
    assert "Return one JSON object. Any keys and values are acceptable." in calls[0]


def test_web_search_probe_persists_the_verified_request_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    def request_json_for_probe(**kwargs):
        assert kwargs["output_type"] is ai_model_probe_service._WebSearchProbeOutput
        return (
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
        )

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        request_json_for_probe,
    )

    report = _run(tmp_path, model, "web_search")

    result = report["results"]["web_search"]
    assert result["status"] == "supported"
    assert result["request_mode"] == "web_search_options"
    assert result["capability_profile"]["request_mode"] == "web_search_options"


def test_web_search_probe_reports_plain_text_as_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def request_json_for_probe(**kwargs):
        del kwargs
        raise ai_model_probe_service.ai_direct_request_service.AiProbeStructuredOutputError(
            "模型返回的内容不是 JSON object。"
        )

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_json_for_probe",
        request_json_for_probe,
    )

    report = _run(tmp_path, model, "web_search")

    result = report["results"]["web_search"]
    assert result["status"] == "unsupported"
    assert result["error_code"] == "CAPABILITY_PROBE_UNSUPPORTED"
    assert result["error"] == (
        "模型返回了普通文本或不符合约定的结果，未能证明实时联网能力。"
    )
    assert "AI response JSON" not in result["error"]


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
        assert kwargs["allow_text_output"] is (calls > 1)
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
    assert result["capability_profile"]["supports_tool_choice_required"] is True


def test_function_call_probe_falls_back_on_http_400_and_records_profile_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    calls: list[bool] = []
    token = "thinking-fallback-token"
    monkeypatch.setattr(ai_gateway_probe.secrets, "token_hex", lambda _: token)

    def request_for_probe(**kwargs):
        allow_text = bool(kwargs["allow_text_output"])
        calls.append(allow_text)
        if len(calls) == 1:
            assert allow_text is False
            raise AIHTTPError(
                status_code=400,
                reason="Bad Request",
                detail="Thinking mode does not support this tool_choice",
                model_id="probe-model",
                model_name="probe-model",
                api_style="openai_compatible",
                endpoint="https://models.example.invalid/v1",
            )
        if len(calls) == 2:
            assert allow_text is True
            return ModelResponse(
                parts=[
                    ThinkingPart(content="need to call noop"),
                    ToolCallPart(
                        tool_name="noop",
                        args={"probe_token": token},
                        tool_call_id="probe-call",
                    ),
                ]
            )
        assert allow_text is True
        return ModelResponse(parts=[TextPart(f"probe-complete:{token}")])

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        request_for_probe,
    )

    report = _run(tmp_path, model, "tool_calling")

    result = report["results"]["tool_calling"]
    assert calls == [False, True, True]
    assert result["status"] == "supported"
    assert result["capability_profile"]["supports_tool_choice_required"] is False
    assert result["capability_profile"]["observed_thinking"] is True


def test_chat_probe_records_observed_thinking_when_thinking_part_is_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "request_for_probe",
        lambda **kwargs: ModelResponse(
            parts=[ThinkingPart(content="greeting user"), TextPart("Hello!")]
        ),
    )

    report = _run(tmp_path, model, "chat")

    result = report["results"]["chat"]
    assert result["status"] == "supported"
    assert result["capability_profile"]["observed_thinking"] is True


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

    def generate_images_for_probe(**kwargs):
        assert kwargs["prompt"] == "single small blue square"
        return [{"b64_json": generated}]

    def edit_image_for_probe(**kwargs):
        assert kwargs["prompt"] == "Change the red image to blue while preserving its dimensions."
        return [{"b64_json": edited, "source_id": "probe"}]

    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "generate_images_for_probe",
        generate_images_for_probe,
    )
    monkeypatch.setattr(
        ai_model_probe_service.ai_direct_request_service,
        "edit_image_for_probe",
        edit_image_for_probe,
    )

    generated_report = ai_model_probe_service.test_api_model(tmp_path, model, {
        "probe_only_capability": "image_generate", "probe_image_prompt": "用户自定义生图内容",
    })
    edited_report = ai_model_probe_service.test_api_model(tmp_path, model, {
        "probe_only_capability": "image_edit", "probe_image_prompt": "用户自定义编辑内容",
    })

    assert generated_report["capability_results"]["image_generate"]["status"] == "supported"
    assert edited_report["capability_results"]["image_edit"]["status"] == "supported"
    assert "conversation_id" not in edited_report["capability_results"]["image_edit"]


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


def test_payment_required_402_is_inconclusive_non_retryable_not_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model()
    _install_probe_binding(monkeypatch, model)

    def fail(**kwargs):
        raise ModelHTTPError(
            402,
            "probe-model",
            {"error": {"message": "Insufficient Balance"}},
        )

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
    assert result["error_code"] == "CAPABILITY_PROBE_PAYMENT_REQUIRED"
    assert result["retryable"] is False
    assert "余额不足" in result["error"]


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


@pytest.mark.parametrize("api_style", ["openai_compatible", "openai_responses"])
@pytest.mark.parametrize("reverse", [False, True])
def test_image_understanding_reads_two_ordered_pngs_via_provider(
    tmp_path, monkeypatch, api_style, reverse,
):
    # 从发往服务商的真实 PNG 像素还原答案，覆盖协议编码以及顺序校验。
    def respond(request):
        body = json.loads(request.content)
        chat = api_style == "openai_compatible"
        parts = body["messages" if chat else "input"][0]["content"]
        images = [part for part in parts if part["type"] in {"image_url", "input_image"}]
        assert len(images) == 2
        answers = []
        for part in images:
            url = part["image_url"]["url"] if chat else part["image_url"]
            assert url.startswith("data:image/png;base64,")
            with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
                background = Image.new("RGB", image.size, "white")
                bounds = ImageChops.difference(image, background).getbbox()
                area, color = next(pair for pair in image.getcolors() if pair[1] != (255, 255, 255))
                colors = {
                    (255, 0, 0): "红色", (0, 128, 0): "绿色",
                    (0, 0, 255): "蓝色", (255, 255, 0): "黄色",
                }
                ratio = area / ((bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
                if ratio > 0.9:
                    shape = "正方形"
                elif ratio > 0.65:
                    shape = "圆形"
                else:
                    shape = "三角形"
                answers.append(colors[color] + shape)
        assert answers[0] != answers[1]
        answer = "、".join(reversed(answers) if reverse else answers)
        if chat:
            payload = {"id": "chat", "model": "test", "object": "chat.completion", "created": 0,
                       "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                                    "finish_reason": "stop"}]}
        else:
            payload = {"id": "resp", "model": "test", "object": "response", "created_at": 0,
                       "status": "completed", "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
                       "output": [{"type": "message", "id": "msg", "role": "assistant", "status": "completed",
                                   "content": [{"type": "output_text", "text": answer, "annotations": []}]}]}
        return httpx.Response(200, json=payload)

    client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(
        ai_provider_catalog,
        "create_pydantic_provider",
        lambda *args, **kwargs: OpenAIProvider(openai_client=client),
    )
    model = {**_model(), "api_style": api_style}
    result = _run(tmp_path, model, "image_understanding")["results"]["image_understanding"]
    if reverse:
        assert result["status"] == "inconclusive"
        assert "capability_profile" not in result
    else:
        assert result["status"] == "supported", result
        assert result["capability_profile"]["operation"] == "model.request.image_input"
        assert result["capability_profile"]["configuration_fingerprint"]


def test_list_remote_models_supports_anthropic_and_google_providers(monkeypatch) -> None:
    from types import SimpleNamespace
    from erp_web.services import ai_model_discovery

    class FakeAnthropicProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        class _Models:
            async def list(self, *, timeout):
                assert timeout == 15.0
                return SimpleNamespace(
                    data=[SimpleNamespace(id="claude-sonnet-4-6"), SimpleNamespace(id="claude-opus-4-6")]
                )

        client = SimpleNamespace(models=_Models())

    class FakeGooglePager:
        def __init__(self, items):
            self._items = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._items)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeGoogleProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        class _Models:
            async def list(self):
                return FakeGooglePager(
                    [
                        SimpleNamespace(name="models/gemini-2.5-flash"),
                        SimpleNamespace(name="models/gemini-2.5-pro"),
                    ]
                )

        client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))

    def fake_create(provider_id, *, base_url, api_key):
        del base_url, api_key
        return FakeAnthropicProvider() if provider_id == "anthropic" else FakeGoogleProvider()

    monkeypatch.setattr(ai_provider_catalog, "create_pydantic_provider", fake_create)

    assert ai_model_discovery.list_remote_models(
        "anthropic", "https://api.anthropic.com", "sk-ant", 15
    ) == [
        {"id": "claude-sonnet-4-6", "name": "claude-sonnet-4-6"},
        {"id": "claude-opus-4-6", "name": "claude-opus-4-6"},
    ]
    assert ai_model_discovery.list_remote_models(
        "google", "https://generativelanguage.googleapis.com", "gk-test", 15
    ) == [
        {"id": "gemini-2.5-flash", "name": "gemini-2.5-flash"},
        {"id": "gemini-2.5-pro", "name": "gemini-2.5-pro"},
    ]
