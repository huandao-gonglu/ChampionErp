# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

PRODUCT_RESEARCH_SOURCE_TYPES = {"api", "ai_search", "crawler", "third_party_api", "manual_import"}
PRODUCT_RESEARCH_SEARCH_MODES = {"target_only", "target_plus_reference", "global_scan"}
DEFAULT_MARKET_ID_BY_CODE = {
    "US": "amazon-us",
    "GB": "amazon-uk",
    "UK": "amazon-uk",
    "CA": "amazon-ca",
    "AU": "amazon-au",
}
DEFAULT_AI_SEARCH_METHOD_ID = "ai_web_search"
LEGACY_AI_SEARCH_METHOD_IDS = {"ai_market_search_seeded"}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


def _int_value(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if min_value is not None:
        number = max(number, min_value)
    if max_value is not None:
        number = min(number, max_value)
    return number


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _default_market_search_methods() -> list[dict[str, Any]]:
    return [
        {
            "method_id": DEFAULT_AI_SEARCH_METHOD_ID,
            "enabled": True,
            "config_json": {},
        }
    ]


def default_product_research_config() -> dict[str, Any]:
    defaults = {
        "search_defaults": {
            "search_mode": "target_only",
            "target_markets": ["amazon-us"],
            "reference_markets": [],
            "limit": 12,
            "max_limit": 100,
            "sort_by": "rank",
        },
        "provider_runtime": {
            "source_timeout_seconds": 120,
            "retry_count": 1,
            "cache_ttl_seconds": 21600,
        },
        "source_registry": [
            {
                "id": DEFAULT_AI_SEARCH_METHOD_ID,
                "name": "AI 搜索",
                "source_type": "ai_search",
                "platform": "ai_model",
                "enabled": True,
                "priority": 1,
                "supported_markets": [],
                "supported_languages": [],
                "supported_data_types": ["ai_web_search"],
                "auth_required": False,
                "rate_limit_per_minute": 20,
                "compliance_note": "通过已配置的联网 AI 模型获取真实可追溯的热卖商品候选。",
                "config_json": {
                    "provider_strategy": "ai_web_search",
                    "ai_model_id": "",
                    "max_items": 12,
                    "require_source_url": True,
                    "require_image_url": True,
                    "stream": True,
                },
            },
        ],
    }
    defaults["search_providers"] = [deepcopy(item) for item in defaults["source_registry"]]
    defaults["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
        },
        {
            "id": "amazon-uk",
            "platform": "amazon",
            "site": "amazon.co.uk",
            "display_name": "Amazon UK",
        },
        {
            "id": "amazon-ca",
            "platform": "amazon",
            "site": "amazon.ca",
            "display_name": "Amazon Canada",
        },
        {
            "id": "amazon-au",
            "platform": "amazon",
            "site": "amazon.com.au",
            "display_name": "Amazon Australia",
        },
    ]
    return defaults


