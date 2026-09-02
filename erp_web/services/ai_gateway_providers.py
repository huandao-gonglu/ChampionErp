"""AI 网关编排：API 统一走 Pydantic，CLI/浏览器保留独立连接。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import (
    ai_direct_request_service,
    ai_model_config,
    config_service,
)
from .ai_gateway_browser_provider import (
    BrowserAiProvider,
    probe_browser_model_capabilities,
)
from .ai_gateway_cli_provider import CodexCliProvider, probe_cli_model_capabilities
from .ai_gateway_provider_types import AiChatRequest
from .ai_model_discovery import list_remote_models
from .ai_model_errors import AIHTTPError
from .ai_model_probe_service import probe_model_capabilities, test_api_model
from .ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    CAPABILITY_IMAGE_EDIT,
    CAPABILITY_IMAGE_GENERATE,
    AiChatProvider,
    AiProvider,
)
from .ai_structured_output import (
    object_json_schema,
    output_adapter,
    prompted_schema_instruction,
    validate_structured_output,
)


# 注册表只保存真正独立的非 API 连接。API 不再通过协议 Provider 注册表分流。
AI_PROVIDER_REGISTRY: tuple[AiProvider, ...] = (
    CodexCliProvider(),
    BrowserAiProvider(),
)
OutputT = TypeVar("OutputT")


def _provider_for_model(
    model: dict[str, Any],
    capability: str = CAPABILITY_CHAT_JSON,
) -> AiProvider:
    if ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API:
        raise RuntimeError("API 模型必须通过 Pydantic Direct Model 执行。")
    for provider in AI_PROVIDER_REGISTRY:
        if provider.supports(model, capability):
            return provider
    if (
        capability == CAPABILITY_CHAT_JSON
        and ai_model_config.model_connection_type(model)
        == ai_model_config.CONNECTION_TYPE_CLI
    ):
        raise RuntimeError(
            f"CLI 工具 {ai_model_config.model_cli_tool(model)} 已预留，"
            "但当前版本只支持 Codex CLI。"
        )
    raise RuntimeError(
        "不支持的非 API AI Provider 配置："
        f"capability={capability} "
        f"connection_type={ai_model_config.model_connection_type(model)}"
    )


def resolve_model_for_use_case(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    model_id: str = "",
) -> dict[str, Any]:
    config_service.load_env(app_dir)
    return ai_model_config.resolve_ai_model(
        app_config,
        use_case_id,
        model_id=model_id,
    )


def _resolved_use_case_request(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    model_id: str = "",
    timeout_seconds: int | None = None,
    default_timeout_seconds: int,
) -> tuple[dict[str, Any], tuple[str, ...], int]:
    model = resolve_model_for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id,
    )
    binding = ai_model_config.ai_use_case_binding(app_config, use_case_id)
    required = tuple(
        ai_model_config.ai_use_case_required_capabilities(use_case_id)
    )
    provider_default_timeout = (
        default_timeout_seconds
        if ai_model_config.model_connection_type(model)
        == ai_model_config.CONNECTION_TYPE_API
        else 180
    )
    effective_timeout = int(
        binding.get("timeout_override_seconds")
        or timeout_seconds
        or model.get("timeout_seconds")
        or provider_default_timeout
    )
    return model, required, effective_timeout


@dataclass(frozen=True)
class AiProviderClient:
    """一次业务用例解析后的模型调用上下文。"""

    app_dir: Path | str
    use_case_id: str
    model: dict[str, Any]
    required_capabilities: tuple[str, ...]
    timeout_seconds: int
    generation_settings: dict[str, Any] | None = None

    @classmethod
    def for_use_case(
        cls,
        app_dir: Path | str,
        app_config: dict[str, Any] | None,
        use_case_id: str,
        *,
        model_id: str = "",
        timeout_seconds: int | None = None,
        default_timeout_seconds: int,
    ) -> "AiProviderClient":
        model, required, effective_timeout = _resolved_use_case_request(
            app_dir,
            app_config,
            use_case_id,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            default_timeout_seconds=default_timeout_seconds,
        )
        binding = ai_model_config.ai_use_case_binding(app_config, use_case_id)
        return cls(
            app_dir=app_dir,
            use_case_id=use_case_id,
            model=model,
            required_capabilities=required,
            timeout_seconds=effective_timeout,
            generation_settings=dict(binding.get("generation") or {}),
        )

    @property
    def connection_type(self) -> str:
        return ai_model_config.model_connection_type(self.model)

    def provider_for(self, capability: str) -> AiProvider:
        return _provider_for_model(self.model, capability)

def chat_json(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    messages: list[dict[str, str]],
    *,
    model_id: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    response_format: bool = True,
    extra_body: dict[str, Any] | None = None,
    stream: bool | None = None,
    token_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    client = AiProviderClient.for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=60,
    )
    effective_stream = True if stream is None else bool(stream)
    if client.connection_type == ai_model_config.CONNECTION_TYPE_API:
        if extra_body:
            raise ValueError(
                "API 请求不允许业务层传入 extra_body；请使用模型配置的受控字段。"
            )
        return ai_direct_request_service.chat_json(
            app_dir=client.app_dir,
            use_case_id=client.use_case_id,
            model=client.model,
            required_capabilities=client.required_capabilities,
            messages=messages,
            generation_settings=client.generation_settings,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=client.timeout_seconds,
            response_format=response_format,
            stream=effective_stream,
            token_callback=token_callback,
        )

    provider = client.provider_for(CAPABILITY_CHAT_JSON)
    if not isinstance(provider, AiChatProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现文本对话能力。")
    return provider.chat_json(
        AiChatRequest(
            app_dir=client.app_dir,
            model=client.model,
            messages=messages,
            required_capabilities=client.required_capabilities,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=client.timeout_seconds,
            response_format=response_format,
            extra_body=extra_body,
            stream=effective_stream,
            token_callback=token_callback,
            generation_settings=client.generation_settings,
        )
    )


def chat_structured(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    messages: list[dict[str, str]],
    *,
    output_type: type[OutputT],
    model_id: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    extra_body: dict[str, Any] | None = None,
    stream: bool | None = None,
    token_callback: Callable[[str], None] | None = None,
) -> OutputT:
    """按 Pydantic 类型生成 Schema，并返回通过同一类型校验的结果。"""

    client = AiProviderClient.for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=60,
    )
    effective_stream = True if stream is None else bool(stream)
    if client.connection_type == ai_model_config.CONNECTION_TYPE_API:
        if extra_body:
            raise ValueError(
                "API 请求不允许业务层传入 extra_body；请使用模型配置的受控字段。"
            )
        return ai_direct_request_service.chat_structured(
            app_dir=client.app_dir,
            use_case_id=client.use_case_id,
            model=client.model,
            required_capabilities=client.required_capabilities,
            messages=messages,
            generation_settings=client.generation_settings,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=client.timeout_seconds,
            stream=effective_stream,
            output_type=output_type,
            token_callback=token_callback,
        )

    adapter = output_adapter(output_type)
    schema = object_json_schema(adapter)
    schema_messages = [
        *messages,
        {
            "role": "system",
            "content": prompted_schema_instruction(schema),
        },
    ]
    provider = client.provider_for(CAPABILITY_CHAT_JSON)
    if not isinstance(provider, AiChatProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现文本对话能力。")
    parsed = provider.chat_json(
        AiChatRequest(
            app_dir=client.app_dir,
            model=client.model,
            messages=schema_messages,
            required_capabilities=client.required_capabilities,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=client.timeout_seconds,
            response_format=True,
            extra_body=extra_body,
            stream=effective_stream,
            token_callback=token_callback,
            generation_settings=client.generation_settings,
        )
    )
    return validate_structured_output(adapter, parsed)


def _image_client(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    model_id: str,
    timeout_seconds: int | None,
) -> AiProviderClient:
    client = AiProviderClient.for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=180,
    )
    if client.connection_type != ai_model_config.CONNECTION_TYPE_API:
        raise RuntimeError("图片 API 能力只允许通过 Pydantic Model 执行。")
    return client


def generate_images(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    prompt: str,
    images: list[dict[str, Any]] | None = None,
    mode: str = "generate",
    model_id: str = "",
    size: str = "1024x1024",
    quality: str = "medium",
    count: int = 1,
    timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    del images
    client = _image_client(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
    )
    required = tuple(
        dict.fromkeys(
            (*client.required_capabilities, ai_model_config.CAP_IMAGE_GENERATE)
        )
    )
    return ai_direct_request_service.generate_images(
        app_dir=client.app_dir,
        use_case_id=client.use_case_id,
        model=client.model,
        required_capabilities=required,
        prompt=prompt,
        mode=mode,
        size=size,
        quality=quality,
        count=count,
        timeout_seconds=client.timeout_seconds,
    )


def edit_images(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    prompt: str,
    images: list[dict[str, Any]],
    mode: str = "edit",
    model_id: str = "",
    size: str = "1024x1024",
    quality: str = "medium",
    count: int = 1,
    timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    client = _image_client(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
    )
    return ai_direct_request_service.edit_images(
        app_dir=client.app_dir,
        use_case_id=client.use_case_id,
        model=client.model,
        required_capabilities=client.required_capabilities,
        prompt=prompt,
        images=images,
        mode=mode,
        size=size,
        quality=quality,
        count=count,
        timeout_seconds=client.timeout_seconds,
    )


def test_ai_model(app_dir: Path | str, model: dict[str, Any]) -> dict[str, Any]:
    config_service.load_env(app_dir)
    raw_model = model if isinstance(model, dict) else {}
    normalized = ai_model_config.normalize_ai_model(raw_model)
    connection_type = ai_model_config.model_connection_type(normalized)
    if connection_type == ai_model_config.CONNECTION_TYPE_API:
        return test_api_model(app_dir, normalized, raw_model)
    provider = _provider_for_model(normalized, CAPABILITY_CHAT_JSON)
    if not isinstance(provider, AiChatProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现模型测试能力。")
    return provider.test_model(app_dir, normalized, raw_model)


__all__ = [
    "AI_PROVIDER_REGISTRY",
    "AIHTTPError",
    "AiChatRequest",
    "AiProviderClient",
    "AiChatProvider",
    "AiProvider",
    "BrowserAiProvider",
    "CodexCliProvider",
    "chat_json",
    "chat_structured",
    "edit_images",
    "generate_images",
    "list_remote_models",
    "probe_browser_model_capabilities",
    "probe_cli_model_capabilities",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
    "test_ai_model",
]
