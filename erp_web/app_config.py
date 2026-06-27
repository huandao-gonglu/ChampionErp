# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from .product_research_config import default_product_research_config, normalize_product_research_config


DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"

AI_CONFIG_ALIAS_KEYS_TO_DROP = {
    "api_provider",
    "deepseek_api_key",
    "deepseek_base_url",
    "deepseek_model",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "openai_text_api_key",
    "openai_text_model",
    "openai_image_model",
    "openai_image_quality",
    "nvidia_api_key",
    "nvidia_base_url",
    "nvidia_model",
    "text_ai_platform",
    "text_ai_api_key",
    "text_ai_base_url",
    "text_ai_model",
    "image_ai_platform",
    "image_ai_api_key",
    "image_ai_base_url",
    "image_ai_model",
    "image_ai_quality",
    "nvidia_deprecated",
}


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}****"
    return f"{text[:4]}****{text[-4:]}"


def default_app_config() -> dict[str, Any]:
    return {
        "auto_ai_recognition": "0",
        "alibaba_cookie": "",
        "1688_api": {
            "app_key": "",
            "app_secret": "",
            "access_token": "",
            "base_url": "https://gw.open.1688.com/openapi/param2/1/com.alibaba.product/alibaba.product.get",
            "method": "alibaba.product.get",
            "api_version": "1.0",
            "sign_method": "md5",
            "timeout_seconds": "20",
        },
        "text_ai": {
            "platform": "DeepSeek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
        "image_ai": {
            "platform": "OpenAI",
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-image-1",
            "quality": "medium",
        },
        "pricing_defaults": {
            "exchange_rate_api_url": DEFAULT_EXCHANGE_RATE_API_URL,
            "exchange_rate_timeout_seconds": "10",
            "exchange_rate_cache_ttl_seconds": "3600",
        },
        "product_research": default_product_research_config(),
    }


def normalize_ai_section(section: Any, defaults: dict[str, str], include_quality: bool = False) -> dict[str, str]:
    raw = section if isinstance(section, dict) else {}
    api_key = str(raw.get("api_key") or "").strip()
    normalized = {
        "platform": str(raw.get("platform") or defaults["platform"]).strip(),
        "api_key": api_key,
        "base_url": str(raw.get("base_url") or defaults["base_url"]).strip(),
        "model": str(raw.get("model") or defaults["model"]).strip(),
        "masked_key": mask_secret(api_key),
        "status": "已绑定" if api_key else "未绑定",
    }
    if normalized["platform"].lower() == "nvidia":
        normalized["platform"] = defaults["platform"]
    if include_quality:
        normalized["quality"] = str(raw.get("quality") or defaults.get("quality") or "medium").strip()
    return normalized


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize runtime config. AI config is canonical-only."""
    incoming = config if isinstance(config, dict) else {}
    defaults = default_app_config()

    text_ai = normalize_ai_section(incoming.get("text_ai"), defaults["text_ai"])
    image_ai = normalize_ai_section(incoming.get("image_ai"), defaults["image_ai"], include_quality=True)
    raw_pricing = incoming.get("pricing_defaults") if isinstance(incoming.get("pricing_defaults"), dict) else {}
    pricing_defaults = {
        "commission_percent": str(raw_pricing.get("commission_percent") or "20"),
        "target_margin_percent": str(raw_pricing.get("target_margin_percent") or "30"),
        "domestic_freight": str(raw_pricing.get("domestic_freight") or "0"),
        "international_freight": str(raw_pricing.get("international_freight") or "0"),
        "payment_fee_percent": str(raw_pricing.get("payment_fee_percent") or "0"),
        "currency_rate": str(raw_pricing.get("currency_rate") or "1"),
        "packaging_cost": str(raw_pricing.get("packaging_cost") or raw_pricing.get("packaging") or "0"),
        "default_target_margin_percent": str(raw_pricing.get("default_target_margin_percent") or raw_pricing.get("target_margin_percent") or "30"),
        "default_currency_rate": str(raw_pricing.get("default_currency_rate") or raw_pricing.get("currency_rate") or "1"),
        "default_packaging_cost": str(raw_pricing.get("default_packaging_cost") or raw_pricing.get("packaging_cost") or "0"),
        "default_domestic_freight": str(raw_pricing.get("default_domestic_freight") or raw_pricing.get("domestic_freight") or "0"),
        "mercadolibre_commission_percent": str(raw_pricing.get("mercadolibre_commission_percent") or raw_pricing.get("commission_percent") or "20"),
        "wildberries_commission_percent": str(raw_pricing.get("wildberries_commission_percent") or raw_pricing.get("commission_percent") or "20"),
        "ozon_commission_percent": str(raw_pricing.get("ozon_commission_percent") or raw_pricing.get("commission_percent") or "20"),
        "mercadolibre_payment_fee_percent": str(raw_pricing.get("mercadolibre_payment_fee_percent") or raw_pricing.get("payment_fee_percent") or "0"),
        "wildberries_payment_fee_percent": str(raw_pricing.get("wildberries_payment_fee_percent") or "0"),
        "ozon_payment_fee_percent": str(raw_pricing.get("ozon_payment_fee_percent") or "0"),
        "exchange_rate_api_url": str(raw_pricing.get("exchange_rate_api_url") or defaults["pricing_defaults"]["exchange_rate_api_url"]).strip(),
        "exchange_rate_timeout_seconds": str(raw_pricing.get("exchange_rate_timeout_seconds") or defaults["pricing_defaults"]["exchange_rate_timeout_seconds"]).strip(),
        "exchange_rate_cache_ttl_seconds": str(raw_pricing.get("exchange_rate_cache_ttl_seconds") or defaults["pricing_defaults"]["exchange_rate_cache_ttl_seconds"]).strip(),
    }

    canonical = {
        key: value
        for key, value in incoming.items()
        if key not in AI_CONFIG_ALIAS_KEYS_TO_DROP and key not in {"text_ai", "image_ai", "pricing_defaults", "product_research"}
    }
    canonical["auto_ai_recognition"] = str(canonical.get("auto_ai_recognition") or defaults["auto_ai_recognition"])
    canonical["alibaba_cookie"] = str(canonical.get("alibaba_cookie") or defaults["alibaba_cookie"])
    raw_1688_api = incoming.get("1688_api") if isinstance(incoming.get("1688_api"), dict) else {}
    current_1688_api = canonical.get("1688_api") if isinstance(canonical.get("1688_api"), dict) else {}
    defaults_1688_api = defaults["1688_api"]
    next_1688_api = {
        "app_key": str(raw_1688_api.get("app_key") or current_1688_api.get("app_key") or "").strip(),
        "app_secret": str(raw_1688_api.get("app_secret") or current_1688_api.get("app_secret") or "").strip(),
        "access_token": str(raw_1688_api.get("access_token") or current_1688_api.get("access_token") or "").strip(),
        "base_url": str(raw_1688_api.get("base_url") or current_1688_api.get("base_url") or defaults_1688_api["base_url"]).strip(),
        "method": str(raw_1688_api.get("method") or current_1688_api.get("method") or defaults_1688_api["method"]).strip(),
        "api_version": str(raw_1688_api.get("api_version") or current_1688_api.get("api_version") or defaults_1688_api["api_version"]).strip(),
        "sign_method": str(raw_1688_api.get("sign_method") or current_1688_api.get("sign_method") or defaults_1688_api["sign_method"]).strip().lower(),
        "timeout_seconds": str(raw_1688_api.get("timeout_seconds") or current_1688_api.get("timeout_seconds") or defaults_1688_api["timeout_seconds"]).strip(),
    }
    next_1688_api["masked_app_key"] = mask_secret(next_1688_api["app_key"])
    next_1688_api["masked_app_secret"] = mask_secret(next_1688_api["app_secret"])
    next_1688_api["masked_access_token"] = mask_secret(next_1688_api["access_token"])
    next_1688_api["status"] = "已配置" if next_1688_api["app_key"] and next_1688_api["app_secret"] else "未配置"
    canonical["1688_api"] = next_1688_api
    canonical["text_ai"] = text_ai
    canonical["image_ai"] = image_ai
    canonical["pricing_defaults"] = pricing_defaults
    canonical["product_research"] = normalize_product_research_config(incoming.get("product_research"))
    return canonical
