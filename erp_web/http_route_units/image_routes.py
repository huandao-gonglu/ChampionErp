from __future__ import annotations

from erp_web.schemas.requests import validate_request_payload

from ..facades import image_facade
from .common import JsonRequestHandler
from .manual_ai import send_manual_ai_result


IMAGE_POST_PATHS = frozenset(image_facade.IMAGE_PAYLOAD_HANDLERS)

_AI_ACTIONS = {
    "/api/image-edit": "image_edit",
    "/api/image-translate": "image_translate",
    "/api/draft-images/plan-set": "image_plan",
    "/api/draft-images/generate-set": "image_set",
    "/api/draft-images/process": lambda body: "image_edit" if body["operation"] == "edit" else "image_translate",
    "/api/image-processing/continue": "image_continue",
}


def handle_post(
    handler: JsonRequestHandler,
    path: str,
) -> bool:
    if path not in IMAGE_POST_PATHS:
        return False
    if action := _AI_ACTIONS.get(path):
        send_manual_ai_result(handler, action, image_facade.IMAGE_PAYLOAD_HANDLERS[path], endpoint=path)
        return True
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
