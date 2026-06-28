"""Product research workflow helpers.

The first backend API version keeps external data collection behind configured
providers. Built-in providers are seeded/manual adapters so the API contract,
task persistence, cache, source status, and scoring can ship before live API
credentials are configured.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from erp_web.product_research_config import normalize_product_research_config
from erp_web.schemas.product_research import (
    NormalizedDemandSignal,
    ProductResearchCandidate,
    ProductResearchConfig,
    ProductResearchDataSource,
    ProductResearchSourceStatus,
    ProductResearchTask,
)
from services import ai_gateway


SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "token",
}
SOURCE_DATA_TYPES = {
    "google_trends": "keyword_trend",
    "etsy": "marketplace_products",
    "ebay": "marketplace_products",
    "tiktok": "content_trend",
    "pinterest": "content_trend",
}
MARKET_ALIASES = {
    "GB": {"GB", "UK"},
    "UK": {"GB", "UK"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def _safe_name(value: Any, fallback: str = "item") -> str:
    text = str(value or "").strip().lower()
    safe = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    return safe or fallback


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


class _TemplateContext(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _render_template_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format_map(_TemplateContext(context))
        except Exception:
            return value
    if isinstance(value, dict):
        return {key: _render_template_value(nested, context) for key, nested in value.items()}
    if isinstance(value, list):
        return [_render_template_value(item, context) for item in value]
    return value


def _path_value(value: Any, path: str, default: Any = None) -> Any:
    if not path:
        return value
    current = value
    for part in path.replace("[", ".").replace("]", "").split("."):
        key = part.strip()
        if not key:
            continue
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return default
        elif isinstance(current, dict):
            if key not in current:
                return default
            current = current[key]
        else:
            return default
    return current


def _market_list(value: Any) -> list[str]:
    return [item.upper() for item in _string_list(value)]


def _unique_strings(values: list[str]) -> list[str]:
    return [item for item in dict.fromkeys(values) if item]


def _market_match_values(market: str) -> set[str]:
    market_code = str(market or "").strip().upper()
    return MARKET_ALIASES.get(market_code, {market_code})


def _int_value(value: Any, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(min_value, number)
    if max_value is not None:
        number = min(max_value, number)
    return number


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _cache_dir(app_dir: Path | str) -> Path:
    path = Path(app_dir) / "data" / "cache" / "product_research"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_dir(app_dir: Path | str) -> Path:
    path = _cache_dir(app_dir) / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _source_cache_dir(app_dir: Path | str) -> Path:
    path = _cache_dir(app_dir) / "source_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_path(app_dir: Path | str, task_id: str) -> Path:
    return _task_dir(app_dir) / f"{_safe_name(task_id, 'task')}.json"


def _cache_path(app_dir: Path | str, key: str) -> Path:
    return _source_cache_dir(app_dir) / f"{_safe_name(key, 'cache')}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _mask_config_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {nested_key: _mask_config_value(nested_key, nested_value) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [_mask_config_value(key, item) for item in value]
    if key.lower() in SENSITIVE_CONFIG_KEYS:
        return _mask_secret(value)
    return value


def public_product_research_config(config: dict[str, Any]) -> ProductResearchConfig:
    normalized = normalize_product_research_config(config)
    public_config = json.loads(json.dumps(normalized, ensure_ascii=False))
    for source in (public_config.get("source_registry") or []) + (public_config.get("search_providers") or []):
        if isinstance(source, dict) and isinstance(source.get("config_json"), dict):
            source["config_json"] = {
                key: _mask_config_value(key, value)
                for key, value in source["config_json"].items()
            }
    return public_config


def _target_market_rows(config: dict[str, Any], target_markets: list[str]) -> list[dict[str, Any]]:
    configured = config.get("target_markets") if isinstance(config.get("target_markets"), list) else []
    target_sets = [_market_match_values(market) for market in target_markets]
    rows: list[dict[str, Any]] = []
    for row in configured:
        if not isinstance(row, dict) or not row.get("enabled", True):
            continue
        row_market = str(row.get("market") or "").strip().upper()
        if any(row_market in target_set for target_set in target_sets):
            rows.append(row)
    return rows


def _reference_markets_for_targets(config: dict[str, Any], target_markets: list[str]) -> list[str]:
    references: list[str] = []
    for row in _target_market_rows(config, target_markets):
        references.extend(_market_list(row.get("reference_markets")))
    return _unique_strings([item for item in references if item not in target_markets])


def _provider_ids_for_targets(config: dict[str, Any], target_markets: list[str]) -> list[str]:
    provider_ids: list[str] = []
    for row in _target_market_rows(config, target_markets):
        provider_ids.extend(_string_list(row.get("provider_ids")))
    return _unique_strings(provider_ids)


def normalize_search_request(body: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_product_research_config(config)
    defaults = cfg["search_defaults"]
    raw_markets = body.get("markets") if isinstance(body.get("markets"), dict) else {}
    raw_filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
    raw_sources = body.get("sources") if isinstance(body.get("sources"), dict) else {}
    raw_options = body.get("result_options") if isinstance(body.get("result_options"), dict) else {}

    search_mode = str(body.get("search_mode") or defaults["search_mode"]).strip()
    if search_mode not in {"target_only", "target_plus_reference", "global_scan"}:
        raise ValueError("Unsupported search_mode")

    target_markets = _market_list(raw_markets.get("target_markets")) or _market_list(defaults.get("target_markets"))
    if not target_markets:
        raise ValueError("markets.target_markets is required")

    reference_markets = _market_list(raw_markets.get("reference_markets"))
    if search_mode == "target_plus_reference" and not reference_markets:
        reference_markets.extend(_reference_markets_for_targets(cfg, target_markets))
        reference_map = cfg.get("reference_market_map") if isinstance(cfg.get("reference_market_map"), dict) else {}
        for market in target_markets:
            reference_markets.extend(_market_list(reference_map.get(market)))
        reference_markets = [item for item in dict.fromkeys(reference_markets) if item not in target_markets]

    if search_mode == "target_only":
        reference_markets = []

    demand_sources = _string_list(raw_sources.get("demand_sources"))
    if not demand_sources:
        demand_sources = _provider_ids_for_targets(cfg, target_markets)
    if not demand_sources:
        demand_sources = _string_list(defaults.get("demand_sources"))
    if not demand_sources:
        raise ValueError("sources.demand_sources is required")

    max_limit = _int_value(defaults.get("max_limit"), 100, 1, 500)
    limit = _int_value(raw_options.get("limit"), _int_value(defaults.get("limit"), 50, 1, max_limit), 1, max_limit)

    keywords = _string_list(body.get("keywords") or body.get("seed_keywords"))
    if "include_china_element_types" in raw_filters:
        include_types = _string_list(raw_filters.get("include_china_element_types"))
    elif keywords:
        include_types = []
    else:
        include_types = _string_list(defaults.get("include_china_element_types"))
    if "upgrade_types" in raw_filters:
        upgrade_types = _string_list(raw_filters.get("upgrade_types"))
    else:
        upgrade_types = _string_list(defaults.get("upgrade_types"))
    exclude_risks = _string_list(raw_filters.get("exclude_risks")) or _string_list(defaults.get("exclude_risks"))
    if not include_types and not keywords:
        raise ValueError("filters.include_china_element_types or keywords is required")

    return {
        "search_mode": search_mode,
        "markets": {
            "target_markets": target_markets,
            "reference_markets": reference_markets,
        },
        "keywords": keywords,
        "product_intent": body.get("product_intent") if isinstance(body.get("product_intent"), dict) else {},
        "filters": {
            "include_china_element_types": include_types,
            "upgrade_types": upgrade_types,
            "exclude_risks": exclude_risks,
        },
        "sources": {"demand_sources": _unique_strings(demand_sources)},
        "result_options": {
            "limit": limit,
            "sort_by": str(raw_options.get("sort_by") or defaults.get("sort_by") or "opportunity_score").strip(),
            "time_range_days": _int_value(raw_options.get("time_range_days"), _int_value(defaults.get("time_range_days"), 90, 1, 3650), 1, 3650),
        },
    }


def expand_keywords(request: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    cfg = normalize_product_research_config(config)
    catalog = cfg.get("china_element_catalog") if isinstance(cfg.get("china_element_catalog"), dict) else {}
    expanded: list[dict[str, str]] = []
    for element_type in request["filters"]["include_china_element_types"]:
        item = catalog.get(element_type) if isinstance(catalog.get(element_type), dict) else {}
        keywords = _string_list(item.get("keywords")) or [element_type.replace("_", " ")]
        for keyword in keywords:
            expanded.append(
                {
                    "keyword": keyword,
                    "china_element_type": element_type,
                    "product_type": str(item.get("product_type") or "cultural_product"),
                }
            )
    for keyword in request.get("keywords", []):
        expanded.append(
            {
                "keyword": keyword,
                "china_element_type": "custom_keyword",
                "product_type": "research_keyword",
            }
        )
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in expanded:
        key = (item["keyword"].lower(), item["china_element_type"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _source_supports(source: ProductResearchDataSource, market: str, language: str, data_type: str) -> bool:
    markets = [item.upper() for item in source.get("supported_markets", [])]
    languages = [item.lower() for item in source.get("supported_languages", [])]
    data_types = [item.lower() for item in source.get("supported_data_types", [])]
    market_values = _market_match_values(market)
    return (
        (not markets or "*" in markets or any(item in markets for item in market_values))
        and (not languages or "*" in languages or language.lower() in languages)
        and (not data_types or data_type.lower() in data_types)
    )


def _source_has_auth(source: ProductResearchDataSource) -> bool:
    if not source.get("auth_required"):
        return True
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    request_config = config_json.get("request") if isinstance(config_json.get("request"), dict) else {}
    return any(
        str(config.get(key) or "").strip()
        for config in (config_json, request_config)
        for key in ("api_key", "access_token", "bearer_token", "client_id", "client_secret")
    )


def _source_identity_values(source: ProductResearchDataSource) -> set[str]:
    return {
        str(source.get("id") or "").strip().lower(),
        str(source.get("platform") or "").strip().lower(),
        str(source.get("name") or "").strip().lower(),
    } - {""}


def _source_matches_request(source: ProductResearchDataSource, requested_source: str) -> bool:
    requested = str(requested_source or "").strip().lower()
    return requested in _source_identity_values(source)


def _source_data_type_for_request(source: ProductResearchDataSource, requested_source: str) -> str:
    requested = str(requested_source or "").strip().lower()
    platform = str(source.get("platform") or source.get("id") or "").strip().lower()
    data_types = [str(item or "").strip().lower() for item in source.get("supported_data_types", []) if str(item or "").strip()]
    return SOURCE_DATA_TYPES.get(requested) or SOURCE_DATA_TYPES.get(platform) or (data_types[0] if data_types else "marketplace_products")


def resolve_source_request(
    requested_source: str,
    market: str,
    language: str,
    config: dict[str, Any],
) -> tuple[ProductResearchDataSource, str] | None:
    cfg = normalize_product_research_config(config)
    sources = cfg.get("source_registry") if isinstance(cfg.get("source_registry"), list) else []
    candidates: list[tuple[ProductResearchDataSource, str]] = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("enabled") or not _source_matches_request(source, requested_source):
            continue
        data_type = _source_data_type_for_request(source, requested_source)
        if _source_supports(source, market, language, data_type):
            candidates.append((source, data_type))
    candidates.sort(key=lambda item: int(item[0].get("priority") or 100))
    if not candidates:
        return None
    for source, data_type in candidates:
        if _source_has_auth(source):
            return source, data_type
    return candidates[0]


def resolve_source(
    platform: str,
    market: str,
    language: str,
    data_type: str,
    config: dict[str, Any],
) -> ProductResearchDataSource | None:
    cfg = normalize_product_research_config(config)
    sources = cfg.get("source_registry") if isinstance(cfg.get("source_registry"), list) else []
    candidates = [
        source
        for source in sources
        if isinstance(source, dict)
        and source.get("enabled")
        and _source_matches_request(source, platform)
        and _source_supports(source, market, language, data_type)
    ]
    candidates.sort(key=lambda item: int(item.get("priority") or 100))
    if not candidates:
        return None
    for source in candidates:
        if _source_has_auth(source):
            return source
    return candidates[0]


def _score_seed(*parts: Any, low: int = 50, high: int = 95) -> int:
    span = max(1, high - low)
    digest = _stable_digest(parts, 8)
    return low + (int(digest, 16) % (span + 1))


def _signal_title(source: ProductResearchDataSource, keyword: str) -> str:
    platform = str(source.get("platform") or source.get("id") or "source").replace("_", " ").title()
    return f"{keyword} opportunity on {platform}"


def _seeded_signal(
    source: ProductResearchDataSource,
    market: str,
    language: str,
    keyword_item: dict[str, str],
    data_type: str,
) -> NormalizedDemandSignal:
    platform = str(source.get("platform") or source.get("id") or "").strip().lower()
    keyword = keyword_item["keyword"]
    base = _score_seed(platform, market, keyword)
    metrics: dict[str, Any] = {}
    price: dict[str, Any] | None = None
    title = ""
    if data_type == "keyword_trend":
        metrics["search_interest"] = base
    elif data_type == "content_trend":
        metrics["content_heat"] = base
        metrics["engagement_count"] = _score_seed(platform, market, keyword, "engagement", low=600, high=9000)
        title = _signal_title(source, keyword)
    else:
        metrics["review_count"] = _score_seed(platform, market, keyword, "reviews", low=12, high=380)
        metrics["rating"] = round(_score_seed(platform, market, keyword, "rating", low=40, high=50) / 10, 1)
        price = {
            "amount": round(_score_seed(platform, market, keyword, "price", low=1299, high=6999) / 100, 2),
            "currency": "USD" if market in {"US", "CA", "AU"} else "EUR",
        }
        title = _signal_title(source, keyword)
    signal: NormalizedDemandSignal = {
        "source": platform,
        "source_id": str(source.get("id") or platform),
        "source_type": source.get("source_type", "manual_import"),
        "market": market,
        "keyword": keyword,
        "china_element_type": keyword_item.get("china_element_type", ""),
        "data_type": data_type,
        "metrics": metrics,
        "captured_at": _utc_now(),
    }
    if language:
        signal["language"] = language
    if title:
        signal["title"] = title
    if price:
        signal["price"] = price
    return signal


def _manual_import_signals(
    source: ProductResearchDataSource,
    market: str,
    keyword_items: list[dict[str, str]],
    data_type: str,
) -> list[NormalizedDemandSignal]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    raw_items = config_json.get("items") if isinstance(config_json.get("items"), list) else []
    allowed_keywords = {item["keyword"].lower(): item for item in keyword_items}
    signals: list[NormalizedDemandSignal] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        keyword = str(raw.get("keyword") or "").strip()
        if not keyword or keyword.lower() not in allowed_keywords:
            continue
        if str(raw.get("market") or market).strip().upper() != market:
            continue
        signal = dict(raw)
        signal.setdefault("source", str(source.get("platform") or source.get("id") or "manual_import"))
        signal.setdefault("source_id", str(source.get("id") or signal["source"]))
        signal.setdefault("source_type", source.get("source_type", "manual_import"))
        signal.setdefault("market", market)
        signal.setdefault("data_type", data_type)
        signal.setdefault("china_element_type", allowed_keywords[keyword.lower()].get("china_element_type", ""))
        signal.setdefault("captured_at", _utc_now())
        signals.append(signal)  # type: ignore[arg-type]
    return signals


def _configured_api_request_json(
    source: ProductResearchDataSource,
    market: str,
    language: str,
    keyword_item: dict[str, str],
    data_type: str,
    timeout_seconds: int,
) -> Any:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    request_config = config_json.get("request") if isinstance(config_json.get("request"), dict) else {}
    url = str(request_config.get("url") or config_json.get("url") or "").strip()
    if not url:
        raise ValueError("Configured API source requires config_json.request.url.")
    context = {
        "market": market,
        "language": language,
        "keyword": keyword_item.get("keyword", ""),
        "china_element_type": keyword_item.get("china_element_type", ""),
        "product_type": keyword_item.get("product_type", ""),
        "data_type": data_type,
        "source_id": source.get("id") or "",
        "platform": source.get("platform") or "",
    }
    method = str(request_config.get("method") or "GET").strip().upper()
    rendered_url = str(_render_template_value(url, context)).strip()
    headers = _render_template_value(request_config.get("headers") if isinstance(request_config.get("headers"), dict) else {}, context)
    headers = headers if isinstance(headers, dict) else {}
    query = _render_template_value(request_config.get("query") if isinstance(request_config.get("query"), dict) else {}, context)
    query = query if isinstance(query, dict) else {}
    auth_type = str(request_config.get("auth_type") or "none").strip()
    api_key = str(request_config.get("api_key") or config_json.get("api_key") or "").strip()
    bearer_token = str(request_config.get("bearer_token") or config_json.get("bearer_token") or config_json.get("access_token") or "").strip()
    if auth_type == "api_key_header" and api_key:
        headers[str(request_config.get("api_key_header") or "x-api-key").strip() or "x-api-key"] = api_key
    elif auth_type == "bearer" and bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    data: bytes | None = None
    if method != "GET":
        body = _render_template_value(request_config.get("body") if request_config.get("body") is not None else {}, context)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    if query:
        separator = "&" if urllib.parse.urlparse(rendered_url).query else "?"
        rendered_url = f"{rendered_url}{separator}{urllib.parse.urlencode(query, doseq=True)}"
    req = urllib.request.Request(rendered_url, data=data, headers={str(key): str(value) for key, value in headers.items()}, method=method)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read()
    text = raw.decode("utf-8")
    return json.loads(text) if text else {}


def _number_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _configured_api_signals(
    source: ProductResearchDataSource,
    market: str,
    language: str,
    keyword_items: list[dict[str, str]],
    data_type: str,
    timeout_seconds: int,
) -> list[NormalizedDemandSignal]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    response_config = config_json.get("response") if isinstance(config_json.get("response"), dict) else {}
    items_path = str(response_config.get("items_path") or "data.items").strip()
    signals: list[NormalizedDemandSignal] = []
    max_items = _int_value(response_config.get("max_items_per_keyword"), 20, 1, 100)
    for keyword_item in keyword_items:
        payload = _configured_api_request_json(source, market, language, keyword_item, data_type, timeout_seconds)
        items = _path_value(payload, items_path, [])
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items[:max_items]):
            if not isinstance(item, dict):
                continue
            keyword = str(_path_value(item, str(response_config.get("keyword_path") or "keyword"), "") or keyword_item["keyword"]).strip()
            title = str(_path_value(item, str(response_config.get("title_path") or "title"), "") or keyword).strip()
            price_amount = _number_value(_path_value(item, str(response_config.get("price_path") or "price.amount"), None), 0.0)
            price_currency = str(_path_value(item, str(response_config.get("currency_path") or "price.currency"), "") or "").strip()
            metrics = {
                "search_interest": _number_value(_path_value(item, "search_interest", None), max(35.0, 95.0 - index * 3)),
                "review_count": int(_number_value(_path_value(item, "review_count", None), 0)),
                "rating": _number_value(_path_value(item, "rating", None), 0.0),
                "content_heat": _number_value(_path_value(item, "content_heat", None), 0.0),
            }
            metrics = {key: value for key, value in metrics.items() if value}
            signal: NormalizedDemandSignal = {
                "source": str(source.get("platform") or source.get("id") or "configured_api"),
                "source_id": str(source.get("id") or source.get("platform") or "configured_api"),
                "source_type": source.get("source_type", "api"),
                "market": market,
                "language": language,
                "keyword": keyword,
                "china_element_type": keyword_item.get("china_element_type", ""),
                "data_type": data_type,
                "title": title,
                "metrics": metrics,
                "captured_at": _utc_now(),
            }
            product_url = str(_path_value(item, str(response_config.get("url_path") or "url"), "") or "").strip()
            image_url = str(_path_value(item, str(response_config.get("image_path") or "image_url"), "") or "").strip()
            if product_url:
                signal["product_url"] = product_url
            if image_url:
                signal["image_url"] = image_url
            if price_amount:
                signal["price"] = {"amount": price_amount, "currency": price_currency or ("USD" if market in {"US", "CA", "AU"} else "EUR")}
            signals.append(signal)
    return signals


def _ai_web_search_prompt(market: str, language: str, keyword_items: list[dict[str, str]], max_items: int) -> str:
    keywords = [item["keyword"] for item in keyword_items]
    return f"""Use your web search capability to research ecommerce demand signals.

