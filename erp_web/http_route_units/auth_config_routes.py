# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import auth_config_facade


PostHandler = Callable[[JsonRequestHandler], None]
APPROVAL_TOKEN_HEADER = "X-Approval-Token"


def _approval_token(handler: JsonRequestHandler) -> str:
    headers = getattr(handler, "headers", None)
    get = getattr(headers, "get", None)
    return (
        str(get(APPROVAL_TOKEN_HEADER) or "").strip()
        if callable(get)
        else ""
    )


def _send(
    handler: JsonRequestHandler,
    result: tuple[dict, int],
) -> None:
    payload, status = result
    handler.send_json(payload, status)


def handle_ai_config_save(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.save_ai_config_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_mercadolibre_auth_link(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.mercadolibre_auth_link_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_mercadolibre_auth_checklist(handler: JsonRequestHandler) -> None:
    _send(handler, auth_config_facade.mercadolibre_auth_checklist_payload())


def handle_open_auth_link(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.open_auth_link_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_mercadolibre_exchange_code(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.exchange_mercadolibre_code_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_mercadolibre_refresh_token(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.refresh_mercadolibre_token_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_mercadolibre_real_auth_test(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.run_mercadolibre_auth_test_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_test_store_auth(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.test_store_auth_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_test_api_config(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.test_api_config_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


def handle_save_settings(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.save_settings_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path),
            approval_token=_approval_token(handler),
        ),
    )


def handle_clear_store_auth(handler: JsonRequestHandler) -> None:
    _send(
        handler,
        auth_config_facade.clear_store_auth_payload(
            validate_request_payload(handler.read_body(), endpoint=handler.path)
        ),
    )


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/ai-config/save": handle_ai_config_save,
    "/api/mercadolibre/auth-link": handle_mercadolibre_auth_link,
    "/api/mercadolibre/auth-checklist": handle_mercadolibre_auth_checklist,
    "/api/open-auth-link": handle_open_auth_link,
    "/api/mercadolibre/exchange-code": handle_mercadolibre_exchange_code,
    "/api/mercadolibre/refresh-token": handle_mercadolibre_refresh_token,
    "/api/mercadolibre/real-auth-test": handle_mercadolibre_real_auth_test,
    "/api/test-store-auth": handle_test_store_auth,
    "/api/test-api-config": handle_test_api_config,
    "/api/save-settings": handle_save_settings,
    "/api/store-auth/clear": handle_clear_store_auth,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
