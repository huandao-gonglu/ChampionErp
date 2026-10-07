"""商品调研编排与持久记录；市场绑定显式数据源适配器。"""

from __future__ import annotations

from erp_web.services.external_request_context import request_operation

import hashlib
import json
import logging
import os
import threading
import time
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from erp_web.context import get_context
from erp_web.db import ErpDatabase
from erp_web.product_research_config import normalize_product_research_config
from erp_web.schemas.product_research import (
    HotProductCandidate,
    ProductResearchConfig,
    ProductResearchRun,
    ProductResearchSourceStatus,
)
from erp_web.services import config_service
from erp_web.services.product_research_methods import search_method_for


logger = logging.getLogger(__name__)
PRODUCT_RESEARCH_RUN_LOG_RELATIVE_PATH = Path("data") / "logs" / "product_research_runs.jsonl"
RUN_LOG_ITEM_PREVIEW_LIMIT = 10
RUN_STATUS_RETENTION_LIMIT = 100
TERMINAL_RUN_STATUSES = {"completed", "failed"}
RESTART_INTERRUPTED_DESCRIPTION = "服务已重启，后台任务已中断；已保留已接收的候选商品。"
RunProgressEvent = str | dict[str, Any]
RunProgressCallback = Callable[[RunProgressEvent], None]
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


