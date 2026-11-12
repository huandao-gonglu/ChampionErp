"""按当前授权店铺装配订单聊天，不注册后台同步或消息存储。"""

from erp_web.context import get_context
from erp_web.services.order_chat_service import OrderChatService


def create_service(context=None):
    context = context or get_context()
    return OrderChatService(
        context.order_procurement.store.order, context.config.load_store_config(),
        accounts=context.order_notifications.accounts(),
    )


def command(action, body, *, context=None):
    service = create_service(context)
    if action == "history":
        return service.history(body["order_id"])
    if action == "send":
        return service.send(body["order_id"], body["message"])
    raise ValueError("不支持的聊天操作")
