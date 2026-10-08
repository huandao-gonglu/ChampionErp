"""授权页中断状态及恢复的公开投影，不包含凭据和原始响应。"""
from typing import Literal

from pydantic import BaseModel, Field


class RequestBlockOccurrence(BaseModel):
    id: str
    created_at: float
    blocked_count: int


class RequestBlockView(BaseModel):
    id: str
    platform: str
    account: str
    scope: str
    interface: str
    code: str
    message: str
    http_status: int = 0
    created_at: float
    last_created_at: float
    count: int
    occurrences: list[RequestBlockOccurrence]
    blocked_count: int
    resume_at: float
    recovery_mode: Literal['probe', 'confirm', 'confirm_request', 'waiting', 'verify_result']


class RequestInterruptionView(BaseModel):
    id: str
    platform: str
    interface: str
    trigger: str
    created_at: float
    semantics: Literal['read', 'write']
    code: str
    message: str
    local_rejection: bool


class RequestRejectionNotice(BaseModel):
    id: str
    operation_id: str
    created_at: float
    platform: str
    code: str
    message: str


class RequestControlStatus(BaseModel):
    ok: bool = True
    server_time: float
    blocks: list[RequestBlockView]
    history: list[RequestInterruptionView]
    total: int
    notices: list[RequestRejectionNotice] = Field(default_factory=list)
