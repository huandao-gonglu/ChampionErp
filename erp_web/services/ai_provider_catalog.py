"""ERP 已正式接入的 Pydantic AI Provider 目录与创建边界。

Pydantic AI 能按名称推断很多 Provider，但它没有面向产品配置的运行时目录。
本模块只公开当前依赖、配置契约和测试真正支持的 Provider；前端不直接枚举
Pydantic AI 包内模块，也不根据 Base URL 或模型名猜测厂商。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.profiles.google import google_model_profile
from pydantic_ai.profiles.grok import grok_model_profile
from pydantic_ai.profiles.moonshotai import moonshotai_model_profile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.providers import Provider
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider


PROVIDER_ID_OPENAI = "openai"
PROVIDER_ID_ANTHROPIC = "anthropic"
PROVIDER_ID_GOOGLE = "google"
PROVIDER_ID_DEEPSEEK = "deepseek"
PROVIDER_ID_ALIBABA = "alibaba"
PROVIDER_ID_OMLX = "omlx"

PROVIDER_FAMILY_GENERIC_OPENAI = "generic_openai"
PROVIDER_FAMILY_OPENAI = "openai"
PROVIDER_FAMILY_ANTHROPIC = "anthropic"
PROVIDER_FAMILY_GOOGLE = "google"
PROVIDER_FAMILY_DEEPSEEK = "deepseek"
PROVIDER_FAMILY_ALIBABA = "alibaba"
PROVIDER_FAMILY_OMLX = "omlx"

API_STYLE_OPENAI_COMPATIBLE = "openai_compatible"
API_STYLE_OPENAI_RESPONSES = "openai_responses"
API_STYLE_ANTHROPIC_MESSAGES = "anthropic_messages"
API_STYLE_GOOGLE_GEMINI = "google_gemini"

DiscoveryStrategy = Literal[
    "openai_models",
    "anthropic_models",
    "google_models",
    "manual",
]


@dataclass(frozen=True)
class AiProviderSpec:
    """一个可保存到产品配置中的稳定 Provider 定义。"""

    provider_id: str
    label: str
    description: str
    provider_family: str
    default_base_url: str
    default_api_style: str
    supported_api_styles: tuple[str, ...]
    supported_model_kinds: tuple[str, ...]
    base_url_editable: bool
    discovery_strategy: DiscoveryStrategy
    listed: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "label": self.label,
            "description": self.description,
            "provider_family": self.provider_family,
            "default_base_url": self.default_base_url,
            "default_api_style": self.default_api_style,
            "supported_api_styles": list(self.supported_api_styles),
            "supported_model_kinds": list(self.supported_model_kinds),
            "base_url_editable": self.base_url_editable,
            "model_discovery": self.discovery_strategy,
        }


_PROVIDER_SPECS = (
    AiProviderSpec(
        provider_id=PROVIDER_ID_OPENAI,
        label="OpenAI（官方 / 兼容协议）",
        description=(
            "连接 OpenAI 官方，或 DeepSeek、New API、OpenRouter、硅基流动、vLLM 等任意 "
            "OpenAI 兼容服务；API 协议可选 Chat Completions 或 Responses。"
        ),
        provider_family=PROVIDER_FAMILY_OPENAI,
        default_base_url="https://api.openai.com/v1",
        default_api_style=API_STYLE_OPENAI_COMPATIBLE,
        supported_api_styles=(
            API_STYLE_OPENAI_COMPATIBLE,
            API_STYLE_OPENAI_RESPONSES,
        ),
        supported_model_kinds=("chat", "responses", "images"),
        base_url_editable=True,
        discovery_strategy="openai_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_ANTHROPIC,
        label="Anthropic（Claude）",
        description="使用 Pydantic AI 原生 Anthropic 协议连接 Claude 官方或 Anthropic 兼容代理。",
        provider_family=PROVIDER_FAMILY_ANTHROPIC,
        default_base_url="https://api.anthropic.com",
        default_api_style=API_STYLE_ANTHROPIC_MESSAGES,
        supported_api_styles=(API_STYLE_ANTHROPIC_MESSAGES,),
        supported_model_kinds=("anthropic",),
        base_url_editable=True,
        discovery_strategy="anthropic_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_GOOGLE,
        label="Google Gemini",
        description="使用 Pydantic AI 原生 Google Gemini 协议连接 AI Studio 或自定义代理。",
        provider_family=PROVIDER_FAMILY_GOOGLE,
        default_base_url="https://generativelanguage.googleapis.com",
        default_api_style=API_STYLE_GOOGLE_GEMINI,
        supported_api_styles=(API_STYLE_GOOGLE_GEMINI,),
        supported_model_kinds=("google",),
        base_url_editable=True,
        discovery_strategy="google_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_ALIBABA,
        label="阿里云百炼 / Qwen",
        description="使用 Pydantic AI 的 AlibabaProvider，支持百炼原生 Thinking 与联网参数。",
        provider_family=PROVIDER_FAMILY_ALIBABA,
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_api_style=API_STYLE_OPENAI_COMPATIBLE,
        supported_api_styles=(
            API_STYLE_OPENAI_COMPATIBLE,
            API_STYLE_OPENAI_RESPONSES,
        ),
        supported_model_kinds=("chat", "responses"),
        base_url_editable=True,
        discovery_strategy="openai_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_OMLX,
        label="oMLX（本地模型）",
        description="通过 OpenAI 兼容接口连接 oMLX，默认使用 Chat Completions，支持模型 Thinking 开关。",
        provider_family=PROVIDER_FAMILY_OMLX,
        default_base_url="http://127.0.0.1:8000/v1",
        default_api_style=API_STYLE_OPENAI_COMPATIBLE,
        supported_api_styles=(
            API_STYLE_OPENAI_COMPATIBLE,
            API_STYLE_OPENAI_RESPONSES,
        ),
        supported_model_kinds=("chat", "responses"),
        base_url_editable=True,
        discovery_strategy="openai_models",
    ),
    # 已保存旧配置兼容项：不再展示在新建下拉目录中，但已有 provider_id="deepseek"
    # 配置可继续无损读取、保留能力指纹并经原生 DeepSeekProvider 运行。
    AiProviderSpec(
        provider_id=PROVIDER_ID_DEEPSEEK,
        label="DeepSeek",
        description="使用 Pydantic AI 的 DeepSeekProvider。",
        provider_family=PROVIDER_FAMILY_DEEPSEEK,
        default_base_url="https://api.deepseek.com",
        default_api_style=API_STYLE_OPENAI_COMPATIBLE,
        supported_api_styles=(API_STYLE_OPENAI_COMPATIBLE,),
        supported_model_kinds=("chat",),
        base_url_editable=False,
        discovery_strategy="openai_models",
        listed=False,
    ),
)

AI_PROVIDER_CATALOG = MappingProxyType(
    {spec.provider_id: spec for spec in _PROVIDER_SPECS}
)

def normalize_provider_id(value: Any) -> str:
    """规范化显式 Provider ID；不根据旧字段、URL 或模型名推断厂商。"""

    return str(value or "").strip().lower()


def provider_spec(provider_id: Any) -> AiProviderSpec:
    normalized = str(provider_id or "").strip().lower()
    spec = AI_PROVIDER_CATALOG.get(normalized)
    if spec is None:
        raise ValueError(f"未接入的 AI Provider：{normalized or 'empty'}。")
    return spec


def provider_spec_for_model(model: dict[str, Any]) -> AiProviderSpec:
    provider_id = normalize_provider_id(model.get("provider_id"))
    return provider_spec(provider_id)


def provider_family_for_model(model: dict[str, Any]) -> str:
    """返回内部参数映射族；它由 Provider Catalog 派生，不是用户配置。"""

    try:
        return provider_spec_for_model(model).provider_family
    except ValueError:
        return PROVIDER_FAMILY_GENERIC_OPENAI


def public_provider_catalog() -> list[dict[str, Any]]:
    return [spec.public_dict() for spec in _PROVIDER_SPECS if spec.listed]


def create_pydantic_provider(
    provider_id: str,
    *,
    base_url: str,
    api_key: str,
) -> Provider[Any]:
    """只通过 Pydantic Provider 公共构造器创建推理客户端。"""

    spec = provider_spec(provider_id)
    normalized_base_url = str(base_url or spec.default_base_url).strip()
    if not normalized_base_url:
        raise ValueError(f"AI Provider {spec.label} 未配置 Base URL。")
    if not spec.base_url_editable and (
        normalized_base_url.rstrip("/") != spec.default_base_url.rstrip("/")
    ):
        raise ValueError(
            f"AI Provider {spec.label} 使用固定 Base URL；"
            "请使用该服务商的官方地址。"
        )
    # 推理使用原生客户端和有限重试，不接入商城业务请求的阻断、排队或结果未知对账。
    # 保留模型超时、Agent 总任务期限与取消；AI 发起的业务工具继续走业务请求管理器。
    if spec.provider_id == PROVIDER_ID_ANTHROPIC:
        provider: Provider[Any] = AnthropicProvider(
            api_key=api_key, base_url=normalized_base_url
        )
    elif spec.provider_id == PROVIDER_ID_GOOGLE:
        provider = GoogleProvider(api_key=api_key, base_url=normalized_base_url)
    elif spec.provider_id == PROVIDER_ID_DEEPSEEK:
        provider = DeepSeekProvider(api_key=api_key)
    elif spec.provider_id == PROVIDER_ID_ALIBABA:
        provider = AlibabaProvider(base_url=normalized_base_url, api_key=api_key)
    else:
        provider = OpenAIProvider(base_url=normalized_base_url, api_key=api_key)
    return provider


_SAFE_GATEWAY_PROFILE_KEYS = (
    "supports_thinking",
    "thinking_always_enabled",
    "ignore_streamed_leading_whitespace",
)


def _safe_gateway_subset(profile: ModelProfile | None) -> ModelProfile | None:
    if not profile:
        return None
    subset: dict[str, Any] = {
        key: profile[key]
        for key in _SAFE_GATEWAY_PROFILE_KEYS
        if key in profile
    }
    return ModelProfile(**subset) if subset else None


def custom_gateway_model_profile(
    provider_id: str,
    *,
    base_url: str,
    model_name: str,
) -> ModelProfile | None:
    """为经由自定义 OpenAI 兼容网关（如 New API、DeepSeek、OpenRouter）转发的模型复用原生家族 profile。

    仅在 OpenAI Provider 使用非官方地址时生效，不修改 provider_id，也不影响官方端点；
    直接复用 Pydantic AI 内置模型家族档案，并过滤非 OpenAI 协议的原生工具标记。
    """

    spec = provider_spec(provider_id)
    if spec.provider_id != PROVIDER_ID_OPENAI:
        return None
    normalized_base_url = str(base_url or spec.default_base_url).strip().rstrip("/")
    if not normalized_base_url or normalized_base_url == spec.default_base_url.rstrip("/"):
        return None
    normalized_model = str(model_name or "").strip().lower()
    if "/" in normalized_model:
        _, normalized_model = normalized_model.split("/", 1)
    if normalized_model.startswith("deepseek-"):
        return DeepSeekProvider.model_profile(normalized_model)
    if normalized_model.startswith("qwen"):
        return qwen_model_profile(normalized_model)
    if normalized_model.startswith(("kimi-", "moonshot-")):
        return moonshotai_model_profile(normalized_model)
    if normalized_model.startswith("grok-"):
        return _safe_gateway_subset(grok_model_profile(normalized_model))
    if normalized_model.startswith("claude-"):
        return _safe_gateway_subset(anthropic_model_profile(normalized_model))
    if normalized_model.startswith("gemini-"):
        return _safe_gateway_subset(google_model_profile(normalized_model))
    return None


__all__ = [
    "AI_PROVIDER_CATALOG",
    "API_STYLE_ANTHROPIC_MESSAGES",
    "API_STYLE_GOOGLE_GEMINI",
    "API_STYLE_OPENAI_COMPATIBLE",
    "API_STYLE_OPENAI_RESPONSES",
    "AiProviderSpec",
    "PROVIDER_FAMILY_ALIBABA",
    "PROVIDER_FAMILY_ANTHROPIC",
    "PROVIDER_FAMILY_DEEPSEEK",
    "PROVIDER_FAMILY_GENERIC_OPENAI",
    "PROVIDER_FAMILY_GOOGLE",
    "PROVIDER_FAMILY_OPENAI",
    "PROVIDER_FAMILY_OMLX",
    "PROVIDER_ID_ALIBABA",
    "PROVIDER_ID_ANTHROPIC",
    "PROVIDER_ID_DEEPSEEK",
    "PROVIDER_ID_GOOGLE",
    "PROVIDER_ID_OPENAI",
    "PROVIDER_ID_OMLX",
    "create_pydantic_provider",
    "custom_gateway_model_profile",
    "normalize_provider_id",
    "provider_family_for_model",
    "provider_spec",
    "provider_spec_for_model",
    "public_provider_catalog",
]
