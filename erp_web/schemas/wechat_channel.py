"""微信连接的界面契约；不包含任何通信凭据。"""
from typing import Literal, TypedDict, get_args


WechatForwardType = Literal["reply", "progress", "approval", "error", "command"]
WECHAT_FORWARD_TYPES: tuple[WechatForwardType, ...] = get_args(WechatForwardType)


class WechatChannelStatus(TypedDict):
    ok: bool
    bound: bool
    connected: bool
    status: str
    conversation_id: str
    qr_image: str
    error: str
    pending_deliveries: int
    forward_types: list[WechatForwardType]


__all__ = ["WechatChannelStatus", "WechatForwardType", "WECHAT_FORWARD_TYPES"]
