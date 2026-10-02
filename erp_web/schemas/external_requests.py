"""外部请求的控制契约；不包含凭据或平台业务数据。"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Literal
import math
from uuid import uuid4

from erp_web.schemas.platform_errors import PublishAdapterError


@dataclass(frozen=True)
class RequestContext:
    platform: str
    account_id: str
    interface: str
    source: str
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    trigger: str = "manual"
    credential_id: str = ""
    semantics: Literal["read", "write"] = "write"
    timeout: float = 30
    cancel: Event | None = field(default=None, repr=False, compare=False)
    quota_key: str = ""
    fingerprint: str = ""
    deadline_at: float | None = None
    cancellation_check: Callable[[], None] | None = field(default=None, repr=False, compare=False)
    max_attempts: int = 1
    retry_delay: float = 1


    def __post_init__(self):
        for key in ("platform", "account_id", "interface", "source", "operation_id"):
            value = getattr(self,key)
            if not isinstance(value,str) or not value or len(value) > 512 or "\n" in value:
                raise ValueError(f"外部请求 {key} 必须是有界的非空标识")
        if not math.isfinite(self.timeout) or self.timeout <= 0 or not 1 <= self.max_attempts <= 3 or self.retry_delay < 0:
            raise ValueError("请求时间与次数预算无效")


@dataclass(frozen=True)
class RequestFailure:
    code: str
    message: str
    scope: str = ""
    resume_at: float | None = None
    status: int = 0
    retryable: bool = False


class ExternalRequestBlocked(PublishAdapterError):
    def __init__(self, failure: RequestFailure, *, sent: bool = False):
        unknown = failure.code == "EXTERNAL_WRITE_OUTCOME_UNKNOWN"
        super().__init__(failure.code, failure.message, retryable=False, details={
            "http_status": failure.status, "scope": failure.scope,
            "resume_at": failure.resume_at, "local_rejection": not sent,
            "definitively_rejected": not sent and not unknown,
            **({"outcome_unknown": True, "remote_write_dispatched": True} if unknown else {}),
        })


class ExternalRequestNotSent(ExternalRequestBlocked):
    """传输层确认尚未发送 HTTP 请求；只携带脱敏后的固定原因。"""

    def __init__(self, failure: RequestFailure):
        super().__init__(failure, sent=False)
        self.failure = failure


class ExternalRequestOutcomeUnknown(PublishAdapterError):
    def __init__(self, *, http_status=0):
        super().__init__(
            "EXTERNAL_WRITE_OUTCOME_UNKNOWN",
            "外部写入已进入发送阶段，但未取得明确结果；请查询原业务回执，禁止直接重放",
            retryable=False,
            details={"http_status": http_status, "outcome_unknown": True, "remote_write_dispatched": True},
        )
