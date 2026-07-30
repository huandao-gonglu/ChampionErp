"""AI Provider 注册表、业务客户端与兼容公开门面。

HTTP、CLI、浏览器和图片协议实现在聚焦模块中；这里仅负责 Provider 选择、
一次业务用例的上下文收敛，以及稳定公开函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from erp_web.context import get_context

from . import ai_model_config, ai_work_service, config_service
from .ai_gateway_browser_provider import BrowserAiProvider, probe_browser_model_capabilities
from .ai_gateway_cli_provider import CodexCliProvider, probe_cli_model_capabilities
from .ai_gateway_http_providers import (
    AIHTTPError,
    OpenAICompatibleProvider,
    OpenAIResponsesProvider,
    _model_api_style,
    _resolved_use_case_request,
    _validate_capability_profiles,
    list_remote_models,
    probe_model_capabilities,
    resolve_model_for_use_case,
)
from .ai_gateway_parsing import parse_json_text
from .ai_gateway_provider_types import AiChatRequest
from .ai_image_provider import OpenAIImageProvider
from .ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    CAPABILITY_IMAGE_EDIT,
    CAPABILITY_IMAGE_GENERATE,
    AiChatProvider,
    AiImageProvider,
    AiImageRequest,
    AiProvider,
)

AI_PROVIDER_REGISTRY: tuple[AiProvider, ...] = (
    CodexCliProvider(),
    BrowserAiProvider(),
    OpenAIResponsesProvider(),
    OpenAICompatibleProvider(),
    OpenAIImageProvider(),
)

def _provider_for_model(
    model: dict[str, Any],
    capability: str = CAPABILITY_CHAT_JSON,
) -> AiProvider:
    for provider in AI_PROVIDER_REGISTRY:
        if provider.supports(model, capability):
            return provider
    if (
        capability == CAPABILITY_CHAT_JSON
        and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_CLI
    ):
        raise RuntimeError(f"CLI 工具 {ai_model_config.model_cli_tool(model)} 已预留，但当前版本只支持 Codex CLI。")
    raise RuntimeError(
        "不支持的 AI Provider 配置："
        f"capability={capability} "
        f"connection_type={ai_model_config.model_connection_type(model)} "
        f"api_style={_model_api_style(model)}"
    )

@dataclass(frozen=True)
class AiProviderClient:
    """一次业务用例解析后的 Provider 客户端。

    业务入口只在构造时传递 app/config/use-case 参数；后续的 Provider 选择、
    能力配方校验、超时和对话日志都从该对象读取，避免三条调用链重复隧穿。
    """

    app_dir: Path | str
    use_case_id: str
    model: dict[str, Any]
    required_capabilities: tuple[str, ...]
    timeout_seconds: int

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
        model, required_capabilities, effective_timeout = _resolved_use_case_request(
            app_dir,
            app_config,
            use_case_id,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            default_timeout_seconds=default_timeout_seconds,
        )
        _validate_capability_profiles(model, required_capabilities)
        return cls(
            app_dir=app_dir,
            use_case_id=use_case_id,
            model=model,
            required_capabilities=required_capabilities,
            timeout_seconds=effective_timeout,
        )

    def provider_for(self, capability: str) -> AiProvider:
        return _provider_for_model(self.model, capability)

    def start_conversation(
        self,
        capability: str,
        provider: AiProvider,
        input_payload: dict[str, Any],
        *,
        stream: bool = False,
    ) -> ai_work_service.AiWorkConversation:
        return get_context().ai_journal.start_conversation(
            use_case_id=self.use_case_id,
            capability=capability,
            provider_id=provider.provider_id,
            model=self.model,
            stream=stream,
            required_capabilities=self.required_capabilities,
            timeout_seconds=self.timeout_seconds,
            input_payload=input_payload,
        )

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
    provider = client.provider_for(CAPABILITY_CHAT_JSON)
    if not isinstance(provider, AiChatProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现文本对话能力。")
    conversation = client.start_conversation(
        CAPABILITY_CHAT_JSON,
        provider,
        {"messages": messages},
        stream=effective_stream,
    )
    try:
        parsed = provider.chat_json(
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
                conversation=conversation,
            )
        )
        conversation.emit_custom("business.result", {"parsed": parsed})
        conversation.finish({"parsed": parsed})
        return parsed
    except Exception as exc:
        conversation.fail(exc)
        raise

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
    client = AiProviderClient.for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=180,
    )
    provider = client.provider_for(CAPABILITY_IMAGE_GENERATE)
    if not isinstance(provider, AiImageProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现图片生成能力。")
    conversation = client.start_conversation(
        CAPABILITY_IMAGE_GENERATE,
        provider,
        {"prompt": prompt, "images": images or []},
    )
    try:
        results = provider.generate_images(
            AiImageRequest(
                app_dir=client.app_dir,
                model=client.model,
                prompt=prompt,
                images=images or [],
                mode=mode,
                timeout_seconds=client.timeout_seconds,
                size=size,
                quality=quality,
                count=count,
                conversation=conversation,
            )
        )
        summary = {"generated_count": len(results)}
        conversation.emit_custom("business.result", summary)
        conversation.finish(summary)
        return results
    except Exception as exc:
        conversation.fail(exc)
        raise

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
    client = AiProviderClient.for_use_case(
        app_dir,
        app_config,
        use_case_id,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=180,
    )
    provider = client.provider_for(CAPABILITY_IMAGE_EDIT)
    if not isinstance(provider, AiImageProvider):
        raise RuntimeError(f"Provider {provider.provider_id} 未实现图片编辑能力。")
    conversation = client.start_conversation(
        CAPABILITY_IMAGE_EDIT,
        provider,
        {"prompt": prompt, "images": images},
    )
    try:
        results = provider.edit_images(
            AiImageRequest(
                app_dir=client.app_dir,
                model=client.model,
                prompt=prompt,
                images=images,
                mode=mode,
                timeout_seconds=client.timeout_seconds,
                size=size,
                quality=quality,
                count=count,
                conversation=conversation,
            )
        )
        summary = {"generated_count": len(results)}
        conversation.emit_custom("business.result", summary)
        conversation.finish(summary)
        return results
    except Exception as exc:
        conversation.fail(exc)
        raise

def test_ai_model(app_dir: Path | str, model: dict[str, Any]) -> dict[str, Any]:
    config_service.load_env(app_dir)
    raw_model = model if isinstance(model, dict) else {}
    normalized = ai_model_config.normalize_ai_model(raw_model)
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
    "AiImageProvider",
    "AiImageRequest",
    "AiProvider",
    "BrowserAiProvider",
    "CodexCliProvider",
    "OpenAICompatibleProvider",
    "OpenAIImageProvider",
    "OpenAIResponsesProvider",
    "chat_json",
    "edit_images",
    "generate_images",
    "list_remote_models",
    "parse_json_text",
    "probe_browser_model_capabilities",
    "probe_cli_model_capabilities",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
    "test_ai_model",
]
