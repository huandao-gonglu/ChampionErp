"""核价参数模板与独立汇率查询的薄 HTTP 入口。"""
from urllib.parse import parse_qs

from erp_web.facades import pricing_workspace_facade as facade
from erp_web.schemas.requests import validate_request_payload
from .common import JsonRequestHandler


def handle_save(handler: JsonRequestHandler) -> None:
    result, status = facade.mutate_pricing_template_payload(validate_request_payload(handler.read_body(), endpoint=handler.path), "save")
    handler.send_json(result, status)


def handle_default(handler: JsonRequestHandler) -> None:
    result, status = facade.mutate_pricing_template_payload(validate_request_payload(handler.read_body(), endpoint=handler.path), "default")
    handler.send_json(result, status)


def handle_delete(handler: JsonRequestHandler) -> None:
    result, status = facade.mutate_pricing_template_payload(validate_request_payload(handler.read_body(), endpoint=handler.path), "delete")
    handler.send_json(result, status)


def handle_rates(handler: JsonRequestHandler, parsed: object) -> None:
    refresh = (parse_qs(parsed.query).get("refresh") or [""])[0] == "1"
    result, status = facade.pricing_exchange_rates_payload(refresh)
    handler.send_json(result, status)


def handle_warehouse_refresh(handler: JsonRequestHandler) -> None:
    validate_request_payload(handler.read_body(), endpoint=handler.path)
    handler.send_json(*facade.yandex_warehouses_payload(refresh=True))


def handle_warehouse_save(handler: JsonRequestHandler) -> None:
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    handler.send_json(*facade.yandex_warehouses_payload(body=body))


POST_HANDLERS = {"/api/yandex/warehouses/refresh": handle_warehouse_refresh, "/api/yandex/warehouses/logistics": handle_warehouse_save, "/api/pricing/templates/save": handle_save, "/api/pricing/templates/default": handle_default,
                 "/api/pricing/templates/delete": handle_delete}
GET_HANDLERS = {"/api/yandex/warehouses": lambda handler, parsed: handler.send_json(*facade.yandex_warehouses_payload()), "/api/pricing/templates": lambda handler, parsed: handler.send_json(*facade.pricing_templates_payload()),
                "/api/pricing/exchange-rates": handle_rates}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route = POST_HANDLERS.get(parsed.path)
    if route is None:
        return False
    route(handler)
    return True


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    route = GET_HANDLERS.get(parsed.path)
    if route is None:
        return False
    route(handler, parsed)
    return True
