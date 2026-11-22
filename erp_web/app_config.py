# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from erp_web.services import ai_model_config
from erp_web.services.image_hosting_config import normalize_image_hosting
from erp_web.schemas.ai_approval import normalize_ai_tool_approval_mode
from erp_web.schemas.config import SystemSettingsRequest
from erp_web.schemas.forbidden_categories import ForbiddenCategoryConfiguration
from erp_web.schemas.pricing_templates import PricingTemplateSettings
from erp_web.schemas.yandex_warehouses import YandexWarehouseConfiguration

from .product_research_config import (
    default_product_research_config,
    normalize_product_research_config,
)


DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
PRESERVED_APP_CONFIG_KEYS = {"auto_ai_recognition", "alibaba_cookie"}
_RETIRED_AI_CONFIG_KEYS = frozenset(
    {
        "api_provider",
        "deepseek_api_key",
        "deepseek_base_url",
        "deepseek_model",
        "text_ai",
        "text_ai_api_key",
        "text_ai_base_url",
        "text_ai_model",
        "image_ai",
        "image_ai_api_key",
        "image_ai_base_url",
        "image_ai_model",
        "image_ai_platform",
        "image_ai_quality",
        "openai_api_key",
        "openai_base_url",
        "openai_image_model",
        "openai_image_quality",
        "openai_model",
    }
)


