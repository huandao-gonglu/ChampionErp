from __future__ import annotations

"""确定性发布校验与确认后队列提交的领域契约。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductPublishValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""


class PublishValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Annotated[TrimmedText, StringConstraints(max_length=120)] = ""
    field: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    message: Annotated[TrimmedText, StringConstraints(max_length=1000)]
    severity: Literal["error", "warning"]
    next_action: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""


class ProductPublishSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)]
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    store_identity: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=255),
    ]
    store_label: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    title: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    listing_currency: Annotated[TrimmedText, StringConstraints(max_length=16)] = ""
    price: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    stock: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    image_count: int = Field(default=0, ge=0)


class ProductPublishValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    summary: ProductPublishSummary
    errors: list[PublishValidationIssue]
    warnings: list[PublishValidationIssue]
    validation_digest: Annotated[TrimmedText, StringConstraints(max_length=128)] = ""


class PublishRequestConfirmation(BaseModel):
    """由 Controller 从已持久化确认状态构造，不能来自模型参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    step_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    validation_digest: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=128),
    ]
    confirmed_at: datetime


class ProductPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    idempotency_key: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=255),
    ]
    confirmation: PublishRequestConfirmation


class ProductPublishRequestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)]
    status: Annotated[TrimmedText, StringConstraints(max_length=80)]
    idempotent_replay: bool = False


__all__ = [
    "ProductPublishRequest",
    "ProductPublishRequestResult",
    "ProductPublishSummary",
    "ProductPublishValidateRequest",
    "ProductPublishValidationResult",
    "PublishRequestConfirmation",
    "PublishValidationIssue",
]
