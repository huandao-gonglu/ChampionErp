from urllib.parse import parse_qs

from erp_web.facades import task_queue_facade
from erp_web.schemas.requests import validate_request_payload


def handle_check(handler):
    try:
        handler.send_json(task_queue_facade.check(validate_request_payload(handler.read_body(), endpoint=handler.path)))
    except ValueError as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


POST_HANDLERS = {"/api/tasks/check": handle_check}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset({"/api/tasks"})


def handle_get(handler, parsed):
    if parsed.path not in GET_API_ROUTES:
        return False
    try:
        handler.send_json(task_queue_facade.read({key: values[0] for key, values in parse_qs(parsed.query).items()}))
    except ValueError as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True
