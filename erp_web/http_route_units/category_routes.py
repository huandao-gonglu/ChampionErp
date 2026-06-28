# -*- coding: utf-8 -*-
from __future__ import annotations


from typing import Callable

import marketplace_publish as publisher
from product_model import validate_category_precheck

from .common import JsonRequestHandler
from ..runtime_units.category_attribute_ai_fill import apply_ai_model_attribute_fill
from ..runtime_units.category_attribute_translation import translate_category_attributes
from ..runtime_units.category_result_translation import translate_category_results
from ..runtime_units.category_refresh import refresh_official_category_cache, start_category_cache_refresh_job
from ..runtime_units.category_store import (
    category_cache_status,
    find_category_record,
    load_category_cache,
    search_category_cache,
    suggest_category_ids,
)
from ..runtime_units.product_store import load_product, load_store_config, normalize_product_fields, save_product
from ..runtime_units.publish_helpers import mock_category_attrs


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
    body_record = body.get("category_record")
    record = body_record if isinstance(body_record, dict) else find_category_record(platform, category_id)
    updated, fill_meta = apply_ai_model_attribute_fill(product, platform, record if isinstance(record, dict) else None)
    saved = save_product(updated)
    handler.send_json(
        {
            "ok": True,
            "fill_source": fill_meta.get("source"),
            "warning": fill_meta.get("warning", ""),
            "ai_filled": fill_meta.get("ai_filled", []),
            "product": saved,
            "draft": saved.get("drafts", {}).get(platform, {}),
            "attributes": saved.get("drafts", {}).get(platform, {}).get("attributes", {}),
            "need_review": saved.get("drafts", {}).get(platform, {}).get("validation_errors", []),
            "cache_status": category_cache_status(platform),
        }
    )
    return


def handle_category_attribute_translations(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    category_id = str(body.get("category_id") or "").strip()
    category_path = str(body.get("category_path") or "").strip()
    language = str(body.get("language") or "zh-CN").strip() or "zh-CN"
    attrs = body.get("attributes") if isinstance(body.get("attributes"), list) else []
    handler.send_json(translate_category_attributes(platform, category_id, category_path, attrs, language=language))
    return


def handle_category_result_translations(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    language = str(body.get("language") or "zh-CN").strip() or "zh-CN"
    categories = body.get("categories") if isinstance(body.get("categories"), list) else []
    handler.send_json(translate_category_results(platform, categories, language=language))
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
    "/api/category-attribute-translations": handle_category_attribute_translations,
    "/api/category-result-translations": handle_category_result_translations,
    "/api/category-precheck": handle_category_precheck,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
