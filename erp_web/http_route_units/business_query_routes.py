"""从普通查询声明派生 GET 路由，无需逐功能编写页面或 AI 适配。"""
from functools import partial
from urllib.parse import parse_qs

from erp_web.facades import business_data_facade as facade


def build_get_handlers(paths):
    return {path: partial(facade.business_query_payload, name) for path, name in paths.items()}


GET_HANDLERS = build_get_handlers(facade.BUSINESS_QUERY_PATHS)
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_get(handler, parsed):
    action = GET_HANDLERS.get(parsed.path)
    if action is None:
        return False
    handler.send_json(*action(parse_qs(parsed.query, keep_blank_values=True)))
    return True
