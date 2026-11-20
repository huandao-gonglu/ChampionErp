"""只给显式接入的页面 AI 操作绑定普通对话。"""

from erp_web.context import get_context
from erp_web.schemas.requests import validate_request_payload
from erp_web.services.manual_ai_action import execute_manual_ai_action


def send_manual_ai_result(handler, action, execute, *, endpoint=None):
    body = validate_request_payload(handler.read_body(), endpoint=endpoint or handler.path)
    headers = getattr(handler, "headers", None)
    conversation_id = str(headers.get("X-AI-Conversation-ID", "") if headers is not None else "").strip()
    if conversation_id:
        action_name = action(body) if callable(action) else action
        result, status = execute_manual_ai_action(get_context(), conversation_id, action_name, body, execute)
    else:
        result, status = execute(body)
    handler.send_json(result, status)
