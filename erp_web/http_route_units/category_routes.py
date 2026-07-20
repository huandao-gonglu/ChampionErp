# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.product_model import validate_category_precheck

from .common import JsonRequestHandler
from ..runtime_units.category_attribute_ai_fill import apply_ai_model_attribute_fill
from ..runtime_units.category_attribute_translation import translate_category_attributes
from ..runtime_units.category_result_translation import translate_category_results
from ..runtime_units.category_store import (
    fetch_category_attributes,
    fetch_category_record,
    search_categories_live,
    suggest_category_ids,
)
from ..runtime_units.draft_publish_context import load_required_draft_publish_context, save_draft_target_listing_result
from ..runtime_units.product_store import load_required_product_from_body, save_product


PostHandler = Callable[[JsonRequestHandler], None]


def _send_category_error(handler: JsonRequestHandler, exc: Exception, status: int = 400) -> None:
    handler.send_json(
        {
            "ok": False,
            "error": str(exc),
            "error_code": "CATEGORY_LIVE_API_FAILED",
            "next_action": "请确认目标站点、类目 ID 和 Mercado Libre 授权可用后重试。",
        },
        status,
    )


def _category_path(record: dict[str, object]) -> str:
    value = record.get("category_path")
    if isinstance(value, str) and value.strip():
        return value.strip()
    path = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    return " / ".join(str(item).strip() for item in path if str(item).strip())


def handle_category_attrs(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("site_id") or body.get("country") or "").strip()
    category_id = str(body.get("category_id") or "").strip()
    if not category_id:
        handler.send_json({"ok": False, "error": "缺少 category_id", "error_code": "CATEGORY_ID_REQUIRED"}, 400)
        return
    try:
        handler.send_json(fetch_category_attributes(platform, category_id, site=site))
    except Exception as exc:
        _send_category_error(handler, exc)


def handle_category_search(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip()
    query = str(body.get("query") or body.get("keyword") or "").strip()
    limit = int(body.get("limit") or 20)
    try:
        results = search_categories_live(platform, query=query, site=site, limit=limit)
        handler.send_json({"ok": True, "platform": platform, "site": site, "query": query, "source": "mercadolibre_live", "results": results})
    except Exception as exc:
        _send_category_error(handler, exc)


def handle_category_ai_suggest(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("country") or "").strip()
    if body.get("draft_id") or body.get("draftId"):
        context, error_response, status = load_required_draft_publish_context(body)
        if error_response:
            handler.send_json(error_response, status)
            return
        product = context["product"]
        platform = context["platform"]
        site = context["site"]
    else:
        product, error_response, status = load_required_product_from_body(body)
        if error_response:
            handler.send_json(error_response, status)
            return
    limit = int(body.get("limit") or 5)
    try:
        handler.send_json(suggest_category_ids(product, platform=platform, site=site, limit=limit))
    except Exception as exc:
        _send_category_error(handler, exc)


def handle_category_ai_fill(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    category_id = str(body.get("category_id") or "").strip()
    site = str(body.get("site") or body.get("site_id") or body.get("country") or "").strip()
    context: dict[str, object] | None = None
    if body.get("draft_id") or body.get("draftId"):
        context, error_response, status = load_required_draft_publish_context(body)
        if error_response:
            handler.send_json(error_response, status)
            return
        product = context["product"]
        platform = str(context["platform"])
        site = str(context.get("site") or site)
    else:
        product, error_response, status = load_required_product_from_body(body)
        if error_response:
            handler.send_json(error_response, status)
            return
    body_record = body.get("category_record")
    try:
        record = body_record if isinstance(body_record, dict) else fetch_category_record(platform, category_id, site=site, include_attributes=True)
    except Exception as exc:
        _send_category_error(handler, exc)
        return
    updated, fill_meta = apply_ai_model_attribute_fill(product, platform, record if isinstance(record, dict) else None)
    if context:
        updated_drafts = updated.get("drafts") if isinstance(updated.get("drafts"), dict) else {}
        updated_draft = updated_drafts.get(platform) if isinstance(updated_drafts.get(platform), dict) else {}
        saved = save_draft_target_listing_result(context, updated_draft)
        saved_draft = saved.get("draft", {})
        handler.send_json(
            {
                "ok": True,
                "fill_source": fill_meta.get("source"),
                "warning": fill_meta.get("warning", ""),
                "ai_filled": fill_meta.get("ai_filled", []),
                "draft": saved_draft,
                "productContext": saved.get("productContext"),
                "productsIndex": saved.get("productsIndex", []),
                "draftsIndex": saved.get("draftsIndex", []),
                "attributes": updated_draft.get("attributes", {}) if isinstance(updated_draft, dict) else {},
                "need_review": updated_draft.get("validation_errors", []) if isinstance(updated_draft, dict) else [],
            }
        )
        return
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
        }
    )


def handle_category_attribute_translations(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    category_id = str(body.get("category_id") or "").strip()
    category_path = str(body.get("category_path") or "").strip()
    language = str(body.get("language") or "zh-CN").strip() or "zh-CN"
    attrs = body.get("attributes") if isinstance(body.get("attributes"), list) else []
    handler.send_json(translate_category_attributes(platform, category_id, category_path, attrs, language=language))


def handle_category_result_translations(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    language = str(body.get("language") or "zh-CN").strip() or "zh-CN"
    categories = body.get("categories") if isinstance(body.get("categories"), list) else []
    handler.send_json(translate_category_results(platform, categories, language=language))


def handle_category_precheck(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    site = str(body.get("site") or body.get("site_id") or body.get("country") or "").strip()
    category_id = str(body.get("category_id") or "").strip()
    if body.get("draft_id") or body.get("draftId"):
        context, error_response, status = load_required_draft_publish_context(body)
        if error_response:
            handler.send_json(error_response, status)
            return
        product = context["product"]
        platform = context["platform"]
        site = str(context.get("site") or site)
    else:
        product, error_response, status = load_required_product_from_body(body)
        if error_response:
            handler.send_json(error_response, status)
            return
    body_record = body.get("category_record")
    try:
        record = body_record if isinstance(body_record, dict) else fetch_category_record(platform, category_id, site=site, include_attributes=True)
    except Exception as exc:
        _send_category_error(handler, exc)
        return
    errors = validate_category_precheck(product, platform, record if isinstance(record, dict) else None)
    handler.send_json(
        {
            "ok": True,
            "platform": platform,
            "site": site,
            "category_id": category_id,
            "category_path": _category_path(record if isinstance(record, dict) else {}),
            "category_record": record if isinstance(record, dict) else {},
            "errors": errors,
            "missing_fields": errors,
        }
    )


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/category-attrs": handle_category_attrs,
    "/api/category-search": handle_category_search,
    "/api/category-ai-suggest": handle_category_ai_suggest,
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
