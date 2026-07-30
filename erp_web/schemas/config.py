from __future__ import annotations

from typing import Any, TypedDict


class AiCapabilityProfile(TypedDict, total=False):
    version: int
    tested: bool
    connection_type: str
    provider: str
    api_style: str
    model: str
    base_url: str
    request_mode: str
    operation: str
    strategy: str
    request_body: dict[str, Any]


class AiModelConfig(TypedDict, total=False):
    id: str
    name: str
    connection_type: str
    provider: str
    api_style: str
    base_url: str
    model: str
    capabilities: list[str]
    capability_profiles: dict[str, AiCapabilityProfile]
    timeout_seconds: int
    extra: dict[str, Any]
    enabled: bool


class AiUseCaseBinding(TypedDict, total=False):
    model_id: str
    timeout_override_seconds: int


class AppConfig(TypedDict, total=False):
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


__all__ = ["AiCapabilityProfile", "AiModelConfig", "AiUseCaseBinding", "AppConfig", "StoreConfig"]