def _normalize_product_research_source(source: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    defaults = fallback if isinstance(fallback, dict) else {}
    source_type = str(raw.get("source_type") or defaults.get("source_type") or "manual_import").strip().lower()
    if source_type not in PRODUCT_RESEARCH_SOURCE_TYPES:
        source_type = "manual_import"
    source_id = str(raw.get("id") or raw.get("source_id") or defaults.get("id") or defaults.get("source_id") or "").strip()
    if source_id in LEGACY_AI_SEARCH_METHOD_IDS:
        source_id = DEFAULT_AI_SEARCH_METHOD_ID
    platform = str(raw.get("platform") or defaults.get("platform") or source_id or "manual_import").strip().lower()
    if not source_id:
        source_id = platform
    config_json = raw.get("config_json") if isinstance(raw.get("config_json"), dict) else defaults.get("config_json")
    config_json = dict(config_json) if isinstance(config_json, dict) else {}
    if source_type == "ai_search":
        for prompt_key in (
            "prompt",
            "promptTemplate",
            "prompt_template",
            "prompt_template_path",
            "promptTemplatePath",
            "system_prompt",
            "systemPrompt",
        ):
            config_json.pop(prompt_key, None)
        config_json = {
            "provider_strategy": "ai_web_search",
            "ai_model_id": "",
            "max_items": 12,
            "require_image_url": True,
            **config_json,
        }
        if config_json.get("provider_strategy") == "seeded_mock":
            config_json["provider_strategy"] = "ai_web_search"
    rate_limit_raw = raw.get("rate_limit_per_minute", defaults.get("rate_limit_per_minute"))
    rate_limit_per_minute = None if rate_limit_raw is None or str(rate_limit_raw).strip() == "" else _int_value(rate_limit_raw, 0, 0)
    return {
        "id": source_id,
        "name": str(raw.get("name") or defaults.get("name") or source_id).strip(),
        "source_type": source_type,
        "platform": platform,
        "enabled": _bool_value(raw.get("enabled"), _bool_value(defaults.get("enabled"), True)),
        "priority": _int_value(raw.get("priority", defaults.get("priority", 10)), 10, 1, 1000),
        "supported_markets": _string_list(raw.get("supported_markets", defaults.get("supported_markets", []))),
        "supported_languages": _string_list(raw.get("supported_languages", defaults.get("supported_languages", []))),
        "supported_data_types": _string_list(raw.get("supported_data_types", defaults.get("supported_data_types", []))),
        "auth_required": _bool_value(raw.get("auth_required"), _bool_value(defaults.get("auth_required"), False)),
        "rate_limit_per_minute": rate_limit_per_minute,
        "compliance_note": str(raw.get("compliance_note") or defaults.get("compliance_note") or "").strip(),
        "config_json": config_json,
    }


def _source_strategy(source: dict[str, Any]) -> str:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    return str(config_json.get("provider_strategy") or source.get("provider_strategy") or "").strip()


def _site_slug(value: str) -> str:
    return "-".join(part for part in value.lower().replace(".", "-").split("-") if part)


def _target_market_id(raw: dict[str, Any], defaults: dict[str, Any]) -> str:
    explicit_id = str(
        raw.get("id")
        or raw.get("market_id")
        or raw.get("marketId")
        or defaults.get("id")
        or defaults.get("market_id")
        or defaults.get("marketId")
        or ""
    ).strip()
    if explicit_id:
        return explicit_id
    legacy_code = str(raw.get("market") or raw.get("code") or defaults.get("market") or defaults.get("code") or "").strip().upper()
    if legacy_code in DEFAULT_MARKET_ID_BY_CODE:
        return DEFAULT_MARKET_ID_BY_CODE[legacy_code]
    platform = str(raw.get("platform") or defaults.get("platform") or "").strip().lower()
    site = str(raw.get("site") or defaults.get("site") or "").strip().lower()
    if platform and site:
        return f"{platform}-{_site_slug(site)}"
    return legacy_code.lower()


def _normalize_target_market(market: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = market if isinstance(market, dict) else {"id": market}
    defaults = fallback if isinstance(fallback, dict) else {}
    market_id = _target_market_id(raw, defaults)
    platform = str(raw.get("platform") or defaults.get("platform") or "").strip().lower()
    site = str(raw.get("site") or defaults.get("site") or "").strip().lower()
    display_name = str(
        raw.get("display_name")
        or raw.get("displayName")
        or raw.get("name")
        or defaults.get("display_name")
        or defaults.get("displayName")
        or defaults.get("name")
        or market_id
    ).strip()
    normalized = {
        "id": market_id,
        "platform": platform,
        "site": site,
        "display_name": display_name,
    }
    search_methods_raw = raw.get("search_methods") if isinstance(raw.get("search_methods"), list) else raw.get("searchMethods")
    if not isinstance(search_methods_raw, list):
        legacy_provider_ids = _string_list(raw.get("provider_ids") or raw.get("providerIds") or raw.get("source_ids") or raw.get("sourceIds"))
        search_methods_raw = [
            {"method_id": method_id, "enabled": True, "config_json": {}}
            for method_id in legacy_provider_ids
        ] if legacy_provider_ids else defaults.get("search_methods")
    if not isinstance(search_methods_raw, list):
        search_methods_raw = _default_market_search_methods()
    normalized["search_methods"] = [
        _normalize_market_search_method_binding(item, normalized)
        for item in search_methods_raw
        if isinstance(item, dict)
    ]
    return normalized


def _normalize_market_search_method_binding(value: Any, market: dict[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    method_id = str(raw.get("method_id") or raw.get("methodId") or raw.get("provider_id") or raw.get("providerId") or raw.get("id") or "").strip()
    if method_id in LEGACY_AI_SEARCH_METHOD_IDS:
        method_id = DEFAULT_AI_SEARCH_METHOD_ID
    config_json = raw.get("config_json") if isinstance(raw.get("config_json"), dict) else raw.get("configJson")
    if not isinstance(config_json, dict):
        config_json = {}
    config_json = dict(config_json)
    for prompt_key in (
        "promptOverride",
        "prompt_override",
        "promptTemplate",
        "prompt_template",
        "prompt_template_path",
        "promptTemplatePath",
    ):
        config_json.pop(prompt_key, None)
    return {
        "method_id": method_id,
        "enabled": _bool_value(raw.get("enabled"), True),
        "config_json": dict(config_json),
    }


def normalize_product_research_config(config: Any) -> dict[str, Any]:
    defaults = default_product_research_config()
    raw = config if isinstance(config, dict) else {}
    search_defaults_raw = raw.get("search_defaults") if isinstance(raw.get("search_defaults"), dict) else {}
    provider_runtime_raw = raw.get("provider_runtime") if isinstance(raw.get("provider_runtime"), dict) else {}

    search_defaults = dict(defaults["search_defaults"])
    search_defaults.update({key: value for key, value in search_defaults_raw.items() if value is not None})
    search_mode = str(search_defaults.get("search_mode") or defaults["search_defaults"]["search_mode"]).strip()
    if search_mode not in PRODUCT_RESEARCH_SEARCH_MODES:
        search_mode = defaults["search_defaults"]["search_mode"]
    max_limit = _int_value(search_defaults.get("max_limit"), defaults["search_defaults"]["max_limit"], 1, 500)
    search_defaults = {
        **search_defaults,
        "search_mode": search_mode,
        "target_markets": _string_list(search_defaults.get("target_markets")) or defaults["search_defaults"]["target_markets"],
        "reference_markets": _string_list(search_defaults.get("reference_markets")),
        "limit": _int_value(search_defaults.get("limit"), defaults["search_defaults"]["limit"], 1, max_limit),
        "max_limit": max_limit,
        "sort_by": str(search_defaults.get("sort_by") or defaults["search_defaults"]["sort_by"]).strip(),
    }

    provider_runtime = dict(defaults["provider_runtime"])
    provider_runtime.update({key: value for key, value in provider_runtime_raw.items() if value is not None})
    provider_runtime = {
        "source_timeout_seconds": _int_value(provider_runtime.get("source_timeout_seconds"), defaults["provider_runtime"]["source_timeout_seconds"], 30, 300),
        "retry_count": _int_value(provider_runtime.get("retry_count"), defaults["provider_runtime"]["retry_count"], 0, 5),
        "cache_ttl_seconds": _int_value(provider_runtime.get("cache_ttl_seconds"), defaults["provider_runtime"]["cache_ttl_seconds"], 0, 604800),
    }
    default_sources = defaults["source_registry"]
    raw_providers = raw.get("search_providers")
    raw_sources = raw.get("source_registry")
    provider_rows = raw_providers
    if isinstance(raw_sources, list) and (
        not isinstance(raw_providers, list)
        or raw_providers == defaults.get("search_providers")
    ):
        provider_rows = raw_sources
    search_providers = (
        [_normalize_product_research_source(item) for item in provider_rows if isinstance(item, dict)]
        if isinstance(provider_rows, list)
        else (
            [_normalize_product_research_source(item) for item in raw_sources if isinstance(item, dict)]
            if isinstance(raw_sources, list)
            else [_normalize_product_research_source(item) for item in default_sources]
        )
    )
    search_providers = [item for item in search_providers if item.get("id") and _source_strategy(item) != "seeded_mock"]
    source_registry = [deepcopy(item) for item in search_providers]
    source_registry = [item for item in source_registry if item.get("id")]

    target_market_defaults = {
        item["id"]: item for item in defaults.get("target_markets", []) if isinstance(item, dict) and item.get("id")
    }
    raw_target_markets = raw.get("target_markets")
    target_markets = (
        [
            _normalize_target_market(
                item,
                target_market_defaults.get(
                    DEFAULT_MARKET_ID_BY_CODE.get(
                        str((item.get("market") if isinstance(item, dict) else item) or "").strip().upper(),
                        str((item.get("id") if isinstance(item, dict) else item) or "").strip(),
                    )
                ),
            )
            for item in raw_target_markets
        ]
        if isinstance(raw_target_markets, list)
        else [_normalize_target_market(item) for item in defaults.get("target_markets", [])]
    )
    target_markets = [item for item in target_markets if item.get("id") and item.get("platform") and item.get("site")]
    method_ids = {str(item.get("id") or "").strip() for item in search_providers}
    for target in target_markets:
        search_methods = target.get("search_methods") if isinstance(target.get("search_methods"), list) else []
        target["search_methods"] = [
            item for item in search_methods
            if str(item.get("method_id") or item.get("methodId") or "").strip() in method_ids
        ]
    return {
        "search_defaults": search_defaults,
        "provider_runtime": provider_runtime,
        "search_providers": search_providers,
        "target_markets": target_markets,
        "source_registry": source_registry,
    }


__all__ = [
    "default_product_research_config",
    "normalize_product_research_config",
]
