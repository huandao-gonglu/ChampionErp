# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from typing import Any

import erp_db
import marketplace_publish as publisher
from erp_web import category_cache as category_cache_runtime

from .auth_runtime import _mercadolibre_app_secret
from .collect_helpers import collect_time_iso
from .product_store import _store_auth_result_fields, load_store_config, save_store_config
from .runtime_common import APP_DIR

CATEGORY_REFRESH_JOBS: dict[str, dict[str, Any]] = {}
CATEGORY_REFRESH_LOCK = threading.Lock()


def http_json(url: str, access_token: str | None = None) -> dict[str, Any] | list[Any]:
    return category_cache_runtime.http_json(url, access_token)


def _ml_attr_required(attr: dict[str, Any]) -> bool:
    return category_cache_runtime.ml_attr_required(attr)


def _normalize_ml_attribute(attr: dict[str, Any]) -> dict[str, Any]:
    return category_cache_runtime.normalize_ml_attribute(attr)


def _ml_attributes_for_category(category_id: str, access_token: str | None = None) -> dict[str, list[dict[str, Any]]]:
    return category_cache_runtime.ml_attributes_for_category(category_id, access_token=access_token, http_client=http_json)


def _ml_category_record(detail: dict[str, Any], site: str, attrs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return category_cache_runtime.ml_category_record(detail, site, attrs)


def build_mercadolibre_category_cache(site: str = "MLM", max_categories: int = 500, access_token: str | None = None) -> dict[str, Any]:
    return category_cache_runtime.build_mercadolibre_category_cache(
        site=site,
        max_categories=max_categories,
        access_token=access_token,
        http_client=http_json,
    )


def _refresh_mercadolibre_access_token_for_category_cache(config: dict[str, Any]) -> str:
    ml = config.setdefault("mercadolibre", {})
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = _mercadolibre_app_secret(ml)
    refresh_token = str(ml.get("refresh_token") or "").strip()
    if not app_id or not app_secret or not refresh_token:
        return ""
    refreshed = publisher.refresh_mercadolibre_token(app_id, app_secret, refresh_token)
    token = str(refreshed.get("access_token") or "").strip()
    if not token:
        return ""
    ml["access_token"] = token
    ml["refresh_token"] = str(refreshed.get("refresh_token") or refresh_token).strip()
    ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
    ml["auth_error_code"] = ""
    ml["auth_error_message"] = ""
    save_store_config(config)
    return token


def refresh_official_category_cache(
    platform: str,
    site: str = "",
    max_categories: int = 500,
    progress_callback: category_cache_runtime.ProgressCallback | None = None,
) -> dict[str, Any]:
    config = load_store_config()
    ml = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    resolved_site = str(site or ml.get("site_id") or "MLM").strip().upper() or "MLM"
    result = category_cache_runtime.refresh_official_category_cache(
        APP_DIR,
        platform,
        config,
        site=resolved_site,
        max_categories=max_categories,
        http_client=http_json,
        progress_callback=progress_callback,
    )
    if (
        str(platform or "").strip().lower() == "mercadolibre"
        and not result.get("ok")
        and result.get("error_code") == "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED"
        and str(ml.get("refresh_token") or "").strip()
    ):
        try:
            token = _refresh_mercadolibre_access_token_for_category_cache(config)
        except Exception as exc:
            token = ""
            result["refresh_error"] = str(exc)
        if token:
            if progress_callback:
                progress_callback({"stage": "token_refreshed", "site": resolved_site, "max_categories": max_categories})
            result = category_cache_runtime.refresh_official_category_cache(
                APP_DIR,
                platform,
                config,
                site=resolved_site,
                max_categories=max_categories,
                http_client=http_json,
                progress_callback=progress_callback,
            )
            if result.get("ok"):
                result["token_refreshed"] = True
    return result


def _category_refresh_job_update(job_id: str, updates: dict[str, Any]) -> None:
    with CATEGORY_REFRESH_LOCK:
        job = CATEGORY_REFRESH_JOBS.setdefault(job_id, {})
        job.update(updates)
        job["updated_at"] = collect_time_iso()


def _category_refresh_progress_percent(job: dict[str, Any]) -> int:
    status = str(job.get("status") or "")
    if status == "completed":
        return 100
    if status == "failed":
        return int(job.get("progress") or 100)
    max_categories = max(1, int(job.get("max_categories") or 500))
    visited = max(0, int(job.get("visited") or 0))
    return max(3, min(98, int(visited / max_categories * 100)))


def _category_refresh_job_snapshot(job_id: str) -> dict[str, Any]:
    with CATEGORY_REFRESH_LOCK:
        job = deepcopy(CATEGORY_REFRESH_JOBS.get(job_id) or {})
    if not job:
        raise KeyError(job_id)
    job["progress"] = _category_refresh_progress_percent(job)
    return job


def start_category_cache_refresh_job(platform: str, site: str = "", max_categories: int = 500) -> dict[str, Any]:
    job_id = f"cat-{uuid.uuid4().hex[:12]}"
    platform = str(platform or "mercadolibre").strip().lower()
    max_categories = max(1, int(max_categories or 500))
    _category_refresh_job_update(
        job_id,
        {
            "ok": True,
            "job_id": job_id,
            "platform": platform,
            "site": str(site or "").strip().upper(),
            "max_categories": max_categories,
            "status": "queued",
            "stage": "queued",
            "visited": 0,
            "records": 0,
            "queued": 0,
            "errors_count": 0,
            "started_at": collect_time_iso(),
        },
    )

    def run() -> None:
        partial_records: list[dict[str, Any]] = []
        partial_imported = 0

        def import_partial_records(force: bool = False) -> None:
            nonlocal partial_records, partial_imported
            if not partial_records or (not force and len(partial_records) < 25):
                return
            with CATEGORY_REFRESH_LOCK:
                job_site = str(CATEGORY_REFRESH_JOBS.get(job_id, {}).get("site") or site or "").strip().upper()
            cache = {
                "platform": "mercadolibre",
                "site": job_site or "MLM",
                "updated_at": collect_time_iso(),
                "records": list(partial_records),
            }
            imported = erp_db.import_category_cache(APP_DIR, cache)
            partial_imported += imported
            partial_records = []
            _category_refresh_job_update(job_id, {"partial_imported": partial_imported})

        def on_progress(progress: dict[str, Any]) -> None:
            record = progress.get("record")
            if isinstance(record, dict):
                partial_records.append(record)
                import_partial_records()
            _category_refresh_job_update(
                job_id,
                {
                    "status": "running",
                    "stage": progress.get("stage") or "running",
                    "site": progress.get("site") or "",
                    "category_id": progress.get("category_id") or "",
                    "visited": int(progress.get("visited") or 0),
                    "records": int(progress.get("records") or 0),
                    "queued": int(progress.get("queued") or 0),
                    "max_categories": int(progress.get("max_categories") or max_categories),
                    "errors_count": int(progress.get("errors") or 0),
                },
            )

        try:
            _category_refresh_job_update(job_id, {"status": "running", "stage": "starting"})
            result = refresh_official_category_cache(platform, site=site, max_categories=max_categories, progress_callback=on_progress)
            import_partial_records(force=True)
            terminal_status = "completed" if result.get("ok") else "failed"
            cache_status = result.get("cache_status") if isinstance(result.get("cache_status"), dict) else {}
            _category_refresh_job_update(
                job_id,
                {
                    "status": terminal_status,
                    "stage": terminal_status,
                    "ok": bool(result.get("ok")),
                    "site": result.get("site") or "",
                    "imported": int(result.get("imported") or 0),
                    "visited": int(result.get("visited") or 0),
                    "records": int(cache_status.get("records") or result.get("imported") or 0),
                    "errors_count": len(result.get("errors") or []),
                    "error": result.get("error") or "",
                    "error_code": result.get("error_code") or "",
                    "next_action": result.get("next_action") or "",
                    "result": {key: value for key, value in result.items() if key != "cache"},
                    "finished_at": collect_time_iso(),
                },
            )
        except Exception as exc:
            import_partial_records(force=True)
            _category_refresh_job_update(
                job_id,
                {
                    "status": "failed",
                    "stage": "failed",
                    "ok": False,
                    "error": str(exc),
                    "finished_at": collect_time_iso(),
                },
            )

    thread = threading.Thread(target=run, name=f"category-refresh-{job_id}", daemon=True)
    thread.start()
    return _category_refresh_job_snapshot(job_id)


def get_category_cache_refresh_job(job_id: str) -> dict[str, Any]:
    return _category_refresh_job_snapshot(str(job_id or "").strip())


__all__ = [
    "build_mercadolibre_category_cache",
    "get_category_cache_refresh_job",
    "http_json",
    "refresh_official_category_cache",
    "start_category_cache_refresh_job",
]
