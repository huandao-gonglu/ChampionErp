"""Yandex 订单聊天：按需读取与发送，不另建消息缓存或后台拉取。"""

import time
from urllib.parse import urlsplit

from erp_web.marketplaces.yandex_http import request_yandex_json
from erp_web.schemas.order_chat import OrderChatHistory, OrderChatMessage, OrderChatSent
from erp_web.schemas.orders import account_in_scope, configured_accounts
from erp_web.services.external_request_context import request_operation


def _identifier(value):
    text = str(value or "")
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        raise ValueError("Yandex 聊天或订单标识无效")
    return text


class OrderChatService:
    def __init__(self, order_reader, config, *, accounts=None, request=request_yandex_json):
        self.order_reader = order_reader
        self.config = config
        self.request = request
        self.accounts = accounts

    def _target(self, order_id):
        store = self.config.get("yandex") or {}
        accounts = self.accounts if self.accounts is not None else configured_accounts(self.config)
        order = self.order_reader(order_id, accounts)
        if order.platform != "yandex" or not account_in_scope(accounts, "yandex", order.account_id):
            raise ValueError("订单不属于当前 Yandex 店铺")
        business = _identifier(store.get("business_id"))
        token = str(store.get("api_token") or "").strip()
        if not token:
            raise ValueError("请先配置 Yandex API-Key，并开通买家沟通权限")
        return order, f"/v2/businesses/{business}/chats", token

    def _call(self, path, token, body, **query):
        data = self.request("POST", path, token, body, query=query)
        if data.get("status") != "OK":
            raise ValueError("Yandex 未返回明确的成功回执，请获取最新消息核对后再操作")
        return data

    def approval_facts(self, order_id: str) -> dict:
        """仅核对本地接收对象与订单状态；不创建聊天，不读取或发送消息。"""
        order, _, _ = self._target(order_id)
        return {
            "order_id": order.id, "platform": order.platform, "account_id": order.account_id,
            "platform_order_id": order.order_id,
            "business_id": _identifier(self.config["yandex"].get("business_id")),
            "state": order.state, "status": order.status,
        }

    def _pages(self, path, token, body, key, **query):
        seen = set()
        while True:
            result = self._call(path, token, body, **query).get("result")
            if not isinstance(result, dict) or not isinstance(result.get(key), list):
                raise ValueError("Yandex 聊天响应不完整")
            yield result
            cursor = str((result.get("paging") or {}).get("nextPageToken") or "")
            if not cursor:
                return
            if cursor in seen or not result[key]:
                raise ValueError("Yandex 聊天分页未前进，请稍后重新获取")
            seen.add(cursor)
            query["pageToken"] = cursor

    @staticmethod
    def _check_context(context, order):
        if not isinstance(context, dict) or (
            context.get("type") != "ORDER"
            or str(context.get("orderId")) != order.order_id
            or str(context.get("campaignId")) != order.account_id
        ):
            raise ValueError("Yandex 聊天归属与当前订单不一致")

    def history(self, order_id: str) -> OrderChatHistory:
        order, path, token = self._target(order_id)
        with request_operation("order_chat.history", deadline_at=time.time() + 120):
            chats = {}
            query = {"contexts": [{"type": "ORDER", "id": int(_identifier(order.order_id))}], "types": ["CHAT"]}
            for page in self._pages(path, token, query, "chats", limit=20):
                for chat in page["chats"]:
                    if not isinstance(chat, dict):
                        raise ValueError("Yandex 聊天响应不完整")
                    self._check_context(chat.get("context"), order)
                    if chat.get("type") != "CHAT":
                        raise ValueError("Yandex 返回了非买家聊天")
                    chats[_identifier(chat.get("chatId"))] = chat
            if not chats:
                return {"ok": True, "chat_id": "", "messages": []}
            if len(chats) != 1:
                raise ValueError("该订单对应多个聊天，无法确定买家会话")
            chat_id = next(iter(chats))
            messages: dict[str, OrderChatMessage] = {}
            for page in self._pages(path + "/history", token, {}, "messages", chatId=chat_id, limit=100):
                self._check_context(page.get("context"), order)
                for row in page["messages"]:
                    if not isinstance(row, dict):
                        raise ValueError("Yandex 消息响应不完整")
                    message_id = _identifier(row.get("messageId"))
                    attachments = []
                    for item in row.get("payload") or []:
                        if not isinstance(item, dict):
                            raise ValueError("Yandex 消息附件响应不完整")
                        url = str(item.get("url") or "")
                        parsed = urlsplit(url)
                        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                            attachments.append({"name": str(item.get("name") or "附件"), "url": url})
                    messages[message_id] = {
                        "id": message_id, "sender": str(row.get("sender") or ""),
                        "created_at": str(row.get("createdAt") or ""),
                        "text": str(row.get("message") or ""), "attachments": attachments,
                    }
            return {
                "ok": True, "chat_id": chat_id,
                "messages": sorted(messages.values(), key=lambda row: int(row["id"])),
            }

    def send(self, order_id: str, message: str) -> OrderChatSent:
        if not isinstance(message, str) or not message.strip() or len(message) > 4096:
            raise ValueError("消息不能为空，且不能超过 4096 个字符")
        order, path, token = self._target(order_id)
        with request_operation("order_chat.send", deadline_at=time.time() + 90):
            data = self._call(path + "/new", token, {"context": {"type": "ORDER", "id": int(_identifier(order.order_id))}})
            chat_id = _identifier((data.get("result") or {}).get("chatId"))
            self._call(path + "/message", token, {"message": message}, chatId=chat_id)
        return {"ok": True, "chat_id": chat_id}
