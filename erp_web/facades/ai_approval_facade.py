"""对话框审批偏好的受信 UI 写入口；不暴露为模型工具。"""

from erp_web.context import get_context
from erp_web.schemas.ai_approval import normalize_ai_tool_approval_mode
from erp_web.services.approval_session import ApprovalSessionError


def save_approval_mode_payload(body: dict, *, approval_token: str = "") -> tuple[dict, int]:
    context = get_context()
    try:
        context.approval_session.require_approver(approval_token)
        if set(body) != {"mode"}:
            raise ValueError("审批偏好请求只能包含 mode。")
        mode = normalize_ai_tool_approval_mode(body["mode"])
        with context.chat_runs.input_guard():
            saved = context.config.save_ai_tool_approval_mode(mode)
        return {"ok": True, "mode": saved}, 200
    except ApprovalSessionError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, 403
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "AI_APPROVAL_MODE_INVALID"}, 400


__all__ = ["save_approval_mode_payload"]
