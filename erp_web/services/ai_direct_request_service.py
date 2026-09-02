"""非 Agent API 推理的 Pydantic Direct Model 唯一执行边界。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TypeVar

from pydantic_ai import direct
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, OutputObjectDefinition
from pydantic_ai.native_tools import ImageGenerationTool, WebSearchTool
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from . import ai_generation_settings, ai_model_config, image_service
from .ai_agent_instrumentation import AiAgentInstrumentation
from .ai_gateway_parsing import parse_json_text
from .ai_model_errors import map_pydantic_model_error
from .ai_model_factory import PydanticModelBinding, create_pydantic_model_binding
from .ai_structured_output import (
    object_json_schema,
    output_adapter,
    validate_structured_output,
)


PYDANTIC_DIRECT_PROVIDER_ID = "pydantic_direct"
OutputT = TypeVar("OutputT")


def _messages(value: Sequence[dict[str, str]]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    instructions: list[str] = []
    for item in value:
        role = str(item.get("role") or "user").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content)]))
        elif role == "system":
            instructions.append(content)
        else:
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
    if instructions:
        instruction_text = "\n\n".join(instructions)
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, ModelRequest):
                continue
            if message.instructions:
                instruction_text = f"{instruction_text}\n\n{message.instructions}"
            messages[index] = replace(message, instructions=instruction_text)
            break
        else:
            messages.append(ModelRequest(parts=[], instructions=instruction_text))
    if not messages:
        raise ValueError("AI 对话消息不能为空。")
    return messages


def _probe_model_messages(
    value: Sequence[dict[str, str] | ModelMessage],
) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    pending_records: list[dict[str, str]] = []

    def flush_records() -> None:
        if pending_records:
            messages.extend(_messages(pending_records))
            pending_records.clear()

    for item in value:
        if isinstance(item, (ModelRequest, ModelResponse)):
            flush_records()
            messages.append(item)
        elif isinstance(item, dict):
            pending_records.append(item)
        else:
            raise TypeError("能力探测消息必须是对话字典或 Pydantic ModelMessage。")
    flush_records()
    if not messages:
        raise ValueError("AI 能力探测消息不能为空。")
    return messages


def _merge_generation_settings(
    configured: dict[str, Any] | None,
    *,
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    if temperature is not None:
        defaults["temperature"] = temperature
    if max_tokens is not None:
        defaults["max_output_tokens"] = max_tokens
    defaults.update(dict(configured or {}))
    return defaults


def _web_search_parameters(
    binding: PydanticModelBinding,
    settings: ModelSettings,
) -> tuple[list[WebSearchTool], ModelSettings]:
    if ai_model_config.CAP_WEB_SEARCH not in binding.required_capabilities:
        return [], settings

    extra = (
        binding.model_config.get("extra")
        if isinstance(binding.model_config.get("extra"), dict)
        else {}
    )
    custom_body = extra.get("web_search_request_body")
    if isinstance(custom_body, dict) and custom_body:
        unsupported = sorted(
            set(custom_body)
            - {"enable_search", "search_options", "web_search_options"}
        )
        if unsupported:
            raise ValueError(
                "web_search_request_body 含有未受控协议字段："
                + ", ".join(unsupported)
            )
        merged = dict(settings)
        merged_extra = (
            dict(merged.get("extra_body"))
            if isinstance(merged.get("extra_body"), dict)
            else {}
        )
        merged_extra.update(custom_body)
        merged["extra_body"] = merged_extra
        return [], ModelSettings(**merged)

    supported_native_tools = binding.model.profile.get(
        "supported_native_tools",
        frozenset(),
    )
    if WebSearchTool in supported_native_tools:
        return [WebSearchTool(search_context_size="medium")], settings

    # Alibaba/OpenAI-compatible 的 Pydantic Model 仍负责发请求；厂商扩展字段只从
    # 模型配置策略生成，并通过 ModelSettings.extra_body 进入公开 Model API。
    default_request_mode = (
        "enable_search"
        if binding.provider_family
        == ai_generation_settings.PROVIDER_FAMILY_ALIBABA
        else "web_search_options"
    )
    capability_profiles = (
        binding.model_config.get("capability_profiles")
        if isinstance(binding.model_config.get("capability_profiles"), dict)
        else {}
    )
    web_search_profile = (
        capability_profiles.get(ai_model_config.CAP_WEB_SEARCH)
        if isinstance(capability_profiles, dict)
        else {}
    )
    profile_request_mode = (
        str(web_search_profile.get("request_mode") or "").strip()
        if isinstance(web_search_profile, dict)
        else ""
    )
    request_mode = str(
        profile_request_mode
        or extra.get("web_search_request_mode")
        or default_request_mode
    )
    if request_mode == "openai_tools":
        raise ValueError("已验证的 OpenAI WebSearchTool 当前不可用，请重新探测联网能力。")
    provider_body: dict[str, Any]
    if request_mode == "web_search_options":
        provider_body = {
            "web_search_options": extra.get("web_search_options")
            or {"search_context_size": "medium"}
        }
    else:
        search_options = (
            dict(extra.get("search_options"))
            if isinstance(extra.get("search_options"), dict)
            else {}
        )
        search_options.setdefault("forced_search", True)
        provider_body = {
            "enable_search": True,
            "search_options": search_options,
        }
    merged = dict(settings)
    merged_extra = (
        dict(merged.get("extra_body"))
        if isinstance(merged.get("extra_body"), dict)
        else {}
    )
    merged_extra.update(provider_body)
    merged["extra_body"] = merged_extra
    return [], ModelSettings(**merged)


def web_search_request_mode(binding: PydanticModelBinding) -> str:
    """返回当前 binding 实际采用的联网请求策略。"""

    extra = (
        binding.model_config.get("extra")
        if isinstance(binding.model_config.get("extra"), dict)
        else {}
    )
    custom_body = extra.get("web_search_request_body")
    if isinstance(custom_body, dict):
        if (
            custom_body.get("enable_search") is not None
            or custom_body.get("search_options") is not None
        ):
            return "enable_search"
        if custom_body.get("web_search_options") is not None:
            return "web_search_options"
    supported_native_tools = binding.model.profile.get(
        "supported_native_tools",
        frozenset(),
    )
    if WebSearchTool in supported_native_tools:
        return "openai_tools"
    capability_profiles = (
        binding.model_config.get("capability_profiles")
        if isinstance(binding.model_config.get("capability_profiles"), dict)
        else {}
    )
    web_search_profile = (
        capability_profiles.get(ai_model_config.CAP_WEB_SEARCH)
        if isinstance(capability_profiles, dict)
        else {}
    )
    profile_request_mode = (
        str(web_search_profile.get("request_mode") or "").strip()
        if isinstance(web_search_profile, dict)
        else ""
    )
    if profile_request_mode:
        return profile_request_mode
    request_mode = str(extra.get("web_search_request_mode") or "").strip()
    if request_mode:
        return request_mode
    return (
        "enable_search"
        if binding.provider_family
        == ai_generation_settings.PROVIDER_FAMILY_ALIBABA
        else "web_search_options"
    )


def _instrumentation(
    app_dir: Path | str,
) -> AiAgentInstrumentation:
    return AiAgentInstrumentation(
        Path(app_dir) / "data" / "logs" / "ai_traces" / "direct_model_spans.jsonl"
    )


def _request(
    *,
    app_dir: Path | str,
    use_case_id: str,
    binding: PydanticModelBinding,
    messages: Sequence[ModelMessage],
    parameters: ModelRequestParameters,
    stream: bool,
    emit_text_delta: Callable[[str], None] | None,
    emit_reasoning_delta: Callable[[str], None] | None,
) -> ModelResponse:
    instrumentation = _instrumentation(app_dir)

    async def run() -> ModelResponse:
        async with binding.model:
            if not stream:
                return await direct.model_request(
                    binding.model,
                    messages,
                    model_settings=binding.model_settings,
                    model_request_parameters=parameters,
                    instrument=instrumentation.settings,
                )
            async with direct.model_request_stream(
                binding.model,
                messages,
                model_settings=binding.model_settings,
                model_request_parameters=parameters,
                instrument=instrumentation.settings,
            ) as response_stream:
                async for event in response_stream:
                    text_delta = ""
                    reasoning_delta = ""
                    if isinstance(event, PartStartEvent) and isinstance(
                        event.part, TextPart
                    ):
                        text_delta = event.part.content
                    elif isinstance(event, PartStartEvent) and isinstance(
                        event.part, ThinkingPart
                    ):
                        reasoning_delta = event.part.content
                    elif isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta, TextPartDelta
                    ):
                        text_delta = event.delta.content_delta
                    elif isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta, ThinkingPartDelta
                    ):
                        reasoning_delta = event.delta.content_delta or ""
                    if reasoning_delta and emit_reasoning_delta:
                        emit_reasoning_delta(reasoning_delta)
                    if text_delta and emit_text_delta:
                        emit_text_delta(text_delta)
                return response_stream.get()

    try:
        with instrumentation.start_run_span(use_case_id=use_case_id):
            return asyncio.run(run())
    finally:
        instrumentation.force_flush()
        instrumentation.shutdown()


def _chat_response(
    *,
    app_dir: Path | str,
    use_case_id: str,
    model: dict[str, Any],
    required_capabilities: Iterable[str],
    messages: list[dict[str, str]],
    generation_settings: dict[str, Any] | None,
    temperature: float,
    max_tokens: int | None,
    timeout_seconds: int,
    response_format: bool,
    stream: bool,
    output_object: OutputObjectDefinition | None = None,
    token_callback: Callable[[str], None] | None = None,
) -> ModelResponse:

    generation = _merge_generation_settings(
        generation_settings,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    binding = create_pydantic_model_binding(
        model,
        generation_settings=generation,
        timeout_seconds=timeout_seconds,
        required_capabilities=required_capabilities,
    )
    native_tools, model_settings = _web_search_parameters(
        binding,
        binding.model_settings,
    )
    binding = PydanticModelBinding(
        model=binding.model,
        model_settings=model_settings,
        model_id=binding.model_id,
        model_name=binding.model_name,
        provider_id=binding.provider_id,
        provider_family=binding.provider_family,
        api_style=binding.api_style,
        model_config=binding.model_config,
        required_capabilities=binding.required_capabilities,
    )
    parameters = ModelRequestParameters(
        native_tools=native_tools,
        output_mode="prompted" if response_format else "text",
        output_object=output_object,
    )

    def emit_text_delta(text: str) -> None:
        if token_callback:
            token_callback(text)

    try:
        response = _request(
            app_dir=app_dir,
            use_case_id=use_case_id,
            binding=binding,
            messages=_messages(messages),
            parameters=parameters,
            stream=stream,
            emit_text_delta=emit_text_delta,
            emit_reasoning_delta=None,
        )
    except Exception as exc:
        mapped = map_pydantic_model_error(
            exc,
            model_id=binding.model_id,
            model_name=binding.model_name,
            api_style=binding.api_style,
            base_url=ai_model_config.model_base_url(binding.model_config),
        )
        if mapped is exc:
            raise
        raise mapped from None
    return response


def chat_json(
    *,
    app_dir: Path | str,
    use_case_id: str,
    model: dict[str, Any],
    required_capabilities: Iterable[str],
    messages: list[dict[str, str]],
    generation_settings: dict[str, Any] | None,
    temperature: float,
    max_tokens: int | None,
    timeout_seconds: int,
    response_format: bool,
    stream: bool,
    token_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """通过 Pydantic Direct Model 执行普通文本/JSON/流式请求。"""

    response = _chat_response(
        app_dir=app_dir,
        use_case_id=use_case_id,
        model=model,
        required_capabilities=required_capabilities,
        messages=messages,
        generation_settings=generation_settings,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        response_format=response_format,
        stream=stream,
        token_callback=token_callback,
    )
    return parse_json_text(response.text or "")


def chat_structured(
    *,
    app_dir: Path | str,
    use_case_id: str,
    model: dict[str, Any],
    required_capabilities: Iterable[str],
    messages: list[dict[str, str]],
    generation_settings: dict[str, Any] | None,
    temperature: float,
    max_tokens: int | None,
    timeout_seconds: int,
    stream: bool,
    output_type: type[OutputT],
    token_callback: Callable[[str], None] | None = None,
) -> OutputT:
    """使用 Pydantic Direct Model 的 prompted output 并校验类型化结果。"""

    adapter = output_adapter(output_type)
    schema = object_json_schema(adapter)
    response = _chat_response(
        app_dir=app_dir,
        use_case_id=use_case_id,
        model=model,
        required_capabilities=required_capabilities,
        messages=messages,
        generation_settings=generation_settings,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        response_format=True,
        stream=stream,
        output_object=OutputObjectDefinition(
            json_schema=schema,
            name=getattr(output_type, "__name__", None),
            description=str(getattr(output_type, "__doc__", "") or "").strip()
            or None,
        ),
        token_callback=token_callback,
    )
    parsed = parse_json_text(response.text or "")
    return validate_structured_output(adapter, parsed)


def _image_settings(binding: PydanticModelBinding, count: int) -> ModelSettings:
    settings = dict(binding.model_settings)
    extra_body = (
        dict(settings.get("extra_body"))
        if isinstance(settings.get("extra_body"), dict)
        else {}
    )
    extra_body["n"] = max(1, int(count))
    settings["extra_body"] = extra_body
    return ModelSettings(**settings)


def _image_results(
    response: ModelResponse,
    *,
    provider_name: str,
    mode: str,
    source_id: str = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for content in response.files:
        if not isinstance(content, BinaryContent) or not content.is_image:
            continue
        suffix = {
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }.get(content.media_type, ".png")
        results.append(
            {
                "provider": provider_name,
                "mode": mode,
                "source_id": source_id,
                "suffix": suffix,
                "b64_json": base64.b64encode(content.data).decode("ascii"),
            }
        )
    for part in response.parts:
        if not isinstance(part, TextPart):
            continue
        details = part.provider_details or {}
        url = str(details.get("image_url") or "").strip()
        if url:
            results.append(
                {
                    "provider": provider_name,
                    "mode": mode,
                    "source_id": source_id,
                    "suffix": ".png",
                    "url": url,
                }
            )
    return results


def _image_request(
    *,
    app_dir: Path | str,
    use_case_id: str,
    binding: PydanticModelBinding,
    prompt: str,
    action: str,
    result_mode: str,
    source: BinaryContent | None,
    source_id: str,
    size: str,
    quality: str,
    count: int,
) -> list[dict[str, Any]]:
    user_content: list[str | BinaryContent] = [prompt]
    if source is not None:
        user_content.append(source)
    parameters = ModelRequestParameters(
        native_tools=[
            ImageGenerationTool(
                action=action,
                model=binding.model_name,
                size=size,  # type: ignore[arg-type]
                quality=quality,  # type: ignore[arg-type]
            )
        ],
        allow_text_output=False,
        allow_image_output=True,
    )
    request_binding = PydanticModelBinding(
        model=binding.model,
        model_settings=_image_settings(binding, count),
        model_id=binding.model_id,
        model_name=binding.model_name,
        provider_id=binding.provider_id,
        provider_family=binding.provider_family,
        api_style=binding.api_style,
        model_config=binding.model_config,
        required_capabilities=binding.required_capabilities,
    )
    response = _request(
        app_dir=app_dir,
        use_case_id=use_case_id,
        binding=request_binding,
        messages=[ModelRequest(parts=[UserPromptPart(user_content)])],
        parameters=parameters,
        stream=False,
        emit_text_delta=None,
        emit_reasoning_delta=None,
    )
    provider_name = str(
        binding.model_config.get("provider")
        or binding.model_config.get("name")
        or binding.provider_id
    )
    return _image_results(
        response,
        provider_name=provider_name,
        mode=result_mode or action,
        source_id=source_id,
    )


def generate_images(
    *,
    app_dir: Path | str,
    use_case_id: str,
    model: dict[str, Any],
    required_capabilities: Iterable[str],
    prompt: str,
    mode: str = "generate",
    size: str,
    quality: str,
    count: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    binding = create_pydantic_model_binding(
        model,
        timeout_seconds=timeout_seconds,
        required_capabilities=required_capabilities,
    )
    try:
        return _image_request(
            app_dir=app_dir,
            use_case_id=use_case_id,
            binding=binding,
            prompt=prompt,
            action="generate",
            result_mode=mode,
            source=None,
            source_id="",
            size=size,
            quality=quality,
            count=count,
        )
    except Exception as exc:
        mapped = map_pydantic_model_error(
            exc,
            model_id=binding.model_id,
            model_name=binding.model_name,
            api_style=binding.api_style,
            base_url=ai_model_config.model_base_url(binding.model_config),
        )
        if mapped is exc:
            raise
        raise mapped from None


def edit_images(
    *,
    app_dir: Path | str,
    use_case_id: str,
    model: dict[str, Any],
    required_capabilities: Iterable[str],
    prompt: str,
    images: list[dict[str, Any]],
    mode: str = "edit",
    size: str,
    quality: str,
    count: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    binding = create_pydantic_model_binding(
        model,
        timeout_seconds=timeout_seconds,
        required_capabilities=required_capabilities,
    )
    results: list[dict[str, Any]] = []
    last_error: Exception | None = None
    found_source = False
    for item in images:
        source_path = None
        for key in ("path", "local_path"):
            candidate = image_service.resolve_local_path(
                str(item.get(key) or ""),
                app_dir,
            )
            if candidate and candidate.exists() and candidate.is_file():
                source_path = candidate
                break
        if source_path is None:
            continue
        found_source = True
        source_id = str(item.get("id") or "").strip()
        media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(source_path.suffix.lower(), "image/png")
        try:
            results.extend(
                _image_request(
                    app_dir=app_dir,
                    use_case_id=use_case_id,
                    binding=binding,
                    prompt=prompt,
                    action="edit",
                    result_mode=mode,
                    source=BinaryContent(
                        data=source_path.read_bytes(),
                        media_type=media_type,
                    ),
                    source_id=source_id,
                    size=size,
                    quality=quality,
                    count=count,
                )
            )
        except Exception as exc:
            last_error = exc
    if results:
        return results
    if last_error is not None:
        mapped = map_pydantic_model_error(
            last_error,
            model_id=binding.model_id,
            model_name=binding.model_name,
            api_style=binding.api_style,
            base_url=ai_model_config.model_base_url(binding.model_config),
        )
        if mapped is last_error:
            raise last_error
        raise mapped from None
    if not found_source:
        raise ValueError("图片编辑没有可读取的本地源图片。")
    raise RuntimeError("图片模型没有返回可用图片。")


def request_for_probe(
    *,
    app_dir: Path | str,
    binding: PydanticModelBinding,
    messages: Sequence[dict[str, str] | ModelMessage],
    response_format: bool = False,
    native_tools: list[Any] | None = None,
    function_tools: list[ToolDefinition] | None = None,
    allow_text_output: bool = True,
) -> ModelResponse:
    """模型配置页能力探测复用的 Pydantic Direct 请求。"""

    parameters = ModelRequestParameters(
        native_tools=list(native_tools or []),
        function_tools=list(function_tools or []),
        output_mode="prompted" if response_format else "text",
        allow_text_output=allow_text_output,
    )
    try:
        response = _request(
            app_dir=app_dir,
            use_case_id="config.ai_model_probe",
            binding=binding,
            messages=_probe_model_messages(messages),
            parameters=parameters,
            stream=False,
            emit_text_delta=None,
            emit_reasoning_delta=None,
        )
    except Exception as exc:
        mapped = map_pydantic_model_error(
            exc,
            model_id=binding.model_id,
            model_name=binding.model_name,
            api_style=binding.api_style,
            base_url=ai_model_config.model_base_url(binding.model_config),
        )
        if mapped is exc:
            raise
        raise mapped from None
    return response


def request_json_for_probe(
    *,
    app_dir: Path | str,
    binding: PydanticModelBinding,
    messages: Sequence[dict[str, str] | ModelMessage],
    web_search: bool = False,
) -> tuple[dict[str, Any], str]:
    """使用既有 probe binding 验证 JSON，并可复用正式联网参数生成逻辑。"""

    native_tools: list[WebSearchTool] = []
    request_mode = ""
    request_binding = binding
    if web_search:
        native_tools, model_settings = _web_search_parameters(
            binding,
            binding.model_settings,
        )
        request_mode = web_search_request_mode(binding)
        request_binding = PydanticModelBinding(
            model=binding.model,
            model_settings=model_settings,
            model_id=binding.model_id,
            model_name=binding.model_name,
            provider_id=binding.provider_id,
            provider_family=binding.provider_family,
            api_style=binding.api_style,
            model_config=binding.model_config,
            required_capabilities=binding.required_capabilities,
        )
    response = request_for_probe(
        app_dir=app_dir,
        binding=request_binding,
        messages=messages,
        response_format=True,
        native_tools=native_tools,
    )
    return parse_json_text(response.text or ""), request_mode


def generate_images_for_probe(
    *,
    app_dir: Path | str,
    binding: PydanticModelBinding,
    prompt: str,
) -> list[dict[str, Any]]:
    return _image_request(
        app_dir=app_dir,
        use_case_id="config.ai_model_probe.image_generate",
        binding=binding,
        prompt=prompt,
        action="generate",
        result_mode="generate",
        source=None,
        source_id="",
        size="1024x1024",
        quality="medium",
        count=1,
    )


def edit_image_for_probe(
    *,
    app_dir: Path | str,
    binding: PydanticModelBinding,
    prompt: str,
    source: bytes,
) -> list[dict[str, Any]]:
    return _image_request(
        app_dir=app_dir,
        use_case_id="config.ai_model_probe.image_edit",
        binding=binding,
        prompt=prompt,
        action="edit",
        result_mode="edit",
        source=BinaryContent(data=source, media_type="image/png"),
        source_id="probe",
        size="1024x1024",
        quality="medium",
        count=1,
    )


__all__ = [
    "PYDANTIC_DIRECT_PROVIDER_ID",
    "chat_json",
    "chat_structured",
    "edit_images",
    "edit_image_for_probe",
    "generate_images",
    "generate_images_for_probe",
    "request_json_for_probe",
    "request_for_probe",
    "web_search_request_mode",
]
