"""1688 授权状态的公开契约，不含应用密钥或买家 Token。"""
from typing import Literal, NotRequired, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from erp_web.schemas.managed_settings import AlibabaApplicationType

AuthorizationAttempt = Literal["idle", "waiting", "expired", "failed"]


class OneBoundAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    access_token: str | None = Field(default=None, min_length=1, max_length=4096)
    session: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode='after')
    def one_credential(self):
        if bool(self.access_token) == bool(self.session):
            raise ValueError('请只提供一种万邦买家凭据')
        return self


class AlibabaAuthorizationStatus(TypedDict):
    ok: bool
    application_type: AlibabaApplicationType
    active: bool
    application_configured: bool
    can_authorize: bool
    authorized: bool
    member_id: str
    account_name: str
    attempt: AuthorizationAttempt
    expires_at: float
    refreshable: bool
    needs_reauthorization: bool
    binding_pending: NotRequired[bool]


class OneBoundShopBinding(TypedDict):
    id: str
    nick: str
    status: str
    expires_at: str
    selectable: bool


class OneBoundShopBindingPage(TypedDict):
    ok: bool
    items: list[OneBoundShopBinding]
    page: int
    total: int
    page_count: int



class OneBoundUserBindingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=200)
    binding_id: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=1, le=999999999)


class OneBoundUserBinding(TypedDict):
    binding_id: str
    account_name: str
    state: Literal['unbound', 'connected', 'pending', 'expired']