def _mask_config_value(key: str, value: Any) -> Any:
    """Use the single recursive credential policy shared by all config APIs."""
    return config_service.mask_nested_config(value, key)


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
    keywords = _string_list(body.get("keywords"))
    if len(keywords) != 1 or len(keywords[0]) > 200:
        raise ValueError("请输入一个商品关键词（不超过 200 字）。")
    if len(target_markets) != 1:
        raise ValueError("每次仅查询一个目标市场。")
    max_limit = _int_value(defaults.get("max_limit"), 100, 1, 500)
    limit = _int_value(raw_options.get("limit"), _int_value(defaults.get("limit"), 12, 1, max_limit), 1, max_limit)
    return {
        "search_mode": "target_only",
        "markets": {
            "target_markets": target_markets,
            "reference_markets": [],
        },
        "keywords": _string_list(body.get("keywords")),
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


def _candidate_key(item: dict[str, Any]) -> str:
    return str(item.get("source_url") or item.get("id") or item.get("title") or "").strip()


class ProductResearchRunRegistry:
    """Registry for research runs: SQLite is the source of truth, memory a hot cache.

    Every write goes through to ``research_runs``/``research_candidates``;
    the in-memory dict only serves running handles and hot reads. Candidate
    results are kept permanently (no TTL). On construction — i.e. once per
    process — every non-terminal run left over in the database is marked
    ``failed`` while its already-received candidates stay queryable.
    """

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db
        self._lock = threading.RLock()
        self._runs: dict[str, ProductResearchRun] = {}
        self._order: list[str] = []
        self._candidate_operations: set[tuple[str, str]] = set()
        try:
            self._db.mark_interrupted_research_runs_failed(RESTART_INTERRUPTED_DESCRIPTION)
        except Exception:
            logger.exception("Failed to mark interrupted product research runs as failed")

    # -- persistence -----------------------------------------------------------

    def _persist(self, run: ProductResearchRun) -> None:
        params = {key: value for key, value in run.items() if key != "items"}
        items = [item for item in (run.get("items") or []) if isinstance(item, dict)]
        status = str(run.get("status") or "")
        self._db.save_research_run(
            str(run.get("run_id") or ""),
            status=status,
            method=str(run.get("search_mode") or ""),
            params=params,
            error=str(run.get("description") or "") if status == "failed" else "",
            created_at=str(run.get("created_at") or ""),
            updated_at=_utc_now(),
            items=items,
        )

    def _load_from_db(self, run_id: str) -> ProductResearchRun | None:
        record = self._db.load_research_run(run_id)
        if not record:
            return None
        run: ProductResearchRun = dict(record.get("params") or {})
        run["run_id"] = record["run_id"]
        run["status"] = record["status"]
        run["items"] = record["items"]
        if record["status"] == "failed" and record["error"]:
            run["description"] = record["error"]
            run["progress_description"] = str(run.get("progress_description") or "") or record["error"]
            if not run.get("completed_at"):
                run["completed_at"] = record["updated_at"]
            if not isinstance(run.get("source_status"), list) or not run.get("source_status"):
                run["source_status"] = [
                    {
                        "source": "product_research",
                        "source_id": "product_research",
                        "market": "",
                        "status": "failed",
                        "items_found": len(record["items"]),
                        "error_message": record["error"],
                        "provider_strategy": "run_registry",
                    }
                ]
        return run

    # -- memory hot cache ---------------------------------------------------------

    def _remember(self, run: ProductResearchRun) -> ProductResearchRun:
        run_id = str(run.get("run_id") or "").strip()
        self._runs[run_id] = deepcopy(run)
        if run_id not in self._order:
            self._order.append(run_id)
        while len(self._order) > RUN_STATUS_RETENTION_LIMIT:
            old_run_id = self._order.pop(0)
            self._runs.pop(old_run_id, None)
        return deepcopy(self._runs[run_id])

    # -- public API -----------------------------------------------------------------

    @contextmanager
    def candidate_operation(self, run_id: str, candidate_id: str):
        """同一候选不并发扣费；网络等待期间不持有持久化锁。"""
        key = (run_id, candidate_id)
        with self._lock:
            if key in self._candidate_operations:
                raise ValueError("该候选正在处理，请等待当前操作结束。")
            self._candidate_operations.add(key)
        try:
            yield
        finally:
            with self._lock:
                self._candidate_operations.discard(key)

    def update_candidate(self, run_id: str, candidate_id: str, **changes):
        with self._lock:
            run = self.get(run_id)
            if not run:
                raise ValueError("调研记录不存在。")
            for item in run.get("items", []):
                if item.get("id") == candidate_id:
                    item.update(deepcopy(changes))
                    self._persist(run)
                    self._remember(run)
                    return deepcopy(item)
            raise ValueError("候选商品不存在。")

    def store(self, run: ProductResearchRun) -> ProductResearchRun:
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        # Write-through: the table commits first, memory only publishes after —
        # a reader that sees the new state in memory can rely on the DB row.
        stored = deepcopy(run)
        self._persist(stored)
        with self._lock:
            return self._remember(stored)

    def update(self, run_id: str, **updates: Any) -> ProductResearchRun | None:
        run_key = str(run_id or "").strip()
        if not run_key:
            return None
        if "description" in updates:
            updates["description"] = _trim_run_description(updates.get("description"))
        if "progress_description" in updates:
            updates["progress_description"] = _trim_run_description(updates.get("progress_description"))
        with self._lock:
            run = self._runs.get(run_key)
            if run is None:
                run = self._load_from_db(run_key)
                if run is None:
                    return None
            updated = dict(deepcopy(run))
            updated.update(updates)
        self._persist(updated)
        with self._lock:
            return self._remember(updated)

    def append_items(
        self,
        run_id: str,
        items: list[HotProductCandidate],
        description: str = "",
    ) -> ProductResearchRun | None:
        run_key = str(run_id or "").strip()
        if not run_key or not items:
            return None
        with self._lock:
            run = self._runs.get(run_key)
            if run is None:
                return None
            updated = dict(deepcopy(run))
            current_items = [item for item in (updated.get("items") or []) if isinstance(item, dict)]
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
                updated["items"] = sorted(current_items, key=lambda item: int(item.get("rank") or 999999))
                updated["status"] = "running"
                updated["description"] = _trim_run_description(description or f"已接收 {len(current_items)} 个候选商品，查询仍在进行。")
                updated["progress_description"] = updated["description"]
        if added:
            self._persist(updated)
        with self._lock:
            return self._remember(updated)

    def get(self, run_id: str) -> ProductResearchRun | None:
        run_key = str(run_id or "").strip()
        if not run_key:
            return None
        with self._lock:
            run = self._runs.get(run_key)
            if run is not None:
                return deepcopy(run)
        loaded = self._load_from_db(run_key)
        if loaded is None:
            return None
        with self._lock:
            return self._remember(loaded)

    def get_active(self) -> ProductResearchRun | None:
        # Non-terminal runs only ever live in this process (older ones were
        # marked failed at construction), so the memory cache is authoritative.
        with self._lock:
            for run_id in reversed(self._order):
                run = self._runs.get(run_id)
                if run is None:
                    continue
                if str(run.get("status") or "") not in TERMINAL_RUN_STATUSES:
                    return deepcopy(run)
        return None


    def get_latest(self) -> ProductResearchRun | None:
        records = self._db.list_research_runs(limit=1)
        return self.get(records[0]["run_id"]) if records else None


def get_hot_product_run(run_id: str) -> ProductResearchRun | None:
    return get_context().research.get(run_id)


def get_active_hot_product_run() -> ProductResearchRun | None:
    return get_context().research.get_active()


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
    status["quota_receipts"] = diagnostics.get("quota_receipts", [])
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
        queried_methods: set[str] = set()
        for binding in bindings:
            if len(items) >= limit:
                break
            method_id = str(binding.get("method_id") or binding.get("methodId") or "").strip()
            if method_id in queried_methods:
                continue
            queried_methods.add(method_id)
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
    if os.name != "nt":
        path.parent.chmod(0o700)
        path.chmod(0o600)
    return path


def _run_hot_product_worker(
    registry: ProductResearchRunRegistry,
    app_dir: Path | str,
    run_id: str,
    request: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> None:
    registry.update(run_id, status="running", description="正在准备目标市场和搜索手段。")

    def progress(event: RunProgressEvent) -> None:
        if isinstance(event, dict):
            event_type = str(event.get("type") or "").strip()
            if event_type == "candidate":
                item = event.get("item")
                if isinstance(item, dict):
                    registry.append_items(run_id, [item])
                return
            description = str(event.get("description") or "").strip()
            if description:
                registry.update(run_id, status="running", description=description, progress_description=description)
            return
        description = str(event or "")
        registry.update(run_id, status="running", description=description, progress_description=description)

    try:
        with request_operation("product_research", operation_id=run_id, trigger="background"):
            items, source_status = build_hot_product_candidates(
                request,
                config,
                app_dir,
                app_config,
                progress_callback=progress,
            )
        description = _run_completion_description(items, source_status)
        run = registry.update(
            run_id,
            status="failed" if not items and any(row.get("status") in {"failed", "configuration_required"} for row in source_status) else "completed",
            completed_at=_utc_now(),
            items=items,
            source_status=source_status,
            description=description,
        )
    except Exception as exc:
        logger.exception("Product research run failed: %s", run_id)
        run = registry.update(
            run_id,
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


def _store_hot_product_run(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    execution_mode: str,
) -> tuple[ProductResearchRunRegistry, ProductResearchRun, dict[str, Any], dict[str, Any]]:
    normalized_config = normalize_product_research_config(config)
    request = normalize_search_request(body if isinstance(body, dict) else {}, normalized_config)
    created_at = _utc_now()
    run: ProductResearchRun = {
        "run_id": f"prr_{_stable_digest([created_at, request, execution_mode])}",
        "status": "queued",
        "search_mode": request["search_mode"],
        "created_at": created_at,
        "completed_at": "",
        "request": request,
        "items": [],
        "source_status": [],
        "description": "已创建运行任务，等待后台执行。",
        "progress_description": "",
    }
    registry = get_context().research
    stored = registry.store(run)
    return registry, stored, request, normalized_config


def create_hot_product_run(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchRun:
    """同步执行同一调研服务，供离线验收与明确的同步调用使用。"""

    registry, stored, request, normalized_config = _store_hot_product_run(
        body,
        config,
        execution_mode="focused",
    )
    _run_hot_product_worker(
        registry,
        app_dir,
        stored["run_id"],
        request,
        normalized_config,
        app_config,
    )
    return registry.get(stored["run_id"]) or stored


def create_hot_product_run_async(
    app_dir: Path | str,
    body: dict[str, Any],
    config: dict[str, Any],
    app_config: dict[str, Any] | None = None,
) -> ProductResearchRun:
    registry, stored, request, normalized_config = _store_hot_product_run(
        body,
        config,
        execution_mode="async",
    )
    worker = threading.Thread(
        target=_run_hot_product_worker,
        args=(registry, app_dir, stored["run_id"], deepcopy(request), deepcopy(normalized_config), deepcopy(app_config) if app_config is not None else None),
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
            "request": run.get("request"),
            "description": run.get("description") or "",
            "progress_description": run.get("progress_description") or "",
        },
        "items": run.get("items") or [],
        "source_status": run.get("source_status") or [],
    }


def build_run_not_found_response(run_id: str) -> dict[str, Any]:
    return {"ok": False, "error": f"选品运行不存在：{run_id}"}


def test_search_provider_connection(body, config, app_dir=".", app_config=None):
    from erp_web.services.sorftime_client import SorftimeClient

    provider = body.get("provider") or {}
    if provider.get("config_json", {}).get("provider_strategy") != "sorftime":
        raise ValueError("请选择 Sorftime 数据源。")
    client = SorftimeClient(provider.get("config_json", {}).get("api_key", ""))
    data = client.call("CoinQuery", 1, {})
    return {"ok": True, "status": "success", "source_id": provider.get("id"),
            "provider_strategy": "sorftime", "items_found": 0,
            "sample": {"balance": data, "quota_receipts": client.receipts}}


__all__ = [
    "ProductResearchRunRegistry",
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
