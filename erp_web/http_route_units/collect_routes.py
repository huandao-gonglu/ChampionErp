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


def handle_collect_resume(handler: JsonRequestHandler) -> None:
    handler.send_json(collect_facade.resume_collection_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    ))


def handle_collection_receipt(handler: JsonRequestHandler) -> None:
    handler.send_json(collect_facade.collection_receipt_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    ))


def handle_collection_verification(handler: JsonRequestHandler) -> None:
    handler.send_json(
        collect_facade.collection_verification_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
        200,
    )


def handle_manage_links(handler: JsonRequestHandler) -> None:
    handler.send_json(collect_facade.manage_links_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    ))


def handle_run_links(handler: JsonRequestHandler) -> None:
    handler.send_json(collect_facade.run_link_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    ))


def handle_verify_links(handler: JsonRequestHandler) -> None:
    handler.send_json(collect_facade.verify_link_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    ))


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/collection-links/manage": handle_manage_links,
    "/api/collection-links/run": handle_run_links,
    "/api/collection-links/verify": handle_verify_links,
    "/api/collect-source": handle_collect_source,
    "/api/collect-batch": handle_collect_batch,
    "/api/claim-products": handle_claim_products,
    "/api/collect-1688": handle_collect_1688,
    "/api/collect-verification": handle_collection_verification,
    "/api/collect-resume": handle_collect_resume,
    "/api/collect-receipt": handle_collection_receipt,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
