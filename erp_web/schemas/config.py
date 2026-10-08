from __future__ import annotations

from typing import Any, TypedDict
from pydantic import BaseModel, ConfigDict, Field
from .ai_approval import AiToolApprovalMode


class AiCapabilityProfile(TypedDict, total=False):
    version: int
    tested: bool
    connection_type: str
    provider_id: str
    api_style: str
    model: str
    base_url: str
    request_mode: str
    operation: str
    strategy: str
    tested_at: str
    probe_version: str
    configuration_fingerprint: str


class AiModelConfig(TypedDict, total=False):
    id: str
    name: str
    connection_type: str
    provider_id: str
    provider: str
    api_style: str
    base_url: str
    model: str
    capabilities: list[str]
    capability_profiles: dict[str, AiCapabilityProfile]
    timeout_seconds: int
    thinking_enabled: bool  # 缺省时沿用高级请求配置或服务商默认。
    extra: dict[str, Any]
    enabled: bool


class AiReasoningSettings(TypedDict, total=False):
    mode: str
    effort: str
    budget_tokens: int


class AiGenerationSettings(TypedDict, total=False):
    temperature: float
    max_output_tokens: int
    reasoning: AiReasoningSettings


class AiUseCaseBinding(TypedDict, total=False):
    model_id: str
    timeout_override_seconds: int
    generation: AiGenerationSettings


class SystemSettings(TypedDict):
    orders_auto_sync_interval_hours: int


class SystemSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders_auto_sync_interval_hours: int = Field(default=5, ge=5, strict=True)


class AppConfig(TypedDict, total=False):
    system_settings: SystemSettings
    image_hosting: dict[str, Any]
    ai_tool_approval_mode: AiToolApprovalMode
    ai_models: list[AiModelConfig]
    ai_use_case_bindings: dict[str, AiUseCaseBinding]
    ai_use_case_prompts: dict[str, dict[str, str]]
    pricing: dict[str, Any]
    pricing_defaults: dict[str, Any]
    product_research: dict[str, Any]
    browser: dict[str, Any]


class StoreConfig(TypedDict, total=False):
    mercadolibre: dict[str, Any]
    yandex: dict[str, Any]
    ozon: dict[str, Any]


__all__ = [
    "AiCapabilityProfile",
    "AiGenerationSettings",
    "AiModelConfig",
    "AiReasoningSettings",
    "AiUseCaseBinding",
    "AppConfig",
    "StoreConfig",
    "SystemSettings",
    "SystemSettingsRequest",
]
