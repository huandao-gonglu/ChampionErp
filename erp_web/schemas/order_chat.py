"""订单买家聊天的瞬时响应契约；消息由平台保存。"""

from typing import TypedDict


class OrderChatAttachment(TypedDict):
    name: str
    url: str


class OrderChatMessage(TypedDict):
    id: str
    sender: str
    created_at: str
    text: str
    attachments: list[OrderChatAttachment]


class OrderChatHistory(TypedDict):
    ok: bool
    chat_id: str
    messages: list[OrderChatMessage]


class OrderChatSent(TypedDict):
    ok: bool
    chat_id: str
