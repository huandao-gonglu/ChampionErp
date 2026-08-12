from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    FilePart,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import ImageGenerationTool
from pydantic_ai.providers import Provider
from pydantic_ai.profiles import ModelProfile
from openai.types.responses.response import Response

from erp_web.context import get_context
from erp_web.services import ai_direct_request_service, ai_model_probe_service
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_model_errors import (
    AIHTTPError,
    AIModelRequestError,
    map_pydantic_model_error,
)
from erp_web.services.ai_pydantic_image_model import OpenAIImagesModel


def _binding(
    *,
    output: str = '{"ok":true}',
    required: tuple[str, ...] = ("chat", "json"),
) -> PydanticModelBinding:
    return PydanticModelBinding(
        model=TestModel(custom_output_text=output),
        model_settings={},
        model_id="test-model",
        model_name="test-model",
        provider_id="openai",
        provider_family="openai",
        api_style="openai_compatible",
        model_config={
            "id": "test-model",
            "provider_id": "openai",
            "provider": "Test",
            "base_url": "https://api.example.com/v1",
        },
        required_capabilities=required,
    )


def test_direct_chat_json_uses_pydantic_model_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_factory(model, **kwargs):
        captured.update(kwargs)
        return _binding()

    monkeypatch.setattr(
        ai_direct_request_service,
        "create_pydantic_model_binding",
        fake_factory,
    )
    result = ai_direct_request_service.chat_json(
        app_dir=tmp_path,
        use_case_id="copy.generate",
        model={"id": "test-model"},
        required_capabilities=("chat", "json"),
        messages=[
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Return ok."},
        ],
        generation_settings={"temperature": 0},
        temperature=0.7,
        max_tokens=64,
        timeout_seconds=30,
        response_format=True,
        stream=False,
    )

    assert result == {"ok": True}
    assert captured["generation_settings"] == {
        "temperature": 0,
        "max_output_tokens": 64,
    }
    assert captured["required_capabilities"] == ("chat", "json")


def test_direct_stream_emits_pydantic_text_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_direct_request_service,
        "create_pydantic_model_binding",
        lambda *args, **kwargs: _binding(output='{"streamed":true}'),
    )
    deltas: list[str] = []

    result = ai_direct_request_service.chat_json(
        app_dir=tmp_path,
        use_case_id="copy.generate",
        model={"id": "test-model"},
        required_capabilities=("chat", "json"),
        messages=[{"role": "user", "content": "Return JSON."}],
        generation_settings=None,
        temperature=0,
        max_tokens=None,
        timeout_seconds=30,
        response_format=True,
        stream=True,
        token_callback=deltas.append,
    )

    assert result == {"streamed": True}
    assert "".join(deltas) == '{"streamed":true}'


def test_direct_stream_records_reasoning_separately_from_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def stream_response(messages, agent_info):
        del messages, agent_info
        yield {0: DeltaThinkingPart(content="先分析属性")}
        yield {0: DeltaThinkingPart(content="，再生成结果")}
        yield '{"streamed":true}'

    binding = replace(
        _binding(output='{"streamed":true}'),
        model=FunctionModel(stream_function=stream_response),
    )
    monkeypatch.setattr(
        ai_direct_request_service,
        "create_pydantic_model_binding",
        lambda *args, **kwargs: binding,
    )
    conversation = get_context().ai_journal.start_conversation(
        use_case_id="category.attribute_fill",
        capability="chat_json",
        provider_id="pydantic_direct",
        model={"id": "test-model", "model": "test-model"},
        stream=True,
    )
    text_deltas: list[str] = []

    result = ai_direct_request_service.chat_json(
        app_dir=tmp_path,
        use_case_id="category.attribute_fill",
        model={"id": "test-model"},
        required_capabilities=("chat", "json"),
        messages=[{"role": "user", "content": "Return JSON."}],
        generation_settings=None,
        temperature=0,
        max_tokens=None,
        timeout_seconds=30,
        response_format=True,
        stream=True,
        recorder=conversation,
        token_callback=text_deltas.append,
    )

    assert result == {"streamed": True}
    assert text_deltas == ['{"streamed":true}']
    events = get_context().ai_journal.read_events(conversation.conversation_id)
    projected = [
        (event["type"], event.get("delta"))
        for event in events
        if event["type"] not in {"RUN_STARTED", "CUSTOM"}
    ]
    assert projected == [
        ("REASONING_MESSAGE_START", None),
        ("REASONING_MESSAGE_CONTENT", "先分析属性"),
        ("REASONING_MESSAGE_CONTENT", "，再生成结果"),
        ("REASONING_MESSAGE_END", None),
        ("TEXT_MESSAGE_START", None),
        ("TEXT_MESSAGE_CONTENT", '{"streamed":true}'),
        ("TEXT_MESSAGE_END", None),
    ]


