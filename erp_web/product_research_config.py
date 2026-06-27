# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any


PRODUCT_RESEARCH_SOURCE_TYPES = {"api", "ai_search", "crawler", "third_party_api", "manual_import"}
PRODUCT_RESEARCH_SEARCH_MODES = {"target_only", "target_plus_reference", "global_scan"}
DEFAULT_MARKET_CURRENCIES = {
    "US": "USD",
    "GB": "GBP",
    "UK": "GBP",
    "CA": "CAD",
    "AU": "AUD",
    "DE": "EUR",
    "FR": "EUR",
    "JP": "JPY",
}


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


def default_product_research_config() -> dict[str, Any]:
    defaults = {
        "search_defaults": {
            "search_mode": "target_plus_reference",
            "target_markets": ["US"],
            "reference_markets": [],
            "demand_sources": ["google_trends", "etsy", "ebay", "tiktok", "pinterest"],
            "limit": 50,
            "max_limit": 100,
            "sort_by": "opportunity_score",
            "time_range_days": 90,
            "include_china_element_types": [
                "chinese_new_year",
                "calligraphy",
                "mahjong",
                "hanfu",
                "oriental_home_decor",
            ],
            "upgrade_types": ["gift_box", "custom_name", "bundle_set", "localized_explanation"],
            "exclude_risks": ["food", "battery", "children_product", "medical_device", "cosmetics", "liquid"],
        },
        "provider_runtime": {
            "source_timeout_seconds": 12,
            "retry_count": 1,
            "cache_ttl_seconds": 21600,
            "max_keywords_per_source": 12,
        },
        "reference_market_map": {
            "US": ["GB", "CA", "AU"],
            "GB": ["US", "CA", "AU"],
            "DE": ["AT", "CH", "FR", "NL"],
            "JP": ["KR", "TW", "HK", "SG"],
            "FR": ["BE", "CH", "DE", "NL"],
        },
        "market_languages": {
            "US": "en",
            "GB": "en",
            "CA": "en",
            "AU": "en",
            "DE": "de",
            "FR": "fr",
            "JP": "ja",
        },
        "china_element_catalog": {
            "chinese_new_year": {
                "label": "Chinese New Year",
                "product_type": "festival_decor",
                "keywords": [
                    "Chinese New Year decorations",
                    "Lunar New Year decor",
                    "red envelope",
                    "Chinese lantern",
                    "zodiac gifts",
                ],
                "purchase_keywords": [
                    "spring festival decoration set",
                    "red envelope",
                    "lantern hanging decor",
                    "zodiac ornament",
                ],
            },
            "calligraphy": {
                "label": "Chinese calligraphy",
                "product_type": "cultural_gift",
                "keywords": [
                    "Chinese calligraphy gift",
                    "Chinese name stamp",
                    "Chinese lucky charm",
                    "feng shui gift",
                ],
                "purchase_keywords": [
                    "calligraphy gift",
                    "custom Chinese name seal",
                    "zodiac gift",
                    "feng shui ornament",
                ],
            },
            "mahjong": {
                "label": "Mahjong",
                "product_type": "game_accessory",
                "keywords": [
                    "mahjong gifts",
                    "mahjong accessories",
                    "mahjong earrings",
                    "mahjong party decor",
                    "mahjong bag",
                ],
                "purchase_keywords": [
                    "mahjong accessories",
                    "mahjong earrings",
                    "mahjong keychain",
                    "mahjong party decoration",
                ],
            },
            "hanfu": {
                "label": "Hanfu accessories",
                "product_type": "fashion_accessory",
                "keywords": [
                    "hanfu accessories",
                    "Chinese hairpin",
                    "Chinese fan",
                    "jade bracelet",
                    "Chinese style earrings",
                ],
                "purchase_keywords": [
                    "hanfu hairpin",
                    "Chinese fan",
                    "jade bracelet",
                    "Chinese style earrings",
                ],
            },
            "oriental_home_decor": {
                "label": "Oriental home decor",
                "product_type": "home_decor",
                "keywords": [
                    "oriental home decor",
                    "Chinese wall art",
                    "Asian aesthetic room decor",
                    "Chinese vase",
                    "tea room decor",
                ],
                "purchase_keywords": [
                    "new Chinese style wall art",
                    "tea room decor",
                    "oriental ornament",
                    "Chinese vase decor",
                ],
            },
        },
        "upgrade_type_catalog": {
            "gift_box": "Gift box packaging",
            "custom_name": "Custom name or monogram",
            "bundle_set": "Bundle set",
            "localized_explanation": "Localized cultural explanation card",
            "material_upgrade": "Material or finish upgrade",
        },
        "scoring_weights": {
            "search_interest": 20,
            "china_element_fit": 15,
            "wait_tolerance": 15,
            "local_scarcity": 15,
            "upgrade_space": 15,
            "logistics_fit": 10,
            "compliance_fit": 10,
        },
        "source_registry": [
            {
                "id": "google_trends_seeded",
                "name": "Google Trends Seeded Provider",
                "source_type": "api",
                "platform": "google_trends",
                "enabled": True,
                "priority": 1,
                "supported_markets": ["US", "GB", "CA", "AU", "JP", "DE", "FR"],
                "supported_languages": ["en", "ja", "de", "fr"],
                "supported_data_types": ["keyword_trend"],
                "auth_required": False,
                "rate_limit_per_minute": 30,
                "compliance_note": "Seeded local provider for MVP. Replace config_json with official or third-party API settings.",
                "config_json": {"provider_strategy": "seeded_mock"},
            },
            {
                "id": "etsy_public_search_seeded",
                "name": "Etsy Public Search Seeded Provider",
                "source_type": "crawler",
                "platform": "etsy",
                "enabled": True,
                "priority": 3,
                "supported_markets": ["US", "GB", "CA", "AU"],
                "supported_languages": ["en"],
                "supported_data_types": ["marketplace_products"],
                "auth_required": False,
                "rate_limit_per_minute": 12,
                "compliance_note": "Public data collection must follow platform terms, robots rules, and rate limits.",
                "config_json": {"provider_strategy": "seeded_mock"},
            },
            {
                "id": "ebay_browse_api",
                "name": "eBay Browse API",
                "source_type": "api",
                "platform": "ebay",
                "enabled": True,
                "priority": 1,
                "supported_markets": ["US", "GB", "DE", "AU"],
                "supported_languages": ["en", "de"],
                "supported_data_types": ["marketplace_products"],
                "auth_required": True,
                "rate_limit_per_minute": 30,
                "compliance_note": "Requires configured eBay API credentials before live collection.",
                "config_json": {"provider_strategy": "configured_api", "auth_config_keys": ["client_id", "client_secret"]},
            },
            {
                "id": "tiktok_content_seeded",
                "name": "TikTok Content Seeded Provider",
                "source_type": "third_party_api",
                "platform": "tiktok",
                "enabled": True,
                "priority": 4,
                "supported_markets": ["US", "GB", "CA", "AU", "JP", "DE", "FR"],
                "supported_languages": ["en", "ja", "de", "fr"],
                "supported_data_types": ["content_trend"],
                "auth_required": False,
                "rate_limit_per_minute": 20,
                "compliance_note": "Use authorized content APIs or approved third-party data providers for live data.",
                "config_json": {"provider_strategy": "seeded_mock"},
            },
            {
                "id": "pinterest_content_seeded",
                "name": "Pinterest Content Seeded Provider",
                "source_type": "third_party_api",
                "platform": "pinterest",
                "enabled": True,
                "priority": 4,
                "supported_markets": ["US", "GB", "CA", "AU", "JP", "DE", "FR"],
                "supported_languages": ["en", "ja", "de", "fr"],
                "supported_data_types": ["content_trend"],
                "auth_required": False,
                "rate_limit_per_minute": 20,
                "compliance_note": "Use authorized content APIs or approved third-party data providers for live data.",
                "config_json": {"provider_strategy": "seeded_mock"},
            },
            {
                "id": "manual_import",
                "name": "Manual Import",
                "source_type": "manual_import",
                "platform": "manual_import",
                "enabled": True,
                "priority": 9,
                "supported_markets": ["*"],
                "supported_languages": ["*"],
                "supported_data_types": ["keyword_trend", "marketplace_products", "content_trend"],
                "auth_required": False,
                "rate_limit_per_minute": None,
                "compliance_note": "Use config_json.items for manually imported normalized signals.",
                "config_json": {"provider_strategy": "manual_import", "items": []},
            },
        ],
    }
    defaults["search_providers"] = [deepcopy(item) for item in defaults["source_registry"]]
    defaults["target_markets"] = [
        {
            "market": "US",
            "name": "United States",
            "enabled": True,
            "language": "en",
            "currency": "USD",
            "reference_markets": ["GB", "CA", "AU"],
            "provider_ids": [
                "google_trends_seeded",
                "etsy_public_search_seeded",
                "ebay_browse_api",
                "tiktok_content_seeded",
                "pinterest_content_seeded",
            ],
        },
        {
            "market": "GB",
            "name": "United Kingdom",
            "enabled": True,
            "language": "en",
            "currency": "GBP",
            "reference_markets": ["US", "CA", "AU"],
            "provider_ids": [
                "google_trends_seeded",
                "etsy_public_search_seeded",
                "ebay_browse_api",
                "tiktok_content_seeded",
                "pinterest_content_seeded",
            ],
        },
        {
            "market": "CA",
            "name": "Canada",
            "enabled": True,
            "language": "en",
            "currency": "CAD",
            "reference_markets": ["US", "GB", "AU"],
            "provider_ids": [
                "google_trends_seeded",
                "etsy_public_search_seeded",
                "tiktok_content_seeded",
                "pinterest_content_seeded",
            ],
        },
        {
            "market": "AU",
            "name": "Australia",
            "enabled": True,
            "language": "en",
            "currency": "AUD",
            "reference_markets": ["US", "GB", "CA"],
            "provider_ids": [
                "google_trends_seeded",
                "etsy_public_search_seeded",
                "ebay_browse_api",
                "tiktok_content_seeded",
                "pinterest_content_seeded",
            ],
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
    platform = str(raw.get("platform") or defaults.get("platform") or source_id or "manual_import").strip().lower()
    if not source_id:
        source_id = platform
    config_json = raw.get("config_json") if isinstance(raw.get("config_json"), dict) else defaults.get("config_json")
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
        "config_json": dict(config_json) if isinstance(config_json, dict) else {},
    }


def _normalize_target_market(
    market: Any,
    fallback: dict[str, Any] | None = None,
    default_provider_ids: list[str] | None = None,
) -> dict[str, Any]:
    raw = market if isinstance(market, dict) else {"market": market}
    defaults = fallback if isinstance(fallback, dict) else {}
    market_code = str(raw.get("market") or raw.get("code") or raw.get("id") or defaults.get("market") or "").strip().upper()
    provider_ids = (
        _string_list(raw.get("provider_ids"))
        or _string_list(raw.get("source_ids"))
        or _string_list(raw.get("demand_sources"))
        or _string_list(defaults.get("provider_ids"))
        or list(default_provider_ids or [])
    )
    language = str(raw.get("language") or defaults.get("language") or "").strip().lower()
    return {
        "market": market_code,
        "name": str(raw.get("name") or defaults.get("name") or market_code).strip(),
        "enabled": _bool_value(raw.get("enabled"), _bool_value(defaults.get("enabled"), True)),
        "language": language or "en",
        "currency": str(
            raw.get("currency")
            or defaults.get("currency")
            or DEFAULT_MARKET_CURRENCIES.get(market_code)
            or "USD"
        ).strip().upper(),
        "reference_markets": _string_list(raw.get("reference_markets", defaults.get("reference_markets", []))),
        "provider_ids": [item for item in dict.fromkeys(provider_ids) if item],
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
        "demand_sources": _string_list(search_defaults.get("demand_sources")) or defaults["search_defaults"]["demand_sources"],
        "limit": _int_value(search_defaults.get("limit"), defaults["search_defaults"]["limit"], 1, max_limit),
        "max_limit": max_limit,
        "sort_by": str(search_defaults.get("sort_by") or defaults["search_defaults"]["sort_by"]).strip(),
        "time_range_days": _int_value(search_defaults.get("time_range_days"), defaults["search_defaults"]["time_range_days"], 1, 3650),
        "include_china_element_types": _string_list(search_defaults.get("include_china_element_types"))
        or defaults["search_defaults"]["include_china_element_types"],
        "upgrade_types": _string_list(search_defaults.get("upgrade_types")) or defaults["search_defaults"]["upgrade_types"],
        "exclude_risks": _string_list(search_defaults.get("exclude_risks")) or defaults["search_defaults"]["exclude_risks"],
    }

    provider_runtime = dict(defaults["provider_runtime"])
    provider_runtime.update({key: value for key, value in provider_runtime_raw.items() if value is not None})
    provider_runtime = {
        "source_timeout_seconds": _int_value(provider_runtime.get("source_timeout_seconds"), defaults["provider_runtime"]["source_timeout_seconds"], 1, 120),
        "retry_count": _int_value(provider_runtime.get("retry_count"), defaults["provider_runtime"]["retry_count"], 0, 5),
        "cache_ttl_seconds": _int_value(provider_runtime.get("cache_ttl_seconds"), defaults["provider_runtime"]["cache_ttl_seconds"], 0, 604800),
        "max_keywords_per_source": _int_value(provider_runtime.get("max_keywords_per_source"), defaults["provider_runtime"]["max_keywords_per_source"], 1, 100),
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
    search_providers = [item for item in search_providers if item.get("id")]
    source_registry = [deepcopy(item) for item in search_providers]
    source_registry = [item for item in source_registry if item.get("id")]

    reference_market_map = dict(
        raw.get("reference_market_map")
        if isinstance(raw.get("reference_market_map"), dict)
        else defaults["reference_market_map"]
    )
    market_languages = dict(
        raw.get("market_languages")
        if isinstance(raw.get("market_languages"), dict)
        else defaults["market_languages"]
    )
    target_market_defaults = {
        item["market"]: item for item in defaults.get("target_markets", []) if isinstance(item, dict) and item.get("market")
    }
    default_provider_ids = [item["id"] for item in search_providers if item.get("id") and item.get("platform") != "manual_import"]
    raw_target_markets = raw.get("target_markets")
    target_markets = (
        [
            _normalize_target_market(
                item,
                target_market_defaults.get(str((item.get("market") if isinstance(item, dict) else item) or "").strip().upper()),
                default_provider_ids,
            )
            for item in raw_target_markets
        ]
        if isinstance(raw_target_markets, list)
        else [_normalize_target_market(item, None, default_provider_ids) for item in defaults.get("target_markets", [])]
    )
    target_markets = [item for item in target_markets if item.get("market")]
    for item in target_markets:
        market = item["market"]
        if not item.get("reference_markets"):
            item["reference_markets"] = _string_list(reference_market_map.get(market))
        if not item.get("language") or item["language"] == "en":
            item["language"] = str(market_languages.get(market) or item.get("language") or "en").strip().lower()
        market_languages.setdefault(market, item["language"])
        reference_market_map.setdefault(market, item.get("reference_markets", []))

    return {
        "search_defaults": search_defaults,
        "provider_runtime": provider_runtime,
        "reference_market_map": reference_market_map,
        "market_languages": market_languages,
        "china_element_catalog": raw.get("china_element_catalog") if isinstance(raw.get("china_element_catalog"), dict) else defaults["china_element_catalog"],
        "upgrade_type_catalog": raw.get("upgrade_type_catalog") if isinstance(raw.get("upgrade_type_catalog"), dict) else defaults["upgrade_type_catalog"],
        "scoring_weights": raw.get("scoring_weights") if isinstance(raw.get("scoring_weights"), dict) else defaults["scoring_weights"],
        "search_providers": search_providers,
        "target_markets": target_markets,
        "source_registry": source_registry,
    }


__all__ = [
    "default_product_research_config",
    "normalize_product_research_config",
]
