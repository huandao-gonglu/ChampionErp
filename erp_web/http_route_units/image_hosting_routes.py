"""图片托管薄 HTTP 入口。"""
from erp_web.facades import image_hosting_facade
from erp_web.schemas.requests import validate_request_payload
from .common import JsonRequestHandler


def handle_save(handler: JsonRequestHandler) -> None:
    result, status = image_hosting_facade.mutate_image_hosting(validate_request_payload(handler.read_body(), endpoint=handler.path), "save")
    handler.send_json(result, status)


def handle_default(handler: JsonRequestHandler) -> None:
    result, status = image_hosting_facade.mutate_image_hosting(validate_request_payload(handler.read_body(), endpoint=handler.path), "default")
    handler.send_json(result, status)


def handle_delete(handler: JsonRequestHandler) -> None:
    result, status = image_hosting_facade.mutate_image_hosting(validate_request_payload(handler.read_body(), endpoint=handler.path), "delete")
    handler.send_json(result, status)


def handle_test(handler: JsonRequestHandler) -> None:
    result, status = image_hosting_facade.test_image_hosting(validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


POST_HANDLERS = {"/api/image-hosting/save": handle_save, "/api/image-hosting/default": handle_default,
                 "/api/image-hosting/delete": handle_delete, "/api/image-hosting/test": handle_test}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset({"/api/image-hosting"})


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route = POST_HANDLERS.get(parsed.path)
    if route is None:
        return False
    route(handler)
    return True


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path not in GET_API_ROUTES:
        return False
    result, status = image_hosting_facade.list_image_hosting()
    handler.send_json(result, status)
    return True
