# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import copy_facade


PostHandler = Callable[[JsonRequestHandler], None]


def handle_generate_copy(handler: JsonRequestHandler) -> None:
    result, status = copy_facade.generate_copy_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_generate_copy_batch(handler: JsonRequestHandler) -> None:
    result, status = copy_facade.generate_copy_batch_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_generate_image_prompts(handler: JsonRequestHandler) -> None:
    result, status = copy_facade.generate_image_prompts_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_test_ai_model(handler: JsonRequestHandler) -> None:
    result, status = copy_facade.test_ai_model_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/generate-copy": handle_generate_copy,
    "/api/generate-copy-batch": handle_generate_copy_batch,
    "/api/generate-image-prompts": handle_generate_image_prompts,
    "/api/test-ai-model": handle_test_ai_model,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
