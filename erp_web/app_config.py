# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from erp_web.services import ai_model_config, ai_prompt_templates

from .product_research_config import default_product_research_config, normalize_product_research_config


DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
PRESERVED_APP_CONFIG_KEYS = {"auto_ai_recognition", "alibaba_cookie", "mercadolibre_title_limit"}
YUNEXPRESS_SANDBOX_BASE_URL = "https://openapi-sbx.yunexpress.cn"
YUNEXPRESS_PRODUCTION_BASE_URL = "https://openapi.yunexpress.cn"
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
_RETIRED_YUNEXPRESS_KEYS = frozenset(
    {
        "appId",
        "appSecret",
        "sourceKey",
        "productCode",
        "sourceCode",
        "platformAccountCode",
        "labelType",
        "weightUnit",
        "sizeUnit",
        "timeoutSeconds",
    }
)


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
        "yunexpress": {
            "environment": "sandbox",
            "base_url": YUNEXPRESS_SANDBOX_BASE_URL,
            "app_id": "",
            "app_secret": "",
            "source_key": "",
            "product_code": "",
            "source_code": "",
            "platform_account_code": "",
            "label_type": "PDF",
            "weight_unit": "KG",
            "size_unit": "CM",
            "timeout_seconds": "20",
        },
        "ai_models": ai_model_config.default_ai_models(),
        "ai_use_case_bindings": {},
        "ai_use_case_prompts": ai_prompt_templates.default_ai_use_case_prompts(),
        "pricing_defaults": {
            "commission_percent": "20",
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
            "mercadolibre_commission_percent": "20",
            "wildberries_commission_percent": "20",
            "ozon_commission_percent": "20",
            "mercadolibre_payment_fee_percent": "0",
            "wildberries_payment_fee_percent": "0",
            "ozon_payment_fee_percent": "0",
            "exchange_rate_api_url": DEFAULT_EXCHANGE_RATE_API_URL,
            "exchange_rate_timeout_seconds": "10",
            "exchange_rate_cache_ttl_seconds": "3600",
        },
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
    allowed_top_level = set(defaults) | PRESERVED_APP_CONFIG_KEYS
    unknown_top_level = sorted(set(incoming) - allowed_top_level)
    if unknown_top_level:
        raise ValueError(
            "app_config 含有不受支持的顶层字段："
            + ", ".join(unknown_top_level)
        )

    raw_ai_models = incoming.get("ai_models")
    has_canonical_ai_models = isinstance(raw_ai_models, list) and bool(raw_ai_models)
    ai_models = ai_model_config.normalize_ai_models(raw_ai_models if has_canonical_ai_models else defaults["ai_models"])
    ai_use_case_bindings = ai_model_config.normalize_ai_use_case_bindings(incoming.get("ai_use_case_bindings"))
    ai_use_case_prompts = ai_prompt_templates.normalize_ai_use_case_prompts(incoming.get("ai_use_case_prompts"))
    raw_pricing = incoming.get("pricing_defaults") if isinstance(incoming.get("pricing_defaults"), dict) else {}
    unknown_pricing_keys = sorted(
        set(raw_pricing) - set(defaults["pricing_defaults"])
    )
    if unknown_pricing_keys:
        raise ValueError(
            "pricing_defaults 含有已退役或不受支持的字段："
            + ", ".join(unknown_pricing_keys)
        )
    pricing_defaults = {
        key: str(raw_pricing.get(key) or default_value).strip()
        for key, default_value in defaults["pricing_defaults"].items()
    }

    canonical = {key: incoming[key] for key in PRESERVED_APP_CONFIG_KEYS if key in incoming}
    canonical["auto_ai_recognition"] = str(canonical.get("auto_ai_recognition") or defaults["auto_ai_recognition"])
    canonical["alibaba_cookie"] = str(canonical.get("alibaba_cookie") or defaults["alibaba_cookie"])
    raw_1688_api = incoming.get("1688_api") if isinstance(incoming.get("1688_api"), dict) else {}
    defaults_1688_api = defaults["1688_api"]
    next_1688_api = {
        "app_key": str(raw_1688_api.get("app_key") or "").strip(),
        "app_secret": str(raw_1688_api.get("app_secret") or "").strip(),
        "access_token": str(raw_1688_api.get("access_token") or "").strip(),
        "base_url": str(raw_1688_api.get("base_url") or defaults_1688_api["base_url"]).strip(),
        "method": str(raw_1688_api.get("method") or defaults_1688_api["method"]).strip(),
        "api_version": str(raw_1688_api.get("api_version") or defaults_1688_api["api_version"]).strip(),
        "sign_method": str(raw_1688_api.get("sign_method") or defaults_1688_api["sign_method"]).strip().lower(),
        "timeout_seconds": str(raw_1688_api.get("timeout_seconds") or defaults_1688_api["timeout_seconds"]).strip(),
    }
    next_1688_api["masked_app_key"] = mask_secret(next_1688_api["app_key"])
    next_1688_api["masked_app_secret"] = mask_secret(next_1688_api["app_secret"])
    next_1688_api["masked_access_token"] = mask_secret(next_1688_api["access_token"])
    next_1688_api["status"] = "已配置" if next_1688_api["app_key"] and next_1688_api["app_secret"] else "未配置"
    canonical["1688_api"] = next_1688_api
    raw_yunexpress = incoming.get("yunexpress") if isinstance(incoming.get("yunexpress"), dict) else {}
    retired_yunexpress_keys = sorted(
        set(raw_yunexpress) & _RETIRED_YUNEXPRESS_KEYS
    )
    if retired_yunexpress_keys:
        raise ValueError(
            "yunexpress 含有已退役的 camelCase 字段："
            + ", ".join(retired_yunexpress_keys)
        )
    defaults_yunexpress = defaults["yunexpress"]
    environment = str(raw_yunexpress.get("environment") or defaults_yunexpress["environment"]).strip().lower()
    if environment not in {"sandbox", "production"}:
        environment = "sandbox"
    default_base_url = YUNEXPRESS_PRODUCTION_BASE_URL if environment == "production" else YUNEXPRESS_SANDBOX_BASE_URL
    next_yunexpress = {
        "environment": environment,
        "base_url": str(raw_yunexpress.get("base_url") or default_base_url).strip().rstrip("/") or default_base_url,
        "app_id": str(raw_yunexpress.get("app_id") or "").strip(),
        "app_secret": str(raw_yunexpress.get("app_secret") or "").strip(),
        "source_key": str(raw_yunexpress.get("source_key") or "").strip(),
        "product_code": str(raw_yunexpress.get("product_code") or "").strip(),
        "source_code": str(raw_yunexpress.get("source_code") or "").strip(),
        "platform_account_code": str(raw_yunexpress.get("platform_account_code") or "").strip(),
        "label_type": str(raw_yunexpress.get("label_type") or defaults_yunexpress["label_type"]).strip().upper() or "PDF",
        "weight_unit": str(raw_yunexpress.get("weight_unit") or defaults_yunexpress["weight_unit"]).strip().upper() or "KG",
        "size_unit": str(raw_yunexpress.get("size_unit") or defaults_yunexpress["size_unit"]).strip().upper() or "CM",
        "timeout_seconds": str(raw_yunexpress.get("timeout_seconds") or defaults_yunexpress["timeout_seconds"]).strip(),
    }
    next_yunexpress["masked_app_id"] = mask_secret(next_yunexpress["app_id"])
    next_yunexpress["masked_app_secret"] = mask_secret(next_yunexpress["app_secret"])
    next_yunexpress["masked_source_key"] = mask_secret(next_yunexpress["source_key"])
    next_yunexpress["status"] = "已配置" if next_yunexpress["app_id"] and next_yunexpress["app_secret"] and next_yunexpress["source_key"] else "未配置"
    canonical["yunexpress"] = next_yunexpress
    canonical["ai_models"] = ai_models
    canonical["ai_use_case_bindings"] = ai_use_case_bindings
    canonical["ai_use_case_prompts"] = ai_use_case_prompts
    canonical["pricing_defaults"] = pricing_defaults
    canonical["product_research"] = normalize_product_research_config(incoming.get("product_research"))
    ai_model_config.validate_ai_use_case_generation_bindings(canonical)
    ai_model_config.validate_ai_model_request_overrides(canonical)
    return canonical
