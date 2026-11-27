# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable
import urllib.parse

from erp_web.schemas.requests import validate_request_payload
from erp_web.services.draft_transfer_service import ZIP_CONTENT_TYPES

from .common import JsonRequestHandler
from ..facades import product_facade, draft_pricing_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_draft_pricing_preview(handler: JsonRequestHandler) -> None:
    result, status = draft_pricing_facade.price_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path), apply=False,
    )
    handler.send_json(result, status)


def handle_draft_pricing_apply(handler: JsonRequestHandler) -> None:
    result, status = draft_pricing_facade.price_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path), apply=True,
    )
    handler.send_json(result, status)


def handle_draft_pricing_restore(handler: JsonRequestHandler) -> None:
    result, status = draft_pricing_facade.price_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path), apply=False, restore=True,
    )
    handler.send_json(result, status)


def handle_assign_upc(handler: JsonRequestHandler) -> None:
    result, status = product_facade.assign_upc(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_import_upcs(handler: JsonRequestHandler) -> None:
    result, status = product_facade.import_upcs_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_save_product(handler: JsonRequestHandler) -> None:
    handler.send_json(
        product_facade.save_product_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        )
    )


def handle_load_product(handler: JsonRequestHandler) -> None:
    result, status = product_facade.load_product_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_load_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.load_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_duplicate_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.duplicate_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_save_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.save_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_delete_products(handler: JsonRequestHandler) -> None:
    result, status = product_facade.delete_products_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_export_products(handler: JsonRequestHandler) -> None:
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    result, status = product_facade.export_products_ticket(body)
    handler.send_json(result, status)


def handle_import_products(handler: JsonRequestHandler) -> None:
    raw_zip = handler.read_binary_body(allowed_content_types=ZIP_CONTENT_TYPES)
    validate_request_payload({}, endpoint=handler.path)
    result, status = product_facade.import_products_archive(raw_zip)
    handler.send_json(result, status)


def handle_export_drafts(handler: JsonRequestHandler) -> None:
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    result, status = product_facade.export_drafts_ticket(body)
    handler.send_json(result, status)


def handle_import_drafts(handler: JsonRequestHandler) -> None:
    raw_zip = handler.read_binary_body(allowed_content_types=ZIP_CONTENT_TYPES)
    validate_request_payload({}, endpoint=handler.path)
    result, status = product_facade.import_drafts_archive(raw_zip)
    handler.send_json(result, status)


def handle_download_export(handler: JsonRequestHandler, parsed: object) -> None:
    query = urllib.parse.parse_qs(getattr(parsed, "query", "") or "")
    token = str((query.get("token") or [""])[0] or "").strip()
    entry = product_facade.resolve_export_download(token)
    if entry is None:
        handler.send_json(
            {"ok": False, "error": "导出文件不存在或已过期，请重新导出。", "error_code": "EXPORT_ARCHIVE_NOT_FOUND"},
            404,
        )
        return
    handler.send_file_stream(
        entry.path,
        content_type=entry.content_type,
        filename=entry.filename,
    )


def handle_delete_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.delete_draft_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


GET_API_ROUTES = frozenset({"/api/exports/download"})

POST_HANDLERS: dict[str, PostHandler] = {
    "/api/draft-pricing/preview": handle_draft_pricing_preview,
    "/api/draft-pricing/apply": handle_draft_pricing_apply,
    "/api/draft-pricing/restore": handle_draft_pricing_restore,
    "/api/assign-upc": handle_assign_upc,
    "/api/upc-pool/import": handle_import_upcs,
    "/api/save-product": handle_save_product,
    "/api/save-draft": handle_save_draft,
    "/api/load-product": handle_load_product,
    "/api/load-draft": handle_load_draft,
    "/api/duplicate-draft": handle_duplicate_draft,
    "/api/delete-draft": handle_delete_draft,
    "/api/delete-products": handle_delete_products,
    "/api/products/export": handle_export_products,
    "/api/products/import": handle_import_products,
    "/api/drafts/export": handle_export_drafts,
    "/api/drafts/import": handle_import_drafts,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if getattr(parsed, "path", "") != "/api/exports/download":
        return False
    handle_download_export(handler, parsed)
    return True


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