Return only valid JSON in this exact shape:
{{
  "items": [
    {{
      "keyword": "one of the requested keywords",
      "market": "{market}",
      "title": "short evidence title",
      "source_name": "site or search result name",
      "source_url": "https://...",
      "product_url": "https://...",
      "price": {{"amount": 19.99, "currency": "USD"}},
      "metrics": {{"search_interest": 72, "review_count": 120, "rating": 4.6, "content_heat": 80}},
      "reason": "brief reason this is a market signal",
      "confidence": 0.76
    }}
  ]
}}

Rules:
- Target market: {market}.
- Preferred language: {language or "en"}.
- Requested keywords: {json.dumps(keywords, ensure_ascii=False)}.
- Return at most {max_items} items total.
- Every item must use a keyword from the requested keywords.
- source_url is required and must be a real http(s) URL you found through web search.
- Do not invent URLs, prices, reviews, ratings, or source names.
- If web search is unavailable or you cannot verify source URLs, return {{"items":[]}}.
"""


def _keyword_item_for_ai_result(raw_keyword: Any, keyword_items: list[dict[str, str]]) -> dict[str, str] | None:
    keyword = str(raw_keyword or "").strip().lower()
    if not keyword and len(keyword_items) == 1:
        return keyword_items[0]
    exact = {item["keyword"].lower(): item for item in keyword_items}
    if keyword in exact:
        return exact[keyword]
    if len(keyword_items) == 1:
        return keyword_items[0]
    return None


def _url_value(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _ai_web_search_signals_from_items(
    items: Any,
    market: str,
    language: str,
    keyword_items: list[dict[str, str]],
    source: ProductResearchDataSource | None = None,
) -> tuple[list[NormalizedDemandSignal], int]:
    source = source or {
        "id": "ai_web_search",
        "name": "AI Web Search",
        "source_type": "ai_search",
        "platform": "ai_model",
    }
    source_id = str(source.get("id") or "ai_web_search")
    source_name = str(source.get("name") or source.get("platform") or source_id)
    source_type = str(source.get("source_type") or "ai_search")
    if isinstance(items, dict):
        raw_items = [items]
    elif isinstance(items, list):
        raw_items = items
    else:
        raw_items = []
    signals: list[NormalizedDemandSignal] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        keyword_item = _keyword_item_for_ai_result(item.get("keyword"), keyword_items)
        if not keyword_item:
            continue
        source_url = _url_value(item.get("source_url") or item.get("url") or item.get("link"))
        product_url = _url_value(item.get("product_url") or item.get("productUrl") or source_url)
        if not source_url and not product_url:
            continue
        metrics_raw = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        confidence = _number_value(item.get("confidence"), 0.0)
        metrics = {
            "search_interest": _number_value(metrics_raw.get("search_interest") or item.get("search_interest"), 0.0),
            "review_count": int(_number_value(metrics_raw.get("review_count") or item.get("review_count"), 0.0)),
            "rating": _number_value(metrics_raw.get("rating") or item.get("rating"), 0.0),
            "content_heat": _number_value(metrics_raw.get("content_heat") or item.get("content_heat"), 0.0),
        }
        if not any(metrics.values()) and confidence:
            metrics["content_heat"] = min(100.0, max(10.0, confidence * 100))
        if not any(metrics.values()):
            metrics["content_heat"] = max(35.0, 78.0 - index * 4)
        metrics = {key: value for key, value in metrics.items() if value}
        price_raw = item.get("price") if isinstance(item.get("price"), dict) else {}
        price_amount = _number_value(price_raw.get("amount") or item.get("price_amount"), 0.0)
        price_currency = str(price_raw.get("currency") or item.get("currency") or "").strip()
        title = str(item.get("title") or item.get("reason") or keyword_item["keyword"]).strip()
        signal: NormalizedDemandSignal = {
            "source": source_name,
            "source_id": source_id,
            "source_type": source_type,
            "market": market,
            "language": language,
            "keyword": keyword_item["keyword"],
            "china_element_type": keyword_item.get("china_element_type", ""),
            "data_type": "ai_web_search",
            "title": title,
            "product_url": product_url or source_url,
            "metrics": metrics,
            "captured_at": _utc_now(),
            "source_url": source_url,
            "source_name": str(item.get("source_name") or item.get("source") or "").strip(),
            "ai_reason": str(item.get("reason") or "").strip(),
        }
        if price_amount:
            signal["price"] = {"amount": price_amount, "currency": price_currency or ("USD" if market in {"US", "CA", "AU"} else "EUR")}
        signals.append(signal)
    return signals, len(raw_items)


def _ai_web_search_signals(
    source: ProductResearchDataSource,
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    market: str,
    language: str,
    keyword_items: list[dict[str, str]],
    default_timeout_seconds: int,
) -> list[NormalizedDemandSignal]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    max_items = _int_value(config_json.get("max_items"), 12, 1, 50)
    timeout_seconds = _int_value(config_json.get("timeout_seconds"), default_timeout_seconds, 1, 180)
    model_id = str(config_json.get("ai_model_id") or config_json.get("model_id") or "").strip()
    prompt = _ai_web_search_prompt(market, language, keyword_items, max_items)
    parsed = ai_gateway.chat_json(
        app_dir,
        app_config,
        "research.web_search",
        [
            {
                "role": "system",
                "content": "You are a source-backed ecommerce market research assistant. Return only JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        model_id=model_id,
        temperature=0.2,
        max_tokens=1800,
        timeout_seconds=timeout_seconds,
    )
    source_signals, parsed_count = _ai_web_search_signals_from_items(parsed.get("items"), market, language, keyword_items, source)
    require_source_url = _bool_value(config_json.get("require_source_url"), True)
    if require_source_url and parsed_count and not source_signals:
        raise ValueError("AI returned items without valid source URLs.")
    return source_signals


def _provider_config_completion_prompt(provider: ProductResearchDataSource) -> str:
    return [
        "你是 ERP 选品调研 API 配置助手。只返回 JSON，不要解释。",
        "目标：根据用户已填写的搜索手段信息，补全不易理解的技术字段。",
        "不要编造 API Key、Token、真实私密参数。不要覆盖用户已填写的 URL、Key、Token。",
        "JSON schema:",
        "{",
        '  "supported_markets": ["US"],',
        '  "supported_languages": ["en"],',
        '  "supported_data_types": ["marketplace_products"],',
        '  "request": {',
        '    "method": "GET",',
        '    "auth_type": "api_key_header",',
        '    "api_key_header": "x-api-key",',
        '    "headers": {},',
        '    "query": {"market": "{market}", "q": "{keyword}"},',
        '    "body": {}',
        "  },",
        '  "response": {',
        '    "items_path": "data.items",',
        '    "title_path": "title",',
        '    "keyword_path": "keyword",',
        '    "price_path": "price.amount",',
        '    "currency_path": "price.currency",',
        '    "url_path": "url",',
        '    "image_path": "image_url"',
        "  },",
        '  "test": {"market": "US", "keyword": "mahjong gift"}',
        "}",
        "",
        f"用户已填信息：{json.dumps(provider, ensure_ascii=False, indent=2)}",
    ].join("\n")


def complete_provider_config_with_ai(
    provider: ProductResearchDataSource,
    app_dir: Path | str,
    app_config: dict[str, Any] | None = None,
    model_id: str = "",
) -> dict[str, Any]:
    if not isinstance(provider, dict) or not str(provider.get("id") or provider.get("platform") or "").strip():
        raise ValueError("provider is required")
    prompt = _provider_config_completion_prompt(provider)
    return ai_gateway.chat_json(
        app_dir,
        app_config,
        "research.provider_config_complete",
        [
            {"role": "system", "content": "You return strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        model_id=model_id,
        temperature=0.1,
        max_tokens=1800,
    )


def test_search_provider_connection(
    body: dict[str, Any],
    config: dict[str, Any],
    app_dir: Path | str = ".",
    app_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = body.get("provider") if isinstance(body.get("provider"), dict) else {}
    if not provider:
        raise ValueError("provider is required")
    current_config = normalize_product_research_config(config)
    runtime = current_config["provider_runtime"]
    temp_config = normalize_product_research_config(
        {
            "provider_runtime": runtime,
            "search_providers": [provider],
            "target_markets": [
                {
                    "market": (body.get("market") or "US"),
                    "language": (body.get("language") or "en"),
                    "provider_ids": [provider.get("id") or provider.get("platform") or "provider_under_test"],
                }
            ],
        }
    )
    source = (temp_config.get("source_registry") or [{}])[0]
    if not isinstance(source, dict) or not source.get("id"):
        raise ValueError("provider.id is required")
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    market = str(options.get("market") or body.get("market") or (source.get("supported_markets") or ["US"])[0] or "US").strip().upper()
    language = str(options.get("language") or body.get("language") or (source.get("supported_languages") or ["en"])[0] or "en").strip().lower()
    keyword = str(options.get("keyword") or body.get("keyword") or "mahjong gift").strip()
    data_type = str(options.get("data_type") or _source_data_type_for_request(source, str(source.get("id") or ""))).strip()
    timeout_seconds = _int_value(options.get("timeout_seconds"), int(runtime.get("source_timeout_seconds") or 12), 1, 60)
    strategy = str((source.get("config_json") or {}).get("provider_strategy") or source.get("provider_strategy") or "configured_api").strip()
    started = time.time()
    if source.get("auth_required") and not _source_has_auth(source):
        return {
            "ok": False,
            "status": "configuration_required",
            "source_id": source.get("id"),
            "provider_strategy": strategy,
            "market": market,
            "keyword": keyword,
            "items_found": 0,
            "duration_ms": int((time.time() - started) * 1000),
            "error": "Source requires credentials in config_json.request or config_json.",
        }
    try:
        keyword_item = {
            "keyword": keyword,
            "china_element_type": str(options.get("china_element_type") or "api_test"),
            "product_type": str(options.get("product_type") or "api_test"),
        }
        if strategy == "configured_api":
            signals = _configured_api_signals(source, market, language, [keyword_item], data_type, timeout_seconds)
        elif strategy == "manual_import":
            signals = _manual_import_signals(source, market, [keyword_item], data_type)
        elif strategy == "seeded_mock":
            signals = [_seeded_signal(source, market, language, keyword_item, data_type)]
        elif strategy == "ai_web_search":
            signals = _ai_web_search_signals(source, app_dir, app_config, market, language, [keyword_item], timeout_seconds)
        else:
            raise ValueError(f"Provider strategy '{strategy}' is not supported by test runtime.")
        sample = signals[0] if signals else {}
        return {
            "ok": True,
            "status": "success" if signals else "empty",
            "source_id": source.get("id"),
            "provider_strategy": strategy,
            "market": market,
            "keyword": keyword,
            "items_found": len(signals),
            "duration_ms": int((time.time() - started) * 1000),
            "sample": sample,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "source_id": source.get("id"),
            "provider_strategy": strategy,
            "market": market,
            "keyword": keyword,
            "items_found": 0,
            "duration_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def _load_source_cache(app_dir: Path | str, cache_key: str, ttl_seconds: int) -> list[NormalizedDemandSignal] | None:
    if ttl_seconds <= 0:
        return None
    path = _cache_path(app_dir, cache_key)
    payload = _read_json(path, {})
    if not isinstance(payload, dict):
        return None
    created_at = float(payload.get("created_at_epoch") or 0)
    if created_at <= 0 or time.time() - created_at > ttl_seconds:
        return None
    signals = payload.get("signals")
    return signals if isinstance(signals, list) else None


def _save_source_cache(app_dir: Path | str, cache_key: str, signals: list[NormalizedDemandSignal]) -> None:
    _write_json(
        _cache_path(app_dir, cache_key),
        {
            "created_at_epoch": time.time(),
            "signals": signals,
        },
    )


def collect_signals(
    app_dir: Path | str,
    request: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> tuple[list[NormalizedDemandSignal], list[ProductResearchSourceStatus]]:
    cfg = normalize_product_research_config(config)
    runtime = cfg["provider_runtime"]
    market_languages = cfg.get("market_languages") if isinstance(cfg.get("market_languages"), dict) else {}
    keyword_items = expand_keywords(request, cfg)
    keyword_limit = int(runtime.get("max_keywords_per_source") or 12)
    keyword_items = keyword_items[:keyword_limit]
    markets = request["markets"]["target_markets"] + request["markets"]["reference_markets"]
    signals: list[NormalizedDemandSignal] = []
    statuses: list[ProductResearchSourceStatus] = []
    ttl_seconds = int(runtime.get("cache_ttl_seconds") or 0)

    for market in markets:
        language = str(market_languages.get(market) or "en").strip()
        for requested_source in request["sources"]["demand_sources"]:
            resolved = resolve_source_request(requested_source, market, language, cfg)
            if not resolved:
                statuses.append(
                    {
                        "source": requested_source,
                        "market": market,
                        "status": "skipped",
                        "items_found": 0,
                        "error_message": "No enabled source supports this platform, market, language, and data type.",
                    }
                )
                continue
            source, data_type = resolved
            strategy = str((source.get("config_json") or {}).get("provider_strategy") or "configured_api").strip()
            status: ProductResearchSourceStatus = {
                "source": requested_source,
                "source_id": str(source.get("id") or requested_source),
                "market": market,
                "status": "success",
                "items_found": 0,
                "provider_strategy": strategy,
            }
            if source.get("auth_required") and not _source_has_auth(source):
                status.update(
                    {
                        "status": "configuration_required",
                        "error_message": "Source requires credentials in product_research.search_providers[].config_json.",
                    }
                )
                statuses.append(status)
                continue
            cache_key = _stable_digest([source.get("id"), market, language, data_type, keyword_items, strategy])
            cached = _load_source_cache(app_dir, cache_key, ttl_seconds)
            if cached is not None:
                status["status"] = "cached"
                status["items_found"] = len(cached)
                signals.extend(cached)
                statuses.append(status)
                continue
            try:
                if strategy == "manual_import":
                    source_signals = _manual_import_signals(source, market, keyword_items, data_type)
                elif strategy == "seeded_mock":
                    source_signals = [
                        _seeded_signal(source, market, language, keyword_item, data_type)
                        for keyword_item in keyword_items
                    ]
                elif strategy == "configured_api":
                    request_config = source.get("config_json", {}).get("request") if isinstance(source.get("config_json"), dict) else {}
                    api_url = str(request_config.get("url") if isinstance(request_config, dict) else "").strip()
                    if not api_url:
                        source_signals = []
                        status.update(
                            {
                                "status": "configuration_required",
                                "error_message": "Configured API source requires product_research.search_providers[].config_json.request.url.",
                            }
                        )
                    else:
                        source_signals = _configured_api_signals(
                            source,
                            market,
                            language,
                            keyword_items,
                            data_type,
                            int(runtime.get("source_timeout_seconds") or 12),
                        )
                elif strategy == "ai_web_search":
                    source_signals = _ai_web_search_signals(
                        source,
                        app_dir,
                        app_config,
                        market,
                        language,
                        keyword_items,
                        int(runtime.get("source_timeout_seconds") or 12),
                    )
                else:
                    source_signals = []
                    status.update(
                        {
                            "status": "configuration_required",
                            "error_message": f"Provider strategy '{strategy}' is configured but has no runtime adapter yet.",
                        }
                    )
                status["items_found"] = len(source_signals)
                if source_signals:
                    _save_source_cache(app_dir, cache_key, source_signals)
                signals.extend(source_signals)
            except Exception as exc:
                error_text = str(exc)
                next_status = "configuration_required" if strategy == "ai_web_search" and "configured" in error_text.lower() else "failed"
                status.update({"status": next_status, "error_message": error_text, "items_found": 0})
            statuses.append(status)
    return signals, statuses


def _metric_score(signals: list[NormalizedDemandSignal]) -> float:
    scores: list[float] = []
    for signal in signals:
        metrics = signal.get("metrics") if isinstance(signal.get("metrics"), dict) else {}
        if "search_interest" in metrics:
            scores.append(float(metrics.get("search_interest") or 0))
        if "content_heat" in metrics:
            scores.append(float(metrics.get("content_heat") or 0))
        if "review_count" in metrics:
            scores.append(min(100.0, float(metrics.get("review_count") or 0) / 4))
        if "rating" in metrics:
            scores.append(min(100.0, float(metrics.get("rating") or 0) * 20))
    return max(scores) if scores else 55.0


def _band(score: float) -> str:
    if score >= 82:
        return "high"
    if score >= 65:
        return "medium"
    return "low"


def _candidate_action(score: float) -> str:
    if score >= 90:
        return "priority_manual_research"
    if score >= 80:
        return "candidate_pool"
    if score >= 70:
        return "watch"
    return "drop"


def _catalog_item(config: dict[str, Any], element_type: str) -> dict[str, Any]:
    catalog = config.get("china_element_catalog") if isinstance(config.get("china_element_catalog"), dict) else {}
    item = catalog.get(element_type) if isinstance(catalog.get(element_type), dict) else {}
    return item


def _upgrade_suggestions(config: dict[str, Any], upgrade_types: list[str]) -> list[str]:
    catalog = config.get("upgrade_type_catalog") if isinstance(config.get("upgrade_type_catalog"), dict) else {}
    suggestions: list[str] = []
    for upgrade_type in upgrade_types:
        label = str(catalog.get(upgrade_type) or upgrade_type.replace("_", " ")).strip()
        if label:
            suggestions.append(label)
    return suggestions


def build_candidates(
    request: dict[str, Any],
    signals: list[NormalizedDemandSignal],
    config: dict[str, Any],
) -> list[ProductResearchCandidate]:
    cfg = normalize_product_research_config(config)
    weights = cfg.get("scoring_weights") if isinstance(cfg.get("scoring_weights"), dict) else {}
    target_markets = request["markets"]["target_markets"]
    upgrade_types = request["filters"]["upgrade_types"]
    excluded_risks = request["filters"]["exclude_risks"]
    grouped: dict[tuple[str, str], list[NormalizedDemandSignal]] = {}
    for signal in signals:
        market = str(signal.get("market") or "").upper()
        keyword = str(signal.get("keyword") or "").strip()
        if not keyword:
            continue
        for target_market in target_markets:
            if market == target_market or market in request["markets"]["reference_markets"]:
                grouped.setdefault((target_market, keyword), []).append(signal)

    candidates: list[ProductResearchCandidate] = []
    for (target_market, keyword), evidence in grouped.items():
        target_evidence = [signal for signal in evidence if str(signal.get("market") or "").upper() == target_market]
        if not target_evidence:
            continue
        element_type = str(target_evidence[0].get("china_element_type") or "custom_keyword")
        catalog_item = _catalog_item(cfg, element_type)
        search_interest = _metric_score(target_evidence)
        china_element_fit = 90.0 if element_type != "custom_keyword" else 70.0
        wait_tolerance = 86.0 if catalog_item.get("product_type") in {"festival_decor", "cultural_gift", "home_decor", "fashion_accessory"} else 74.0
        reference_count = len({signal.get("market") for signal in evidence if signal.get("market") != target_market})
        local_scarcity = min(95.0, 68.0 + reference_count * 6 + _score_seed(target_market, keyword, "scarcity", low=0, high=12))
        logistics_fit = 88.0 if "liquid" in excluded_risks and "battery" in excluded_risks else 76.0
        compliance_fit = 90.0 if excluded_risks else 70.0
        raw_scores = {
            "search_interest": search_interest,
            "china_element_fit": china_element_fit,
            "wait_tolerance": wait_tolerance,
            "local_scarcity": local_scarcity,
            "logistics_fit": logistics_fit,
            "compliance_fit": compliance_fit,
        }
        if upgrade_types:
            raw_scores["upgrade_space"] = min(95.0, 65.0 + len(upgrade_types) * 6)
        total_weight = sum(float(weights.get(key, 0) or 0) for key in raw_scores) or 100.0
        score_breakdown = {
            key: round(raw_scores[key] * float(weights.get(key, 0) or 0) / total_weight, 2)
            for key in raw_scores
        }
        opportunity_score = round(sum(score_breakdown.values()), 2)
        related_sources = sorted({str(signal.get("source") or "") for signal in evidence if signal.get("source")})
        purchase_keywords = _string_list(catalog_item.get("purchase_keywords")) or [keyword]
        candidate: ProductResearchCandidate = {
            "candidate_id": f"prc_{_stable_digest([target_market, keyword])}",
            "target_market": target_market,
            "overseas_keyword": keyword,
            "china_element_type": element_type,
            "product_type": str(catalog_item.get("product_type") or "research_keyword"),
            "related_sources": related_sources,
            "chinese_purchase_keywords": purchase_keywords,
            "upgrade_suggestions": _upgrade_suggestions(cfg, upgrade_types),
            "logistics_risks": ["fragile", "heavy", "oversize"],
            "compliance_risks": excluded_risks,
            "china_element_strength": _band(china_element_fit),
            "wait_tolerance": _band(wait_tolerance),
            "local_scarcity": _band(local_scarcity),
            "opportunity_score": opportunity_score,
            "score_breakdown": score_breakdown,
            "recommended_action": _candidate_action(opportunity_score),
            "evidence_signals": evidence[:6],
        }
        candidates.append(candidate)
    return candidates


def create_search_task(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchTask:
    normalized_config = normalize_product_research_config(config)
    request = normalize_search_request(body if isinstance(body, dict) else {}, normalized_config)
    created_at = _utc_now()
    signals, source_status = collect_signals(app_dir, request, normalized_config, app_config)
    candidates = build_candidates(request, signals, normalized_config)
    sort_by = request["result_options"].get("sort_by") or "opportunity_score"
    if sort_by == "opportunity_score":
        candidates.sort(key=lambda item: float(item.get("opportunity_score") or 0), reverse=True)
    limit = int(request["result_options"].get("limit") or 50)
    candidates = candidates[:limit]
    task_id = f"prt_{_stable_digest([created_at, request, [status.get('status') for status in source_status]])}"
    task: ProductResearchTask = {
        "task_id": task_id,
        "status": "completed",
        "search_mode": request["search_mode"],
        "created_at": created_at,
        "completed_at": _utc_now(),
        "request": request,
        "items": candidates,
        "signals": signals,
        "source_status": source_status,
    }
    _write_json(_task_path(app_dir, task_id), task)
    return task


def load_search_task(app_dir: Path | str, task_id: str) -> ProductResearchTask | None:
    if not str(task_id or "").strip():
        return None
    payload = _read_json(_task_path(app_dir, task_id), None)
    return payload if isinstance(payload, dict) else None


def list_search_tasks(app_dir: Path | str, limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_task_dir(app_dir).glob("prt_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "task_id": payload.get("task_id") or path.stem,
                "status": payload.get("status") or "",
                "search_mode": payload.get("search_mode") or "",
                "created_at": payload.get("created_at") or "",
                "completed_at": payload.get("completed_at") or "",
                "items_count": len(payload.get("items") or []),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def build_task_response(task: ProductResearchTask) -> dict[str, Any]:
    return {
        "ok": True,
        "task": {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "search_mode": task.get("search_mode"),
            "created_at": task.get("created_at"),
            "completed_at": task.get("completed_at"),
            "request": task.get("request"),
        },
        "items": task.get("items") or [],
        "candidates": task.get("items") or [],
        "signals": task.get("signals") or [],
        "source_status": task.get("source_status") or [],
    }


__all__ = [
    "build_candidates",
    "build_task_response",
    "collect_signals",
    "complete_provider_config_with_ai",
    "create_search_task",
    "expand_keywords",
    "list_search_tasks",
    "load_search_task",
    "normalize_search_request",
    "public_product_research_config",
    "resolve_source",
    "resolve_source_request",
    "test_search_provider_connection",
]
