"""采集验证与商品推送的边界契约，不包含凭据或浏览器连接地址。"""
from typing import Literal, TypedDict


class DraftClaimTarget(TypedDict):
    """用户勾选的销售市场，语言必须与市场注册表一致。"""

    platform: str
    site: str
    language: str


class CollectionVerification(TypedDict):
    browser_tab_id: str
    source_url: str
    platform: str


class CollectionVerificationStatus(TypedDict):
    ok: bool
    status: Literal["waiting_verification", "loading", "ready", "unavailable"]
    message: str
