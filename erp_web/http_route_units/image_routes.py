from __future__ import annotations

from erp_web.schemas.requests import validate_request_payload

from ..facades import image_facade
from .common import JsonRequestHandler


IMAGE_POST_PATHS = frozenset(image_facade.IMAGE_PAYLOAD_HANDLERS)


def handle_post(
    handler: JsonRequestHandler,
    path: str,
) -> bool:
    if path not in IMAGE_POST_PATHS:
        return False
    result = image_facade.handle_image_payload(
        path,
        validate_request_payload(handler.read_body(), endpoint=path),
    )
    if result is None:
        return False
    payload, status = result
    handler.send_json(payload, status)
    return True


__all__ = ["IMAGE_POST_PATHS", "handle_post"]
