# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import collect_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_collect_source(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.collect_source_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_collect_batch(handler: JsonRequestHandler) -> None:
    handler.send_json(
        collect_facade.collect_batch_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
        200,
    )


def handle_claim_products(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.claim_products_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_collect_1688(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.collect_1688_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_collect_1688_clean(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.clean_1688_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_collect_from_browser_tab(handler: JsonRequestHandler) -> None:
    handler.send_json(
        collect_facade.collect_from_browser_tab_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
        200,
    )


def handle_collection_verification(handler: JsonRequestHandler) -> None:
    handler.send_json(
        collect_facade.collection_verification_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
        200,
    )


def handle_browser_debug_open_profile(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.open_browser_profile_payload()
    handler.send_json(result, status)


def handle_open_1688_browser(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.open_1688_browser_payload()
    handler.send_json(result, status)


def handle_collect_extension_payload(handler: JsonRequestHandler) -> None:
    result, status = collect_facade.collect_extension_payload_response(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/collect-source": handle_collect_source,
    "/api/collect-batch": handle_collect_batch,
    "/api/claim-products": handle_claim_products,
    "/api/collect-1688": handle_collect_1688,
    "/api/collect-1688-clean": handle_collect_1688_clean,
    "/api/collect-verification": handle_collection_verification,
    "/api/collect-from-browser-tab": handle_collect_from_browser_tab,
    "/api/browser-debug/open-profile": handle_browser_debug_open_profile,
    "/api/open-1688-browser": handle_open_1688_browser,
    "/api/collect-extension-payload": handle_collect_extension_payload,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