def test_responses_json_direct_request_passes_system_prompt_as_instructions(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    class Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return Response.model_validate(
                {
                    "id": "resp_test",
                    "created_at": 0,
                    "model": "qwen3.7-plus",
                    "object": "response",
                    "output": [
                        {
                            "id": "msg_test",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"probe_token":"nonce","ok":true}',
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "status": "completed",
                }
            )

    class Client:
        base_url = "https://api.example.com/v1"
        responses = Responses()

    class FakeProvider(Provider[object]):
        _client = Client()

        @property
        def name(self) -> str:
            return "openai"

        @property
        def base_url(self) -> str:
            return "https://api.example.com/v1"

        @property
        def client(self) -> object:
            return self._client

    binding = replace(
        _binding(),
        model=OpenAIResponsesModel(
            "qwen3.7-plus",
            provider=FakeProvider(),
            profile=ModelProfile(supports_json_object_output=True),
        ),
        api_style="openai_responses",
    )

    result, request_mode = ai_direct_request_service.request_json_for_probe(
        app_dir=tmp_path,
        binding=binding,
        messages=[
            {"role": "system", "content": "只返回合法 JSON。"},
            {"role": "user", "content": "返回探测结果 JSON。"},
        ],
    )

    assert result == {"probe_token": "nonce", "ok": True}
    assert request_mode == ""
    assert calls[0]["input"] == [
        {"role": "system", "content": "只返回合法 JSON。"},
        {"role": "user", "content": "返回探测结果 JSON。"},
    ]
    assert calls[0]["text"] == {"format": {"type": "json_object"}}


def test_probe_direct_request_records_text_and_function_call_parts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = ModelResponse(
        parts=[
            TextPart("provider-text"),
            ToolCallPart(
                tool_name="noop",
                args={"probe_token": "nonce"},
                tool_call_id="call-1",
            ),
        ]
    )
    monkeypatch.setattr(
        ai_direct_request_service,
        "_request",
        lambda **kwargs: response,
    )
    conversation = get_context().ai_journal.start_conversation(
        use_case_id="config.ai_model_probe",
        capability="tool_calling",
        provider_id="openai",
        model={"id": "probe", "model": "probe"},
    )

    actual = ai_direct_request_service.request_for_probe(
        app_dir=tmp_path,
        binding=_binding(required=("tool_calling",)),
        messages=[{"role": "user", "content": "call noop"}],
        recorder=conversation,
        response_phase="tool_selection",
    )

    assert actual is response
    events = get_context().ai_journal.read_events(conversation.conversation_id)
    assert [
        event.get("delta")
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT"
    ] == ["provider-text"]
    tool_event = next(
        event
        for event in events
        if event.get("name") == "capability_probe.tool_call"
    )
    assert tool_event["value"] == {
        "phase": "tool_selection",
        "tool_name": "noop",
        "tool_call_id": "call-1",
        "args": {"probe_token": "nonce"},
    }


def test_web_search_uses_the_verified_capability_profile_recipe() -> None:
    binding = replace(
        _binding(required=("web_search",)),
        model=TestModel(profile=ModelProfile(supported_native_tools=frozenset())),
    )
    binding.model_config["capability_profiles"] = {
        "web_search": {
            "version": 2,
            "tested": True,
            "request_mode": "enable_search",
        }
    }

    native_tools, settings = ai_direct_request_service._web_search_parameters(
        binding,
        binding.model_settings,
    )

    assert native_tools == []
    assert settings["extra_body"] == {
        "enable_search": True,
        "search_options": {"forced_search": True},
    }
    assert ai_direct_request_service.web_search_request_mode(binding) == "enable_search"


def test_image_response_is_normalized_from_pydantic_file_part() -> None:
    raw = b"\x89PNG\r\n\x1a\nimage"
    response = ModelResponse(
        parts=[
            FilePart(
                content=BinaryContent(data=raw, media_type="image/png")
            )
        ]
    )

    results = ai_direct_request_service._image_results(
        response,
        provider_name="Test",
        mode="edit",
        source_id="source-1",
    )

    assert results == [
        {
            "provider": "Test",
            "mode": "edit",
            "source_id": "source-1",
            "suffix": ".png",
            "b64_json": base64.b64encode(raw).decode("ascii"),
        }
    ]


def test_image_edit_failure_does_not_fallback_to_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(ai_model_probe_service._image_edit_probe_bytes())
    calls: list[str] = []

    monkeypatch.setattr(
        ai_direct_request_service,
        "create_pydantic_model_binding",
        lambda *args, **kwargs: _binding(required=("image_edit",)),
    )

    def fail_request(**kwargs):
        calls.append(kwargs["action"])
        raise RuntimeError("edit failed")

    monkeypatch.setattr(ai_direct_request_service, "_image_request", fail_request)

    with pytest.raises(RuntimeError, match="edit failed"):
        ai_direct_request_service.edit_images(
            app_dir=tmp_path,
            use_case_id="image.translate",
            model={"id": "test-model"},
            required_capabilities=("image_edit",),
            prompt="translate",
            images=[{"id": "source-1", "path": str(source)}],
            size="1024x1024",
            quality="medium",
            count=1,
            timeout_seconds=30,
        )

    assert calls == ["edit"]


def test_focused_image_model_returns_pydantic_file_part() -> None:
    raw = ai_model_probe_service._image_edit_probe_bytes()
    calls: list[dict[str, object]] = []

    class Images:
        async def edit(self, **kwargs):
            calls.append(kwargs)
            item = type(
                "ImageItem",
                (),
                {"b64_json": base64.b64encode(raw).decode("ascii"), "url": None},
            )()
            return type("ImageResponse", (), {"data": [item]})()

    class Client:
        images = Images()

    class FakeProvider(Provider[object]):
        _client = Client()

        @property
        def name(self) -> str:
            return "test"

        @property
        def base_url(self) -> str:
            return "https://api.example.com/v1"

        @property
        def client(self) -> object:
            return self._client

    model = OpenAIImagesModel("gpt-image-test", provider=FakeProvider())
    response = asyncio.run(
        model.request(
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            [
                                "edit",
                                BinaryContent(data=raw, media_type="image/png"),
                            ]
                        )
                    ]
                )
            ],
            {},
            ModelRequestParameters(
                native_tools=[ImageGenerationTool(action="edit")],
                allow_text_output=False,
                allow_image_output=True,
            ),
        )
    )

    assert response.files == [BinaryContent(data=raw, media_type="image/png")]
    assert calls[0]["model"] == "gpt-image-test"
    assert calls[0]["image"][0] == "source-1.png"


