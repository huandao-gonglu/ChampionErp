from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from .manual_ai import send_manual_ai_result
from ..facades import translation_facade


PostHandler = Callable[[JsonRequestHandler], None]


def handle_text_translate(handler: JsonRequestHandler) -> None:
    send_manual_ai_result(handler, "translation", translation_facade.text_translate_payload)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/text-translate": handle_text_translate,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
