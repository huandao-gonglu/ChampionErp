"""Product research hot-product candidate helpers.

Product-research runs return temporary hot-product candidates for selected
target markets. Target markets own search-method bindings; each
search method returns HotProductCandidate rows through a common runtime
contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from erp_web.product_research_config import normalize_product_research_config
from erp_web.schemas.product_research import (
    HotProductCandidate,
    ProductResearchConfig,
    ProductResearchDataSource,
    ProductResearchRun,
    ProductResearchSourceStatus,
)
from erp_web.services import ai_gateway, ai_model_config, ai_prompt_templates
from erp_web.services.product_research_methods import search_method_for


logger = logging.getLogger(__name__)
PRODUCT_RESEARCH_RUN_LOG_RELATIVE_PATH = Path("data") / "logs" / "product_research_runs.jsonl"
PRODUCT_RESEARCH_RUN_CACHE_RELATIVE_DIR = Path("data") / "cache" / "product_research" / "runs"
RUN_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
RUN_LOG_ITEM_PREVIEW_LIMIT = 10
RUN_STATUS_RETENTION_LIMIT = 100
TERMINAL_RUN_STATUSES = {"completed", "failed"}
RunProgressEvent = str | dict[str, Any]
RunProgressCallback = Callable[[RunProgressEvent], None]
_RUNS_LOCK = threading.RLock()
_RUNS: dict[str, ProductResearchRun] = {}
_RUN_ORDER: list[str] = []
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


def _utc_datetime_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_datetime_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_expiry(created_at: Any = None) -> str:
    base = _parse_utc_datetime(created_at) or _utc_datetime_now()
    return _utc_iso(base + timedelta(seconds=RUN_CACHE_TTL_SECONDS))


def _ensure_run_expiry(run: ProductResearchRun) -> ProductResearchRun:
    next_run: ProductResearchRun = deepcopy(run)
    if not str(next_run.get("expires_at") or "").strip():
        next_run["expires_at"] = _run_expiry(next_run.get("created_at"))
    return next_run


def _is_run_expired(run: ProductResearchRun) -> bool:
    expires_at = _parse_utc_datetime(run.get("expires_at")) or _parse_utc_datetime(_run_expiry(run.get("created_at")))
    if expires_at is None:
        return False
    return expires_at <= _utc_datetime_now()


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


def public_product_research_config(config: dict[str, Any], app_dir: Path | str = ".") -> ProductResearchConfig:
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
    max_limit = _int_value(defaults.get("max_limit"), 100, 1, 500)
    limit = _int_value(raw_options.get("limit"), _int_value(defaults.get("limit"), 12, 1, max_limit), 1, max_limit)
    return {
        "search_mode": "target_only",
        "markets": {
            "target_markets": target_markets,
            "reference_markets": [],
        },
        "keywords": [],
        "result_options": {
            "limit": limit,
            "sort_by": "rank",
        },
    }


def _trim_run_description(value: Any, max_length: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return "..." + text[-max_length:]


def product_research_run_cache_dir(app_dir: Path | str = ".") -> Path:
    return Path(app_dir) / PRODUCT_RESEARCH_RUN_CACHE_RELATIVE_DIR


def _safe_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    return "".join(char for char in run_id if char.isalnum() or char in {"_", "-"})


def product_research_run_cache_path(app_dir: Path | str, run_id: str) -> Path:
    safe_run_id = _safe_run_id(run_id)
    if not safe_run_id:
        raise ValueError("run_id is required")
    return product_research_run_cache_dir(app_dir) / f"{safe_run_id}.json"


def _write_run_cache(app_dir: Path | str, run: ProductResearchRun) -> Path:
    cached_run = _ensure_run_expiry(run)
    path = product_research_run_cache_path(app_dir, str(cached_run.get("run_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(cached_run, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)
    return path


def _delete_run_cache(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("Failed to delete expired product research run cache: %s", path)


def _read_run_cache_file(path: Path) -> ProductResearchRun | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to read product research run cache: %s", path)
        return None
    if not isinstance(payload, dict) or not str(payload.get("run_id") or "").strip():
        return None
    run = _ensure_run_expiry(payload)
    if _is_run_expired(run):
        _delete_run_cache(path)
        return None
    return run


def _restore_cached_run_for_process(app_dir: Path | str, run: ProductResearchRun) -> ProductResearchRun:
    restored = _ensure_run_expiry(run)
    if str(restored.get("status") or "") in TERMINAL_RUN_STATUSES:
        return restored
    restored["status"] = "failed"
    restored["completed_at"] = restored.get("completed_at") or _utc_now()
    restored["description"] = "服务已重启，后台任务已中断；已保留已接收的候选商品。"
    restored["progress_description"] = restored.get("progress_description") or restored["description"]
    if not isinstance(restored.get("source_status"), list) or not restored.get("source_status"):
        restored["source_status"] = [
            {
                "source": "product_research",
                "source_id": "product_research",
                "market": "",
                "status": "failed",
                "items_found": len(restored.get("items") or []),
                "error_message": restored["description"],
                "provider_strategy": "run_cache",
            }
        ]
    try:
        _write_run_cache(app_dir, restored)
    except Exception:
        logger.exception("Failed to update interrupted product research run cache: %s", restored.get("run_id"))
    return restored


def _remember_run(run: ProductResearchRun) -> ProductResearchRun:
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    cached_run = _ensure_run_expiry(run)
    _RUNS[run_id] = deepcopy(cached_run)
    if run_id not in _RUN_ORDER:
        _RUN_ORDER.append(run_id)
    while len(_RUN_ORDER) > RUN_STATUS_RETENTION_LIMIT:
        old_run_id = _RUN_ORDER.pop(0)
        _RUNS.pop(old_run_id, None)
    return deepcopy(_RUNS[run_id])


def _store_run(run: ProductResearchRun, app_dir: Path | str | None = None) -> ProductResearchRun:
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    with _RUNS_LOCK:
        stored = _remember_run(run)
    if app_dir is not None:
        try:
            _write_run_cache(app_dir, stored)
        except Exception:
            logger.exception("Failed to write product research run cache: %s", run_id)
    return stored


def _update_run(run_id: str, app_dir: Path | str | None = None, **updates: Any) -> ProductResearchRun | None:
    run_key = str(run_id or "").strip()
    if not run_key:
        return None
    if "description" in updates:
        updates["description"] = _trim_run_description(updates.get("description"))
    if "progress_description" in updates:
        updates["progress_description"] = _trim_run_description(updates.get("progress_description"))
    with _RUNS_LOCK:
        run = _RUNS.get(run_key)
        if run is None:
            return None
        run.update(updates)
        updated = _ensure_run_expiry(run)
        _RUNS[run_key] = deepcopy(updated)
    if app_dir is not None:
        try:
            _write_run_cache(app_dir, updated)
        except Exception:
            logger.exception("Failed to write product research run cache: %s", run_key)
    return deepcopy(updated)


def _candidate_key(item: dict[str, Any]) -> str:
    return str(item.get("source_url") or item.get("id") or item.get("title") or "").strip()


def _append_run_items(
    run_id: str,
    items: list[HotProductCandidate],
    description: str = "",
    app_dir: Path | str | None = None,
) -> ProductResearchRun | None:
    run_key = str(run_id or "").strip()
    if not run_key or not items:
        return None
    with _RUNS_LOCK:
        run = _RUNS.get(run_key)
        if run is None:
            return None
        current_items = [item for item in (run.get("items") or []) if isinstance(item, dict)]
        seen = {_candidate_key(item) for item in current_items if _candidate_key(item)}
        added = False
        for item in items:
            key = _candidate_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            current_items.append(item)
            added = True
        if added:
            run["items"] = sorted(current_items, key=lambda item: int(item.get("rank") or 999999))
            run["status"] = "running"
            run["description"] = _trim_run_description(description or f"已接收 {len(current_items)} 个候选商品，AI 仍在继续搜索。")
            run["progress_description"] = run["description"]
        updated = _ensure_run_expiry(run)
        _RUNS[run_key] = deepcopy(updated)
    if added and app_dir is not None:
        try:
            _write_run_cache(app_dir, updated)
        except Exception:
            logger.exception("Failed to write product research run cache: %s", run_key)
    return deepcopy(updated)


def get_hot_product_run(run_id: str, app_dir: Path | str | None = None) -> ProductResearchRun | None:
    run_key = str(run_id or "").strip()
    if not run_key:
        return None
    with _RUNS_LOCK:
        run = _RUNS.get(run_key)
        if run is not None:
            if _is_run_expired(run):
                _RUNS.pop(run_key, None)
                if run_key in _RUN_ORDER:
                    _RUN_ORDER.remove(run_key)
            else:
                return deepcopy(run)
    if app_dir is None:
        return None
    path = product_research_run_cache_path(app_dir, run_key)
    cached_run = _read_run_cache_file(path)
    if cached_run is None:
        return None
    restored = _restore_cached_run_for_process(app_dir, cached_run)
    return _store_run(restored)


def _load_cached_runs(app_dir: Path | str) -> list[ProductResearchRun]:
    cache_dir = product_research_run_cache_dir(app_dir)
    if not cache_dir.exists():
        return []
    try:
        paths = sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except Exception:
        logger.exception("Failed to list product research run cache: %s", cache_dir)
        return []
    runs: list[ProductResearchRun] = []
    for path in paths[:RUN_STATUS_RETENTION_LIMIT]:
        run = _read_run_cache_file(path)
        if run is None:
            continue
        runs.append(_restore_cached_run_for_process(app_dir, run))
    return runs


def get_active_hot_product_run(app_dir: Path | str | None = None) -> ProductResearchRun | None:
    with _RUNS_LOCK:
        for run_id in reversed(_RUN_ORDER):
            run = _RUNS.get(run_id)
            if run is None:
                continue
            if _is_run_expired(run):
                _RUNS.pop(run_id, None)
                continue
            if str(run.get("status") or "") not in TERMINAL_RUN_STATUSES:
                return deepcopy(run)
    if app_dir is not None:
        for run in reversed(_load_cached_runs(app_dir)):
            _store_run(run)
        with _RUNS_LOCK:
            for run_id in reversed(_RUN_ORDER):
                run = _RUNS.get(run_id)
                if run is None or _is_run_expired(run):
                    continue
                if str(run.get("status") or "") not in TERMINAL_RUN_STATUSES:
                    return deepcopy(run)
    return None


def _search_method_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = config.get("search_providers") if isinstance(config.get("search_providers"), list) else []
    return {
        str(row.get("id") or "").strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }


def _enabled_market_search_bindings(target: dict[str, Any]) -> list[dict[str, Any]]:
    rows = target.get("search_methods") if isinstance(target.get("search_methods"), list) else []
    return [
        row for row in rows
        if isinstance(row, dict) and row.get("enabled", True) and str(row.get("method_id") or row.get("methodId") or "").strip()
    ]


def _method_strategy(method: dict[str, Any]) -> str:
    config_json = method.get("config_json") if isinstance(method.get("config_json"), dict) else {}
    return str(config_json.get("provider_strategy") or method.get("provider_strategy") or method.get("source_type") or "").strip()


def _runner_diagnostics(runner: Any) -> dict[str, Any]:
    diagnostics = getattr(runner, "last_diagnostics", {})
    return diagnostics if isinstance(diagnostics, dict) else {}


def _source_status_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for key in ("raw_items_found", "items_filtered"):
        if key in diagnostics:
            status[key] = _int_value(diagnostics.get(key), 0, 0)
    for key in ("ai_model_id", "api_style"):
        value = str(diagnostics.get(key) or "").strip()
        if value:
            status[key] = value
    for key in ("stream_enabled", "stream_fallback_used"):
        if key in diagnostics:
            status[key] = bool(diagnostics.get(key))
    message = str(diagnostics.get("diagnostic_message") or "").strip()
    if message:
        status["diagnostic_message"] = message
    return status


def _empty_source_error(diagnostics: dict[str, Any]) -> str:
    message = str(diagnostics.get("diagnostic_message") or "").strip()
    return message or "搜索手段没有返回候选商品"


def _run_completion_description(items: list[HotProductCandidate], source_status: list[ProductResearchSourceStatus]) -> str:
    if items:
        return f"运行完成，找到 {len(items)} 个候选商品。"
    diagnostics = [
        str(status.get("diagnostic_message") or status.get("error_message") or "").strip()
        for status in source_status
        if isinstance(status, dict) and str(status.get("diagnostic_message") or status.get("error_message") or "").strip()
    ]
    suffix = f" {diagnostics[0]}" if diagnostics else ""
    return f"运行完成，但没有返回候选商品。{suffix}"


def build_hot_product_candidates(
    request: dict[str, Any],
    config: dict[str, Any],
    app_dir: Path | str = ".",
    app_config: dict[str, Any] | None = None,
    progress_callback: RunProgressCallback | None = None,
) -> tuple[list[HotProductCandidate], list[ProductResearchSourceStatus]]:
    cfg = normalize_product_research_config(config)
    limit = int(request.get("result_options", {}).get("limit") or 12)
    items: list[HotProductCandidate] = []
    statuses: list[ProductResearchSourceStatus] = []
    methods_by_id = _search_method_by_id(cfg)

    for market in request["markets"]["target_markets"]:
        target = _target_market_context(cfg, market)
        if not target:
            statuses.append(
                {
                    "source": "target_market",
                    "source_id": "target_market",
                    "market": market,
                    "status": "empty",
                    "items_found": 0,
                    "error_message": "目标市场不存在",
                    "provider_strategy": "target_market",
                }
            )
            continue
        bindings = _enabled_market_search_bindings(target)
        if not bindings:
            statuses.append(
                {
                    "source": "search_methods",
                    "source_id": "search_methods",
                    "market": market,
                    "status": "empty",
                    "items_found": 0,
                    "error_message": "目标市场还没有关联搜索手段",
                    "provider_strategy": "target_binding",
                }
            )
            continue
        for binding in bindings:
            if len(items) >= limit:
                break
            method_id = str(binding.get("method_id") or binding.get("methodId") or "").strip()
            method = methods_by_id.get(method_id)
            if not method:
                statuses.append(
                    {
                        "source": method_id or "search_method",
                        "source_id": method_id,
                        "market": market,
                        "status": "configuration_required",
                        "items_found": 0,
                        "error_message": "目标市场关联的搜索手段不存在",
                        "provider_strategy": "missing_method",
                    }
                )
                continue
            if method.get("enabled") is False:
                statuses.append(
                    {
                        "source": str(method.get("name") or method_id),
                        "source_id": method_id,
                        "market": market,
                        "status": "skipped",
                        "items_found": 0,
                        "error_message": "搜索手段未启用",
                        "provider_strategy": _method_strategy(method),
                    }
                )
                continue
            start_count = len(items)
            runner = None
            try:
                runner = search_method_for(method)
                if progress_callback:
                    progress_callback(f"正在通过 {method.get('name') or method_id} 获取 {market} 的候选商品。")
                method_items = runner.run(
                    market=target,
                    method=method,
                    binding=binding,
                    keywords=request["keywords"],
                    limit=limit - len(items),
                    config=cfg,
                    app_dir=app_dir,
                    app_config=app_config,
                    progress_callback=progress_callback,
                )
                diagnostics = _runner_diagnostics(runner)
                items.extend(method_items)
                found = len(items) - start_count
                statuses.append(
                    {
                        "source": str(method.get("name") or method_id),
                        "source_id": method_id,
                        "market": market,
                        "status": "success" if found else "empty",
                        "items_found": found,
                        "error_message": "" if found else _empty_source_error(diagnostics),
                        "provider_strategy": _method_strategy(method),
                        **_source_status_diagnostics(diagnostics),
                    }
                )
            except Exception as exc:
                diagnostics = _runner_diagnostics(runner)
                statuses.append(
                    {
                        "source": str(method.get("name") or method_id),
                        "source_id": method_id,
                        "market": market,
                        "status": "failed",
                        "items_found": 0,
                        "error_message": str(exc),
                        "provider_strategy": _method_strategy(method),
                        **_source_status_diagnostics(diagnostics),
                    }
                )
    return items, statuses


def product_research_run_log_path(app_dir: Path | str = ".") -> Path:
    return Path(app_dir) / PRODUCT_RESEARCH_RUN_LOG_RELATIVE_PATH


def _candidate_log_preview(item: HotProductCandidate) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "rank": item.get("rank"),
        "source_url": item.get("source_url"),
        "market_id": item.get("market_id"),
        "platform": item.get("platform"),
        "site": item.get("site"),
        "keyword": item.get("keyword"),
        "price": item.get("price"),
        "rating": item.get("rating"),
        "review_count": item.get("review_count"),
        "hot_score": item.get("hot_score"),
        "source_name": item.get("source_name"),
    }


def build_run_log_record(run: ProductResearchRun) -> dict[str, Any]:
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    markets = request.get("markets") if isinstance(request.get("markets"), dict) else {}
    items = run.get("items") if isinstance(run.get("items"), list) else []
    source_status = run.get("source_status") if isinstance(run.get("source_status"), list) else []
    return {
        "logged_at": _utc_now(),
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "search_mode": run.get("search_mode"),
        "created_at": run.get("created_at"),
        "completed_at": run.get("completed_at"),
        "target_markets": markets.get("target_markets") or [],
        "reference_markets": markets.get("reference_markets") or [],
        "request": request,
        "items_count": len(items),
        "source_status": source_status,
        "items_preview": [
            _candidate_log_preview(item)
            for item in items[:RUN_LOG_ITEM_PREVIEW_LIMIT]
            if isinstance(item, dict)
        ],
    }


def append_product_research_run_log(app_dir: Path | str, run: ProductResearchRun) -> Path:
    path = product_research_run_log_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = build_run_log_record(run)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def create_hot_product_run(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchRun:
    normalized_config = normalize_product_research_config(config)
    request = normalize_search_request(body if isinstance(body, dict) else {}, normalized_config)
    created_at = _utc_now()
    items, source_status = build_hot_product_candidates(request, normalized_config, app_dir, app_config)
    run: ProductResearchRun = {
        "run_id": f"prr_{_stable_digest([created_at, request, len(items)])}",
        "status": "completed",
        "search_mode": request["search_mode"],
        "created_at": created_at,
        "completed_at": _utc_now(),
        "expires_at": _run_expiry(created_at),
        "request": request,
        "items": items,
        "source_status": source_status,
        "description": _run_completion_description(items, source_status),
        "progress_description": "",
    }
    run = _store_run(run, app_dir)
    try:
        append_product_research_run_log(app_dir, run)
    except Exception:
        logger.exception("Failed to write product research run log: %s", run.get("run_id"))
    return run


def _run_hot_product_worker(
    app_dir: Path | str,
    run_id: str,
    request: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> None:
    _update_run(run_id, app_dir, status="running", description="正在准备目标市场和搜索手段。")

    def progress(event: RunProgressEvent) -> None:
        if isinstance(event, dict):
            event_type = str(event.get("type") or "").strip()
            if event_type == "candidate":
                item = event.get("item")
                if isinstance(item, dict):
                    _append_run_items(run_id, [item], app_dir=app_dir)
                return
            description = str(event.get("description") or "").strip()
            if description:
                _update_run(run_id, app_dir, status="running", description=description, progress_description=description)
            return
        description = str(event or "")
        _update_run(run_id, app_dir, status="running", description=description, progress_description=description)

    try:
        items, source_status = build_hot_product_candidates(
            request,
            config,
            app_dir,
            app_config,
            progress_callback=progress,
        )
        description = _run_completion_description(items, source_status)
        run = _update_run(
            run_id,
            app_dir,
            status="completed",
            completed_at=_utc_now(),
            items=items,
            source_status=source_status,
            description=description,
        )
    except Exception as exc:
        logger.exception("Product research run failed: %s", run_id)
        run = _update_run(
            run_id,
            app_dir,
            status="failed",
            completed_at=_utc_now(),
            items=[],
            source_status=[
                {
                    "source": "product_research",
                    "source_id": "product_research",
                    "market": "",
                    "status": "failed",
                    "items_found": 0,
                    "error_message": str(exc),
                    "provider_strategy": "runtime",
                }
            ],
            description=f"运行失败：{exc}",
        )
    if run is not None:
        try:
            append_product_research_run_log(app_dir, run)
        except Exception:
            logger.exception("Failed to write product research run log: %s", run_id)


def create_hot_product_run_async(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchRun:
    normalized_config = normalize_product_research_config(config)
    request = normalize_search_request(body if isinstance(body, dict) else {}, normalized_config)
    created_at = _utc_now()
    run: ProductResearchRun = {
        "run_id": f"prr_{_stable_digest([created_at, request, 'async'])}",
        "status": "queued",
        "search_mode": request["search_mode"],
        "created_at": created_at,
        "completed_at": "",
        "expires_at": _run_expiry(created_at),
        "request": request,
        "items": [],
        "source_status": [],
        "description": "已创建运行任务，等待后台执行。",
        "progress_description": "",
    }
    stored = _store_run(run, app_dir)
    worker = threading.Thread(
        target=_run_hot_product_worker,
        args=(app_dir, stored["run_id"], deepcopy(request), deepcopy(normalized_config), deepcopy(app_config) if app_config is not None else None),
        daemon=True,
    )
    worker.start()
    return stored


def build_run_response(run: ProductResearchRun) -> dict[str, Any]:
    return {
        "ok": True,
        "description": run.get("description") or "",
        "run": {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "search_mode": run.get("search_mode"),
            "created_at": run.get("created_at"),
            "completed_at": run.get("completed_at"),
            "expires_at": run.get("expires_at"),
            "request": run.get("request"),
            "description": run.get("description") or "",
            "progress_description": run.get("progress_description") or "",
        },
        "items": run.get("items") or [],
        "source_status": run.get("source_status") or [],
    }


def build_run_not_found_response(run_id: str) -> dict[str, Any]:
    return {"ok": False, "error": f"选品运行不存在：{run_id}"}


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


def _ai_web_search_sample(
    source: ProductResearchDataSource,
    config: dict[str, Any],
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    market: str,
    language: str,
    keyword: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    model_id = str(config_json.get("ai_model_id") or config_json.get("model_id") or "").strip()
    model = ai_gateway.resolve_model_for_use_case(app_dir, app_config, "research.web_search", model_id=model_id)
    capabilities = ai_model_config.normalize_capabilities(model.get("capabilities"))
    if ai_model_config.CAP_WEB_SEARCH not in capabilities:
        raise RuntimeError("AI 搜索需要选择一个支持 web_search 的 AI 模型。")
    prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(app_dir, app_config, "research.web_search")
    user_prompt = ai_prompt_templates.render_prompt_template(
        prompt_pair["user"],
        {
            "market": market,
            "market_id": market,
            "marketId": market,
            "display_name": market,
            "displayName": market,
            "platform": str(source.get("platform") or ""),
            "site": str(source.get("site") or ""),
            "currency": "USD",
            "language": language or "en",
            "keyword": keyword,
            "keywords": keyword,
            "limit": 1,
        },
    )
    parsed = ai_gateway.chat_json(
        app_dir,
        app_config,
        "research.web_search",
        [
            {"role": "system", "content": prompt_pair["system"]},
            {"role": "user", "content": user_prompt},
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


def _manual_import_sample(source: ProductResearchDataSource, keyword: str, market: str, data_type: str) -> dict[str, Any]:
    config_json = source.get("config_json") if isinstance(source.get("config_json"), dict) else {}
    items = config_json.get("items") if isinstance(config_json.get("items"), list) else []
    first = next((item for item in items if isinstance(item, dict)), {})
    if not first:
        return {}
    return {
        "title": str(first.get("title") or keyword).strip(),
        "source_url": str(first.get("source_url") or first.get("sourceUrl") or first.get("url") or "").strip(),
        "keyword": str(first.get("keyword") or keyword).strip(),
        "market": str(first.get("market") or first.get("market_id") or first.get("marketId") or market).strip(),
        "data_type": data_type,
    }


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
            sample = _ai_web_search_sample(source, current_config, app_dir, app_config, market, language, keyword, timeout_seconds)
        elif strategy == "manual_import":
            sample = _manual_import_sample(source, keyword, market, data_type)
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
    "append_product_research_run_log",
    "build_hot_product_candidates",
    "build_run_log_record",
    "build_run_not_found_response",
    "build_run_response",
    "create_hot_product_run",
    "create_hot_product_run_async",
    "get_active_hot_product_run",
    "get_hot_product_run",
    "normalize_search_request",
    "product_research_run_log_path",
    "public_product_research_config",
    "test_search_provider_connection",
]
