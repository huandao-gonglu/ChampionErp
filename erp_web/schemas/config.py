from __future__ import annotations

from typing import Any, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .ai_approval import AiToolApprovalMode
from .wechat_channel import WECHAT_FORWARD_TYPES, WechatForwardType


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


class WatermarkSettings(TypedDict):
    text: str
    image_data_url: str
    opacity: int
    size_percent: int
    density: int
    color: str


class WatermarkPreview(TypedDict):
    preview_url: str
    sample_name: str
    message: str


class WatermarkSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(default="", max_length=200)
    image_data_url: str = Field(default="", max_length=2_800_000)
    opacity: int = Field(default=25, ge=1, le=100, strict=True)
    size_percent: int = Field(default=30, ge=5, le=80, strict=True)
    density: int = Field(default=5, ge=1, le=10, strict=True)
    color: str = Field(default="#737373", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="before")
    @classmethod
    def read_previous_settings(cls, value: Any) -> Any:
        """旧配置保留文字和图片，保存后只写平铺字段；有内容即生效。"""
        if isinstance(value, dict):
            return {key: item for key, item in value.items()
                    if key not in {"enabled", "kind", "position", "margin_percent"}}
        return value


class SystemSettings(TypedDict):
    orders_auto_sync_interval_hours: int
    watermark: WatermarkSettings
    wechat_forward_types: list[WechatForwardType]


class SystemSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders_auto_sync_interval_hours: int = Field(default=5, ge=5, strict=True)
    watermark: WatermarkSettingsRequest = Field(default_factory=WatermarkSettingsRequest)
    wechat_forward_types: list[WechatForwardType] = Field(default_factory=lambda: list(WECHAT_FORWARD_TYPES))


class AppConfig(TypedDict, total=False):
    system_settings: SystemSettings
    image_hosting: dict[str, Any]
    ai_tool_approval_mode: AiToolApprovalMode
    ai_models: list[AiModelConfig]
    ai_use_case_bindings: dict[str, AiUseCaseBinding]
    ai_use_case_prompts: dict[str, dict[str, str]]
    pricing: dict[str, Any]
    pricing_defaults: dict[str, Any]
    yandex_warehouses: dict[str, Any]
    pricing_templates: dict[str, Any]
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
    "WatermarkSettings",
    "WatermarkSettingsRequest",
    "WatermarkPreview",
]
