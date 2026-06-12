# -*- coding: utf-8 -*-
from __future__ import annotations


from typing import Callable

from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


PostHandler = Callable[[JsonRequestHandler], None]

def handle_category_attrs(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = body.get("platform", "mercadolibre")
    category_id = str(body.get("category_id", "")).strip()
    if platform == "mercadolibre" and category_id:
        token = load_store_config().get("mercadolibre", {}).get("access_token", "")
        if token:
            try:
                attrs = publisher.mercadolibre_category_attributes(category_id, token)
                handler.send_json({"ok": True, "source": "live", "required": attrs})
                return
            except Exception as exc:
                handler.send_json({"source": "mock", "warning": str(exc), **mock_category_attrs(platform, category_id)})
                return
    handler.send_json(mock_category_attrs(platform, category_id))
    return


def handle_category_search(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip()
    query = str(body.get("query") or body.get("keyword") or "").strip()
    limit = int(body.get("limit") or 20)
    results = search_category_cache(platform, query=query, site=site, limit=limit)
    handler.send_json(
        {
            "ok": True,
            "platform": platform,
            "site": site,
            "query": query,
            "cache_status": category_cache_status(platform),
            "results": results,
        }
    )
    return


def handle_category_ai_suggest(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip()
    product = normalize_product_fields(body.get("product") or load_product())
    limit = int(body.get("limit") or 5)
    handler.send_json(suggest_category_ids(product, platform=platform, site=site, limit=limit))
    return


def handle_category_cache_refresh(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip().upper()
    max_categories = int(body.get("max_categories") or 500)
    if platform == "mercadolibre":
        handler.send_json(refresh_official_category_cache(platform, site=site, max_categories=max_categories))
    else:
        cache = load_category_cache(platform)
        handler.send_json(
            {
                "ok": True,
                "platform": platform,
                "cache_status": category_cache_status(platform),
                "cache": cache,
                "warning": "当前平台暂未接入官方类目刷新，仅读取本地缓存。",
            }
        )
    return


def handle_category_cache_refresh_job(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip().upper()
    max_categories = int(body.get("max_categories") or 500)
    handler.send_json({"ok": True, "job": start_category_cache_refresh_job(platform, site=site, max_categories=max_categories)})
    return


def handle_category_ai_fill(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    category_id = str(body.get("category_id") or "").strip()
    product = normalize_product_fields(body.get("product") or load_product())
    record = find_category_record(platform, category_id) or body.get("category_record")
    updated = apply_ai_attribute_fill(product, platform, record if isinstance(record, dict) else None)
    saved = save_product(updated)
    handler.send_json(
        {
            "ok": True,
            "product": saved,
            "draft": saved.get("drafts", {}).get(platform, {}),
            "attributes": saved.get("drafts", {}).get(platform, {}).get("attributes", {}),
            "need_review": saved.get("drafts", {}).get(platform, {}).get("validation_errors", []),
            "cache_status": category_cache_status(platform),
        }
    )
    return


def handle_category_precheck(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    category_id = str(body.get("category_id") or "").strip()
    product = normalize_product_fields(body.get("product") or load_product())
    record = find_category_record(platform, category_id) or body.get("category_record")
    errors = validate_category_precheck(product, platform, record if isinstance(record, dict) else None)
    handler.send_json(
        {
            "ok": True,
            "platform": platform,
            "errors": errors,
            "missing_fields": errors,
            "cache_status": category_cache_status(platform),
        }
    )
    return


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/category-attrs": handle_category_attrs,
    "/api/category-search": handle_category_search,
    "/api/category-ai-suggest": handle_category_ai_suggest,
    "/api/category-cache/refresh": handle_category_cache_refresh,
    "/api/category-cache/refresh-job": handle_category_cache_refresh_job,
    "/api/category-ai-fill": handle_category_ai_fill,
    "/api/category-precheck": handle_category_precheck,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
