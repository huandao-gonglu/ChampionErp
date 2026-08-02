"""Pydantic AI Model、Provider 与 ModelSettings 的集中创建边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.settings import ModelSettings

from . import (
    ai_generation_settings,
    ai_model_config,
    ai_provider_catalog,
    config_service,
)
from .ai_pydantic_image_model import OpenAIImagesModel


class AiModelFactoryError(RuntimeError):
    """无法从产品配置安全创建 Pydantic AI Model。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AI_MODEL_CONFIGURATION_INVALID",
    ) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class PydanticModelBinding:
    """一次 Agent 装配可消费的 Model 与请求设置，不公开认证信息。"""

    model: Model = field(repr=False)
    model_settings: ModelSettings = field(repr=False)
    model_id: str
    model_name: str
    provider_id: str
    provider_family: str
    api_style: str
    model_config: dict[str, Any] = field(default_factory=dict, repr=False)
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_settings", dict(self.model_settings))
        object.__setattr__(self, "model_config", dict(self.model_config))
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(self.required_capabilities),
        )

    def __repr__(self) -> str:
        return (
            "PydanticModelBinding("
            f"model_id={self.model_id!r}, "
            f"model_name={self.model_name!r}, "
            f"provider_id={self.provider_id!r}, "
            f"provider_family={self.provider_family!r}, "
            f"api_style={self.api_style!r})"
        )


def _positive_timeout(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise AiModelFactoryError("AI 模型 timeout_seconds 必须是正数。")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise AiModelFactoryError("AI 模型 timeout_seconds 必须是正数。") from exc
    if timeout <= 0:
        raise AiModelFactoryError("AI 模型 timeout_seconds 必须大于 0。")
    return timeout


def _validate_required_capabilities(
    model: dict[str, Any],
    required_capabilities: Iterable[str],
) -> None:
    required = tuple(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in required_capabilities
            if str(item or "").strip()
        )
    )
    available = set(ai_model_config.normalize_capabilities(model.get("capabilities")))
    missing = [item for item in required if item not in available]
    if not missing:
        return
    code = (
        "AI_MODEL_TOOL_CALLING_UNSUPPORTED"
        if ai_model_config.CAP_TOOL_CALLING in missing
        else "AI_MODEL_CAPABILITY_UNSUPPORTED"
    )
    raise AiModelFactoryError(
        "当前 AI 模型不满足能力要求：" + ", ".join(missing),
        code=code,
    )


def _normalize_operation_capabilities(
    capabilities: Iterable[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in capabilities
            if str(item or "").strip()
        )
    )


