"""1688 授权状态的公开契约，不含应用密钥或买家 Token。"""
from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field
from erp_web.schemas.managed_settings import AlibabaApplicationType

AuthorizationAttempt = Literal["idle", "waiting", "expired", "failed"]


class OneBoundAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    session: str = Field(min_length=1, max_length=4096)


class AlibabaAuthorizationStatus(TypedDict):
    ok: bool
    application_type: AlibabaApplicationType
    application_configured: bool
    can_authorize: bool
    authorized: bool
    member_id: str
    account_name: str
    attempt: AuthorizationAttempt
    expires_at: float
    refreshable: bool
    needs_reauthorization: bool
