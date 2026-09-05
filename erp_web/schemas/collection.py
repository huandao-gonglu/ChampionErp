"""采集等待人工验证的契约，不包含凭据或浏览器连接地址。"""
from typing import Literal, TypedDict


class CollectionVerification(TypedDict):
    browser_tab_id: str
    source_url: str
    platform: str


class CollectionVerificationStatus(TypedDict):
    ok: bool
    status: Literal["waiting_verification", "loading", "ready", "unavailable"]
    message: str

