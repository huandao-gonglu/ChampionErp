"""ERP 已正式接入的 Pydantic AI Provider 目录与创建边界。

Pydantic AI 能按名称推断很多 Provider，但它没有面向产品配置的运行时目录。
本模块只公开当前依赖、配置契约和测试真正支持的 Provider；前端不直接枚举
Pydantic AI 包内模块，也不根据 Base URL 或模型名猜测厂商。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic_ai.providers import Provider
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider


PROVIDER_ID_OPENAI = "openai"
PROVIDER_ID_DEEPSEEK = "deepseek"
PROVIDER_ID_ALIBABA = "alibaba"

PROVIDER_FAMILY_GENERIC_OPENAI = "generic_openai"
PROVIDER_FAMILY_OPENAI = "openai"
PROVIDER_FAMILY_ALIBABA = "alibaba"

API_STYLE_OPENAI_COMPATIBLE = "openai_compatible"
API_STYLE_OPENAI_RESPONSES = "openai_responses"

DiscoveryStrategy = Literal["openai_models", "manual"]


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
        label="OpenAI",
        description=(
            "OpenAI 服务预设；代理可修改 Base URL，"
            "API 协议可选 Chat Completions 或 Responses。"
        ),
        provider_family=PROVIDER_FAMILY_OPENAI,
        default_base_url="https://api.openai.com/v1",
        default_api_style=API_STYLE_OPENAI_RESPONSES,
        supported_api_styles=(
            API_STYLE_OPENAI_COMPATIBLE,
            API_STYLE_OPENAI_RESPONSES,
        ),
        supported_model_kinds=("chat", "responses", "images"),
        base_url_editable=True,
        discovery_strategy="openai_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_DEEPSEEK,
        label="DeepSeek",
        description="使用 Pydantic AI 的 DeepSeekProvider。",
        provider_family=PROVIDER_FAMILY_GENERIC_OPENAI,
        default_base_url="https://api.deepseek.com",
        default_api_style=API_STYLE_OPENAI_COMPATIBLE,
        supported_api_styles=(API_STYLE_OPENAI_COMPATIBLE,),
        supported_model_kinds=("chat",),
        base_url_editable=False,
        discovery_strategy="openai_models",
    ),
    AiProviderSpec(
        provider_id=PROVIDER_ID_ALIBABA,
        label="阿里云百炼 / Qwen",
        description="使用 Pydantic AI 的 AlibabaProvider。",
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
    return [spec.public_dict() for spec in _PROVIDER_SPECS]


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
    if spec.provider_id == PROVIDER_ID_DEEPSEEK:
        return DeepSeekProvider(api_key=api_key)
    if spec.provider_id == PROVIDER_ID_ALIBABA:
        return AlibabaProvider(base_url=normalized_base_url, api_key=api_key)
    return OpenAIProvider(base_url=normalized_base_url, api_key=api_key)


__all__ = [
    "AI_PROVIDER_CATALOG",
    "AiProviderSpec",
    "PROVIDER_FAMILY_ALIBABA",
    "PROVIDER_FAMILY_GENERIC_OPENAI",
    "PROVIDER_FAMILY_OPENAI",
    "PROVIDER_ID_ALIBABA",
    "PROVIDER_ID_DEEPSEEK",
    "PROVIDER_ID_OPENAI",
    "create_pydantic_provider",
    "normalize_provider_id",
    "provider_family_for_model",
    "provider_spec",
    "provider_spec_for_model",
    "public_provider_catalog",
]
