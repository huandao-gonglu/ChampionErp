# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from services import ai_model_config

from .product_research_config import default_product_research_config, normalize_product_research_config


DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
PRESERVED_APP_CONFIG_KEYS = {"auto_ai_recognition", "alibaba_cookie", "mercadolibre_title_limit"}


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}****"
    return f"{text[:4]}****{text[-4:]}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _find_model_index(models: list[dict[str, Any]], preferred_id: str, capabilities: set[str]) -> int:
    for index, model in enumerate(models):
        if str(model.get("id") or "") == preferred_id:
            return index
    for index, model in enumerate(models):
        model_caps = set(ai_model_config.normalize_capabilities(model.get("capabilities")))
        if capabilities and capabilities.issubset(model_caps):
            return index
    return -1


def _apply_legacy_model_values(model: dict[str, Any], values: dict[str, str], *, override_existing: bool) -> dict[str, Any]:
    next_model = dict(model)
    for key, value in values.items():
        if not value:
            continue
        if override_existing or not str(next_model.get(key) or "").strip():
            next_model[key] = value
    return next_model


def migrate_legacy_ai_config(incoming: dict[str, Any], ai_models: list[dict[str, Any]], *, override_defaults: bool) -> list[dict[str, Any]]:
    """Read old AI config fields once and map them into canonical ai_models."""
    legacy_text = _as_dict(incoming.get("text_ai"))
    legacy_image = _as_dict(incoming.get("image_ai"))
    text_values = {
        "provider": _first_text(legacy_text.get("platform"), incoming.get("api_provider")),
        "api_key": _first_text(legacy_text.get("api_key"), incoming.get("text_ai_api_key"), incoming.get("deepseek_api_key")),
        "base_url": _first_text(legacy_text.get("base_url"), incoming.get("text_ai_base_url"), incoming.get("deepseek_base_url")),
        "model": _first_text(legacy_text.get("model"), incoming.get("text_ai_model"), incoming.get("deepseek_model")),
    }
    image_values = {
        "provider": _first_text(legacy_image.get("platform"), incoming.get("image_ai_platform"), "OpenAI" if _first_text(legacy_image.get("api_key"), incoming.get("image_ai_api_key"), incoming.get("openai_api_key")) else ""),
        "api_key": _first_text(legacy_image.get("api_key"), incoming.get("image_ai_api_key"), incoming.get("openai_api_key")),
        "base_url": _first_text(legacy_image.get("base_url"), incoming.get("image_ai_base_url"), incoming.get("openai_base_url")),
        "model": _first_text(legacy_image.get("model"), incoming.get("image_ai_model"), incoming.get("openai_image_model"), incoming.get("openai_model")),
        "quality": _first_text(legacy_image.get("quality"), incoming.get("image_ai_quality"), incoming.get("openai_image_quality")),
    }
    if not any(text_values.values()) and not any(image_values.values()):
        return ai_models

    models = [dict(model) for model in ai_models]
    text_index = _find_model_index(models, "default_text", {ai_model_config.CAP_CHAT, ai_model_config.CAP_JSON})
    if text_index >= 0 and any(text_values.values()):
        models[text_index] = _apply_legacy_model_values(models[text_index], text_values, override_existing=override_defaults)

    image_index = _find_model_index(models, "default_image", {ai_model_config.CAP_IMAGE_EDIT})
    if image_index >= 0 and any(image_values.values()):
        models[image_index] = _apply_legacy_model_values(models[image_index], image_values, override_existing=override_defaults)

    return models


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
        "ai_models": ai_model_config.default_ai_models(),
        "ai_use_case_bindings": {},
        "pricing_defaults": {
            "exchange_rate_api_url": DEFAULT_EXCHANGE_RATE_API_URL,
            "exchange_rate_timeout_seconds": "10",
            "exchange_rate_cache_ttl_seconds": "3600",
        },
        "product_research": default_product_research_config(),
    }


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize runtime config. AI config is stored in canonical ai_models."""
    incoming = config if isinstance(config, dict) else {}
    defaults = default_app_config()

    raw_ai_models = incoming.get("ai_models")
    has_canonical_ai_models = isinstance(raw_ai_models, list) and bool(raw_ai_models)
    ai_models = ai_model_config.normalize_ai_models(raw_ai_models if has_canonical_ai_models else defaults["ai_models"])
    ai_models = migrate_legacy_ai_config(incoming, ai_models, override_defaults=not has_canonical_ai_models)
    ai_models = ai_model_config.normalize_ai_models(ai_models)
    ai_use_case_bindings = ai_model_config.normalize_ai_use_case_bindings(incoming.get("ai_use_case_bindings"))
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

    canonical = {key: incoming[key] for key in PRESERVED_APP_CONFIG_KEYS if key in incoming}
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
    canonical["ai_models"] = ai_models
    canonical["ai_use_case_bindings"] = ai_use_case_bindings
    canonical["pricing_defaults"] = pricing_defaults
    canonical["product_research"] = normalize_product_research_config(incoming.get("product_research"))
    return canonical