def _build_pydantic_model_binding(
    model_config: dict[str, Any],
    *,
    generation_settings: dict[str, Any] | None = None,
    timeout_seconds: int | float | None = None,
    operation_capabilities: Iterable[str] = (),
) -> PydanticModelBinding:
    """只根据连接配置与本次操作构造 Model，不执行能力声明校验。"""

    model = ai_model_config.normalize_ai_model(model_config)
    model_id = str(model.get("id") or "").strip()
    if ai_model_config.model_connection_type(model) != ai_model_config.CONNECTION_TYPE_API:
        raise AiModelFactoryError("Pydantic Agent 当前只支持 API 模型连接。")
    normalized_operation_capabilities = _normalize_operation_capabilities(
        operation_capabilities
    )

    model_name = ai_model_config.model_name(model)
    if not model_name:
        raise AiModelFactoryError(f"AI 模型 {model_id or 'unknown'} 未配置模型名。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise AiModelFactoryError(f"AI 模型 {model_id or 'unknown'} 未配置 Base URL。")
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise AiModelFactoryError(f"AI 模型 {model_id or 'unknown'} 未配置 API Key。")

    api_style = ai_model_config.normalize_api_style(model.get("api_style"))
    try:
        provider_spec = ai_provider_catalog.provider_spec_for_model(model)
    except ValueError as exc:
        raise AiModelFactoryError(str(exc)) from None
    provider_id = provider_spec.provider_id
    provider_family = provider_spec.provider_family
    if api_style not in provider_spec.supported_api_styles:
        raise AiModelFactoryError(
            f"AI Provider {provider_spec.label} 不支持 API 协议 {api_style}。"
        )
    try:
        settings_payload = ai_generation_settings.pydantic_model_settings_payload(
            model,
            generation_settings,
        )
    except ValueError as exc:
        raise AiModelFactoryError(
            f"AI 模型 {model_id or 'unknown'} 的生成配置无效：{exc}"
        ) from exc
    effective_timeout = _positive_timeout(
        timeout_seconds
        if timeout_seconds is not None
        else model.get("timeout_seconds")
    )
    if effective_timeout is not None:
        settings_payload["timeout"] = effective_timeout
    settings_payload["parallel_tool_calls"] = False
    model_settings = ModelSettings(**settings_payload)

    try:
        provider = ai_provider_catalog.create_pydantic_provider(
            provider_id,
            base_url=base_url,
            api_key=api_key,
        )
        image_capabilities = {
            ai_model_config.CAP_IMAGE_GENERATE,
            ai_model_config.CAP_IMAGE_EDIT,
        }
        if image_capabilities & set(normalized_operation_capabilities):
            if "images" not in provider_spec.supported_model_kinds:
                raise AiModelFactoryError(
                    f"AI Provider {provider_spec.label} 未接入 Images Model。"
                )
            unsupported_mix = set(normalized_operation_capabilities) - image_capabilities
            if unsupported_mix:
                raise AiModelFactoryError(
                    "专用图片模型不能同时承担文本或工具能力："
                    + ", ".join(sorted(unsupported_mix))
                )
            pydantic_model = OpenAIImagesModel(
                model_name,
                provider=provider,
            )
        elif api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
            if "responses" not in provider_spec.supported_model_kinds:
                raise AiModelFactoryError(
                    f"AI Provider {provider_spec.label} 未接入 Responses Model。"
                )
            profile_payload = (
                ai_generation_settings.pydantic_openai_responses_profile_payload(
                    provider_family,
                    model_name,
                )
            )
            profile = (
                OpenAIModelProfile(**profile_payload)
                if profile_payload
                else None
            )
            pydantic_model: Model = OpenAIResponsesModel(
                model_name,
                provider=provider,
                profile=profile,
            )
        else:
            profile_payload = (
                ai_generation_settings.pydantic_openai_chat_profile_payload(
                    provider_family
                )
            )
            profile = (
                OpenAIModelProfile(**profile_payload)
                if profile_payload
                else None
            )
            pydantic_model = OpenAIChatModel(
                model_name,
                provider=provider,
                profile=profile,
            )
    except AiModelFactoryError:
        raise
    except ValueError as exc:
        raise AiModelFactoryError(str(exc)) from None
    except Exception:
        raise AiModelFactoryError(
            f"AI 模型 {model_id or 'unknown'} 无法创建 Pydantic Model。"
        ) from None

    return PydanticModelBinding(
        model=pydantic_model,
        model_settings=model_settings,
        model_id=model_id,
        model_name=model_name,
        provider_id=provider_id,
        provider_family=provider_family,
        api_style=api_style,
        model_config=model,
        required_capabilities=normalized_operation_capabilities,
    )


def create_pydantic_model_binding(
    model_config: dict[str, Any],
    *,
    generation_settings: dict[str, Any] | None = None,
    timeout_seconds: int | float | None = None,
    required_capabilities: Iterable[str] = (),
) -> PydanticModelBinding:
    """正式业务入口：校验已启用能力后创建 Pydantic Model binding。"""

    model = ai_model_config.normalize_ai_model(model_config)
    normalized_required_capabilities = _normalize_operation_capabilities(
        required_capabilities
    )
    _validate_required_capabilities(model, normalized_required_capabilities)
    return _build_pydantic_model_binding(
        model,
        generation_settings=generation_settings,
        timeout_seconds=timeout_seconds,
        operation_capabilities=normalized_required_capabilities,
    )


def create_pydantic_probe_binding(
    model_config: dict[str, Any],
    *,
    probe_capability: str,
    generation_settings: dict[str, Any] | None = None,
    timeout_seconds: int | float | None = None,
) -> PydanticModelBinding:
    """能力探测入口：不依赖尚未产生的 capability 声明。"""

    normalized = ai_model_config.normalize_capabilities([probe_capability])
    if len(normalized) != 1:
        raise AiModelFactoryError(
            f"未知 AI 能力：{str(probe_capability or '').strip() or 'empty'}。"
        )
    return _build_pydantic_model_binding(
        ai_model_config.normalize_ai_model(model_config),
        generation_settings=generation_settings,
        timeout_seconds=timeout_seconds,
        operation_capabilities=normalized,
    )


def create_pydantic_model_binding_for_use_case(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    model_id: str = "",
    timeout_seconds: int | float | None = None,
    default_timeout_seconds: int | float = 60,
) -> PydanticModelBinding:
    """从前端稳定 use-case 配置解析一次 Agent 可用的 Model binding。"""

    normalized_use_case_id = str(use_case_id or "").strip()
    if normalized_use_case_id not in ai_model_config.AI_USE_CASES:
        raise AiModelFactoryError(
            f"未知 AI 功能：{normalized_use_case_id or 'empty'}。"
        )
    try:
        config_service.load_env(app_dir)
        required_capabilities = tuple(
            ai_model_config.ai_use_case_required_capabilities(
                normalized_use_case_id
            )
        )
        model = ai_model_config.resolve_ai_model(
            app_config,
            normalized_use_case_id,
            required_capabilities=required_capabilities,
            model_id=model_id,
        )
        use_case_binding = ai_model_config.ai_use_case_binding(
            app_config,
            normalized_use_case_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise AiModelFactoryError(
            f"AI 功能 {normalized_use_case_id} 的模型配置无效：{exc}"
        ) from None

    effective_timeout = use_case_binding.get("timeout_override_seconds")
    if effective_timeout is None:
        effective_timeout = timeout_seconds
    if effective_timeout is None:
        effective_timeout = model.get("timeout_seconds")
    if effective_timeout is None:
        effective_timeout = default_timeout_seconds
    return create_pydantic_model_binding(
        model,
        generation_settings=use_case_binding.get("generation"),
        timeout_seconds=effective_timeout,
        required_capabilities=required_capabilities,
    )


__all__ = [
    "AiModelFactoryError",
    "PydanticModelBinding",
    "create_pydantic_model_binding",
    "create_pydantic_model_binding_for_use_case",
    "create_pydantic_probe_binding",
]
