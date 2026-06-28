"""Product research hot-product candidate helpers.

Product-research runs return temporary hot-product candidates for selected
target markets and keywords. Target markets describe platform + site only;
candidate lists live separately under market_hot_products keyed by market_id.
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
    HotProductCandidate,
    ProductResearchConfig,
    ProductResearchDataSource,
    ProductResearchRun,
    ProductResearchSourceStatus,
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
MARKET_ALIASES = {
    "US": "amazon-us",
    "GB": "amazon-uk",
    "UK": "amazon-uk",
    "CA": "amazon-ca",
    "AU": "amazon-au",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


def _market_list(value: Any) -> list[str]:
    return _string_list(value)


def _resolve_market_id(config: dict[str, Any], value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    configured = config.get("target_markets") if isinstance(config.get("target_markets"), list) else []
    configured_ids = {
        str(row.get("id") or "").strip()
        for row in configured
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    if raw in configured_ids:
        return raw
    alias = MARKET_ALIASES.get(raw.upper(), raw)
    return alias if alias in configured_ids else raw


def _int_value(value: Any, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(min_value, number)
    if max_value is not None:
        number = min(max_value, number)
    return number


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
    requested = {_resolve_market_id(config, market) for market in target_markets}
    rows: list[dict[str, Any]] = []
    for row in configured:
        if not isinstance(row, dict):
            continue
        row_market = str(row.get("id") or "").strip()
        if row_market in requested:
            rows.append(row)
    return rows


def _target_market_context(config: dict[str, Any], market: str) -> dict[str, Any]:
    rows = _target_market_rows(config, [market])
    return rows[0] if rows else {}


def normalize_search_request(body: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_product_research_config(config)
    defaults = cfg["search_defaults"]
    raw_markets = body.get("markets") if isinstance(body.get("markets"), dict) else {}
    raw_options = body.get("result_options") if isinstance(body.get("result_options"), dict) else {}

    raw_target_markets = (
        raw_markets.get("target_markets")
        or raw_markets.get("targetMarketIds")
        or body.get("target_market_ids")
        or body.get("targetMarketIds")
        or body.get("market_id")
        or body.get("marketId")
    )
    target_markets = _market_list(raw_target_markets) or _market_list(defaults.get("target_markets"))
    target_markets = [_resolve_market_id(cfg, market) for market in target_markets]
    if not target_markets:
        raise ValueError("markets.target_markets is required")
    keywords = _string_list(body.get("keywords") or body.get("seed_keywords"))
    if not keywords:
        raise ValueError("keywords is required")

    max_limit = _int_value(defaults.get("max_limit"), 100, 1, 500)
    limit = _int_value(raw_options.get("limit"), _int_value(defaults.get("limit"), 12, 1, max_limit), 1, max_limit)
    return {
        "search_mode": "target_only",
        "markets": {
            "target_markets": target_markets,
            "reference_markets": [],
        },
        "keywords": keywords,
        "result_options": {
            "limit": limit,
            "sort_by": "rank",
        },
    }


def _keyword_matches_product(product: HotProductCandidate, keywords: list[str]) -> bool:
    product_keyword = str(product.get("keyword") or "").strip().lower()
    title = str(product.get("title") or "").strip().lower()
    for keyword in keywords:
        query = keyword.strip().lower()
        if query and (query == product_keyword or query in title):
            return True
    return False


def _market_hot_products_for_market(
    config: dict[str, Any],
    market_id: str,
    keywords: list[str],
    limit: int,
) -> list[HotProductCandidate]:
    collections = config.get("market_hot_products") if isinstance(config.get("market_hot_products"), list) else []
    collection = next(
        (
            row for row in collections
            if isinstance(row, dict) and str(row.get("market_id") or row.get("marketId") or "").strip() == market_id
        ),
        {},
    )
    raw_items = collection.get("items") if isinstance(collection, dict) and isinstance(collection.get("items"), list) else []
    if not raw_items:
        return []
    typed_items = [item for item in raw_items if isinstance(item, dict)]
    matched = [item for item in typed_items if _keyword_matches_product(item, keywords)]
    selected = matched or typed_items
    return sorted(selected, key=lambda item: int(item.get("rank") or 999999))[:limit]  # type: ignore[return-value]


def build_hot_product_candidates(request: dict[str, Any], config: dict[str, Any]) -> tuple[list[HotProductCandidate], list[ProductResearchSourceStatus]]:
    cfg = normalize_product_research_config(config)
    limit = int(request.get("result_options", {}).get("limit") or 12)
    items: list[HotProductCandidate] = []
    statuses: list[ProductResearchSourceStatus] = []

    for market in request["markets"]["target_markets"]:
        start_count = len(items)
        target = _target_market_context(cfg, market)
        market_items = _market_hot_products_for_market(cfg, market, request["keywords"], limit - len(items)) if target else []
        items.extend(market_items)
        found = len(items) - start_count
        error_message = "" if found else "目标市场还没有候选商品数据"
        if not target:
            error_message = "目标市场不存在"
        statuses.append(
            {
                "source": "market_hot_products",
                "source_id": "market_hot_products",
                "market": market,
                "status": "success" if found else "empty",
                "items_found": found,
                "error_message": error_message,
                "provider_strategy": "market_data",
            }
        )
    return items, statuses


def create_hot_product_run(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchRun:
    normalized_config = normalize_product_research_config(config)
    request = normalize_search_request(body if isinstance(body, dict) else {}, normalized_config)
    created_at = _utc_now()
    items, source_status = build_hot_product_candidates(request, normalized_config)
    return {
        "run_id": f"prr_{_stable_digest([created_at, request, len(items)])}",
        "status": "completed",
        "search_mode": request["search_mode"],
        "created_at": created_at,
        "completed_at": _utc_now(),
        "request": request,
        "items": items,
        "source_status": source_status,
    }


def build_run_response(run: ProductResearchRun) -> dict[str, Any]:
    return {
        "ok": True,
        "run": {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "search_mode": run.get("search_mode"),
            "created_at": run.get("created_at"),
            "completed_at": run.get("completed_at"),
            "request": run.get("request"),
        },
        "items": run.get("items") or [],
        "source_status": run.get("source_status") or [],
    }


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


def _configured_api_request_json(
    source: ProductResearchDataSource,
    market: str,
    language: str,
    keyword: str,
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
        "keyword": keyword,
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


def _configured_api_sample(
    source: ProductResearchDataSource,
    market: str,
    language: str,
    keyword: str,
    data_type: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    response_config = config_json.get("response") if isinstance(config_json.get("response"), dict) else {}
    payload = _configured_api_request_json(source, market, language, keyword, data_type, timeout_seconds)
    items_path = str(response_config.get("items_path") or "data.items").strip()
    items = _path_value(payload, items_path, [])
    if isinstance(items, dict):
        items = [items]
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    title = str(_path_value(first, str(response_config.get("title_path") or "title"), "") or keyword).strip()
    url = str(_path_value(first, str(response_config.get("url_path") or "url"), "") or "").strip()
    return {
        "title": title,
        "source_url": url,
        "keyword": keyword,
        "market": market,
        "data_type": data_type,
    }


def _ai_web_search_prompt(market: str, language: str, keyword: str) -> str:
    return "\n".join(
        [
            "Use web search to find ecommerce hot-product evidence.",
            "Return only valid JSON in this shape:",
            '{"items":[{"title":"product title","source_url":"https://...","image_url":"https://...","price":{"amount":19.99,"currency":"USD"},"rating":4.6,"review_count":120}]}',
            f"Target market: {market}.",
            f"Preferred language: {language or 'en'}.",
            f"Keyword: {keyword}.",
            "Do not invent URLs. Return an empty items array when unavailable.",
        ]
    )


def _ai_web_search_sample(
    source: ProductResearchDataSource,
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    market: str,
    language: str,
    keyword: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    model_id = str(config_json.get("ai_model_id") or config_json.get("model_id") or "").strip()
    parsed = ai_gateway.chat_json(
        app_dir,
        app_config,
        "research.web_search",
        [
            {"role": "system", "content": "You are a source-backed ecommerce market research assistant. Return only JSON."},
            {"role": "user", "content": _ai_web_search_prompt(market, language, keyword)},
        ],
        model_id=model_id,
        temperature=0.2,
        max_tokens=1200,
        timeout_seconds=timeout_seconds,
    )
    items = parsed.get("items")
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    if not first:
        return {}
    return {
        "title": str(first.get("title") or keyword).strip(),
        "source_url": str(first.get("source_url") or first.get("url") or "").strip(),
        "keyword": keyword,
        "market": market,
    }


def _provider_config_completion_prompt(provider: ProductResearchDataSource) -> str:
    return "\n".join(
        [
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
        ]
    )


def complete_provider_config_with_ai(
    provider: ProductResearchDataSource,
    app_dir: Path | str,
    app_config: dict[str, Any] | None = None,
    model_id: str = "",
) -> dict[str, Any]:
    if not isinstance(provider, dict) or not str(provider.get("id") or provider.get("platform") or "").strip():
        raise ValueError("provider is required")
    return ai_gateway.chat_json(
        app_dir,
        app_config,
        "research.provider_config_complete",
        [
            {"role": "system", "content": "You return strict JSON only."},
            {"role": "user", "content": _provider_config_completion_prompt(provider)},
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
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    market = str(options.get("market") or body.get("market") or "US").strip().upper()
    language = str(options.get("language") or body.get("language") or "en").strip().lower()
    keyword = str(options.get("keyword") or body.get("keyword") or "mahjong gift").strip()
    data_type = str(options.get("data_type") or "marketplace_products").strip()
    timeout_seconds = _int_value(options.get("timeout_seconds"), int(runtime.get("source_timeout_seconds") or 12), 1, 60)
    source = provider
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
        if strategy == "configured_api":
            sample = _configured_api_sample(source, market, language, keyword, data_type, timeout_seconds)
        elif strategy == "ai_web_search":
            sample = _ai_web_search_sample(source, app_dir, app_config, market, language, keyword, timeout_seconds)
        elif strategy in {"manual_import", "seeded_mock"}:
            sample = {
                "title": f"{keyword} sample product",
                "source_url": f"https://example.com/search?{urllib.parse.urlencode({'q': keyword})}",
                "keyword": keyword,
                "market": market,
                "data_type": data_type,
            }
        else:
            raise ValueError(f"Provider strategy '{strategy}' is not supported by test runtime.")
        return {
            "ok": True,
            "status": "success" if sample else "empty",
            "source_id": source.get("id"),
            "provider_strategy": strategy,
            "market": market,
            "keyword": keyword,
            "items_found": 1 if sample else 0,
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


__all__ = [
    "build_hot_product_candidates",
    "build_run_response",
    "complete_provider_config_with_ai",
    "create_hot_product_run",
    "normalize_search_request",
    "public_product_research_config",
    "test_search_provider_connection",
]