def default_app_config() -> dict[str, Any]:
    return {
        "system_settings": SystemSettingsRequest().model_dump(),
        "forbidden_categories": ForbiddenCategoryConfiguration().model_dump(),
        "image_hosting": {"default_profile_id": "", "profiles": []},
        "ai_tool_approval_mode": "ask",
        "auto_ai_recognition": "0",
        "alibaba_cookie": "",
        "1688_api": {
            "app_key": "",
            "app_secret": "",
            "access_token": "",
            "timeout_seconds": "20",
        },
        "ai_models": ai_model_config.default_ai_models(),
        "ai_use_case_bindings": {},
        "pricing_defaults": {
            "target_margin_percent": "30",
            "domestic_freight": "0",
            "international_freight": "0",
            "payment_fee_percent": "0",
            "currency_rate": "1",
            "packaging_cost": "0",
            "default_target_margin_percent": "30",
            "default_currency_rate": "1",
            "default_packaging_cost": "0",
            "default_domestic_freight": "0",
            "mercadolibre_payment_fee_percent": "0",
            "wildberries_payment_fee_percent": "0",
            "ozon_payment_fee_percent": "0",
            "exchange_rate_api_url": DEFAULT_EXCHANGE_RATE_API_URL,
            "exchange_rate_timeout_seconds": "10",
            "exchange_rate_cache_ttl_seconds": "3600",
        },
        "pricing_templates": PricingTemplateSettings().model_dump(),
        "yandex_warehouses": YandexWarehouseConfiguration().model_dump(),
        "product_research": default_product_research_config(),
    }


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the current app-config shape."""
    if not isinstance(config, dict):
        raise ValueError("app_config 必须是 JSON object")
    incoming = config
    defaults = default_app_config()
    retired_ai_keys = sorted(set(incoming) & _RETIRED_AI_CONFIG_KEYS)
    if retired_ai_keys:
        raise ValueError(
            "app_config 含有已退役的 AI 配置字段："
            + ", ".join(retired_ai_keys)
            + "；请仅使用 ai_models"
        )
    allowed_top_level = set(defaults) | PRESERVED_APP_CONFIG_KEYS | {"ai_use_case_prompts"}
    unknown_top_level = sorted(set(incoming) - allowed_top_level)
    if unknown_top_level:
        raise ValueError(
            "app_config 含有不受支持的顶层字段：" + ", ".join(unknown_top_level)
        )

    raw_ai_models = incoming.get("ai_models")
    has_canonical_ai_models = isinstance(raw_ai_models, list) and bool(raw_ai_models)
    ai_models = ai_model_config.normalize_ai_models(
        raw_ai_models if has_canonical_ai_models else defaults["ai_models"]
    )
    ai_use_case_bindings = ai_model_config.normalize_ai_use_case_bindings(
        incoming.get("ai_use_case_bindings")
    )
    # 旧持久化路径继续可读，但不进入当前配置，也不再作为提示词来源。
    raw_pricing = (
        incoming.get("pricing_defaults")
        if isinstance(incoming.get("pricing_defaults"), dict)
        else {}
    )
    # 旧配置继续可读；这些历史手填佣金不再进入新配置或核价。
    retired_commissions = {"commission_percent", "mercadolibre_commission_percent", "wildberries_commission_percent", "ozon_commission_percent"}
    raw_pricing = {key: value for key, value in raw_pricing.items() if key not in retired_commissions}
    unknown_pricing_keys = sorted(set(raw_pricing) - set(defaults["pricing_defaults"]))
    if unknown_pricing_keys:
        raise ValueError(
            "pricing_defaults 含有已退役或不受支持的字段："
            + ", ".join(unknown_pricing_keys)
        )
    pricing_defaults = {
        key: str(raw_pricing.get(key) or default_value).strip()
        for key, default_value in defaults["pricing_defaults"].items()
    }

    canonical = {
        key: incoming[key] for key in PRESERVED_APP_CONFIG_KEYS if key in incoming
    }
    canonical["ai_tool_approval_mode"] = normalize_ai_tool_approval_mode(
        incoming.get("ai_tool_approval_mode", "ask")
    )
    raw_system = incoming.get("system_settings")
    if isinstance(raw_system, dict):
        # 已退役的 sync_policies 允许既有配置无损读取，重新保存时自然剥离。
        raw_system = {key: value for key, value in raw_system.items() if key != "sync_policies"}
    else:
        raw_system = {}
    canonical["system_settings"] = SystemSettingsRequest.model_validate(raw_system).model_dump()
    canonical["forbidden_categories"] = ForbiddenCategoryConfiguration.model_validate(incoming.get("forbidden_categories", {})).model_dump()
    canonical["auto_ai_recognition"] = str(
        canonical.get("auto_ai_recognition") or defaults["auto_ai_recognition"]
    )
    canonical["alibaba_cookie"] = str(
        canonical.get("alibaba_cookie") or defaults["alibaba_cookie"]
    )
    raw_1688_api = (
        incoming.get("1688_api") if isinstance(incoming.get("1688_api"), dict) else {}
    )
    defaults_1688_api = defaults["1688_api"]
    next_1688_api = {
        "app_key": str(raw_1688_api.get("app_key") or "").strip(),
        "app_secret": str(raw_1688_api.get("app_secret") or "").strip(),
        "access_token": str(raw_1688_api.get("access_token") or "").strip(),
        "timeout_seconds": str(
            raw_1688_api.get("timeout_seconds") or defaults_1688_api["timeout_seconds"]
        ).strip(),
    }
    canonical["1688_api"] = next_1688_api
    canonical["image_hosting"] = normalize_image_hosting(incoming.get("image_hosting"))
    canonical["ai_models"] = ai_models
    canonical["ai_use_case_bindings"] = ai_use_case_bindings
    canonical["pricing_defaults"] = pricing_defaults
    canonical["pricing_templates"] = PricingTemplateSettings.model_validate(incoming.get("pricing_templates", {})).model_dump(exclude_none=True)
    canonical["yandex_warehouses"] = YandexWarehouseConfiguration.model_validate(incoming.get("yandex_warehouses", {})).model_dump()
    canonical["product_research"] = normalize_product_research_config(
        incoming.get("product_research")
    )
    ai_model_config.validate_ai_use_case_generation_bindings(canonical)
    ai_model_config.validate_ai_model_request_overrides(canonical)
    return canonical