def test_pydantic_http_error_is_stable_and_redacted() -> None:
    mapped = map_pydantic_model_error(
        ModelHTTPError(
            403,
            "test-model",
            {
                "error": {
                    "message": "Bearer secret-token sk-very-secret-key was rejected"
                }
            },
        ),
        model_id="model-1",
        model_name="test-model",
        api_style="openai_compatible",
        base_url="https://api.example.com/v1?token=secret",
    )

    assert isinstance(mapped, AIHTTPError)
    assert mapped.status_code == 403
    assert mapped.endpoint == "api.example.com/openai_compatible"
    assert "secret-token" not in str(mapped)
    assert "very-secret-key" not in str(mapped)


def test_pydantic_http_error_keeps_code_message_and_request_id() -> None:
    mapped = map_pydantic_model_error(
        ModelHTTPError(
            400,
            "test-model",
            {
                "error": {
                    "code": "invalid_tool_schema",
                    "message": "Tool schema is not supported.",
                },
                "request_id": "request-123",
            },
        ),
        model_id="model-1",
        model_name="test-model",
        api_style="openai_responses",
        base_url="https://api.example.com/v1",
    )

    assert isinstance(mapped, AIHTTPError)
    assert mapped.detail == (
        "code=invalid_tool_schema; message=Tool schema is not supported.; "
        "request_id=request-123"
    )


def test_pydantic_api_error_keeps_provider_message_and_only_redacts_secret() -> None:
    mapped = map_pydantic_model_error(
        ModelAPIError(
            "test-model",
            "Provider connection failed for sk-very-secret-provider-key",
        ),
        model_id="model-1",
        model_name="test-model",
        api_style="openai_responses",
        base_url="https://api.example.com/v1",
    )

    assert isinstance(mapped, AIModelRequestError)
    assert "Provider connection failed" in str(mapped)
    assert "very-secret-provider-key" not in str(mapped)
