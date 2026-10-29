"""受信设置界面的微信连接入口；不暴露凭据，不装配额外 Agent。"""
from erp_web.context import get_context
from erp_web.services.chat_conversation_service import ChatConversationError


def status_payload():
    return get_context().wechat.status()


def command_payload(command, body):
    service = get_context().wechat
    try:
        if command == "login":
            service.begin_login()
        elif command == "verify":
            service.verify(body["code"])
        elif command == "unbind":
            service.unbind()
        elif command == "settings":
            service.save_forward_types(body.get("forward_types"))
        else:
            raise ValueError("未知的微信设置操作。")
        return service.status(), 200
    except ChatConversationError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, exc.status_code
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400


__all__ = ["status_payload", "command_payload"]
