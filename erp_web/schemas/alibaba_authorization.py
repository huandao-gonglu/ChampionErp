"""1688 授权状态的公开契约，不含应用密钥或买家 Token。"""
from typing import Literal, TypedDict

AuthorizationAttempt = Literal["idle", "waiting", "expired", "failed"]


class AlibabaAuthorizationStatus(TypedDict):
    ok: bool
    application_configured: bool
    can_authorize: bool
    authorized: bool
    member_id: str
    account_name: str
    attempt: AuthorizationAttempt
    expires_at: float
    refreshable: bool
    needs_reauthorization: bool
