"""ERP 与固定 New API 服务之间的公开形状；不包含模型 Key 或登录 Cookie。"""
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

NewAPITarget = Literal["keys", "wallet", "usage", "proof"]


class NewAPIFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    flow_id: Annotated[str, StringConstraints(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    challenge: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    target: NewAPITarget
    expires_at: Annotated[StrictInt, Field(gt=0)]


class NewAPIConnectionInfo(TypedDict):
    ok: bool
    configured: bool
    url: str
    sync_pending: bool


class NewAPIAuthorizationPreview(TypedDict):
    ok: bool
    code: str
    target: NewAPITarget
    expires_at: int


class NewAPILaunch(TypedDict):
    ok: bool
    url: str
    ticket: str
    expires_at: int
