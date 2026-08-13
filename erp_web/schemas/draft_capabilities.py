from __future__ import annotations

"""草稿查询 Capability 与轻量查询快照的数据契约。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]
DraftQueryScope = Literal["active", "published", "all"]
DraftQueryView = Literal["summary", "workflow", "publish_readiness", "detail"]
DraftQuerySort = Literal["created_desc", "created_asc", "title_asc"]


class DraftQueryCriteria(BaseModel):
    """会写入快照、决定草稿集合与顺序的查询条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: DraftQueryScope = "active"
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    status: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    keyword: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    view: DraftQueryView = "summary"
    sort: DraftQuerySort = "created_desc"
    limit: int = Field(default=50, ge=1, le=100)


class DraftQueryRequest(DraftQueryCriteria):
    """新查询，或基于既有快照解析一到多个一基序号。"""

    snapshot_id: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    positions: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list,
        max_length=10,
    )


class DraftQuerySnapshot(BaseModel):
    """只保存稳定身份与查询条件，不复制草稿业务数据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=80),
    ]
    draft_ids: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]
    ] = Field(max_length=100)
    total: int = Field(ge=0)
    count_by_platform: dict[str, int]
    count_by_status: dict[str, int]
    query: DraftQueryCriteria
    created_at: datetime


class DraftPublishReadiness(BaseModel):
    """草稿当前已持久化的工作流/预检事实，不代替正式发布校验。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_status: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    publish_status: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    precheck_passed: bool = False
    image_count: int = Field(default=0, ge=0)
    attribute_count: int = Field(default=0, ge=0)
    validation_error_count: int = Field(default=0, ge=0)
    validation_warning_count: int = Field(default=0, ge=0)


class DraftSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    title: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    product_title: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    platforms: list[Annotated[TrimmedText, StringConstraints(max_length=80)]] = Field(
        default_factory=list,
        max_length=20,
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    language: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    category_path: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""
    has_description: bool = False
    created_at: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    updated_at: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    readiness: DraftPublishReadiness


class DraftQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0)
    items: list[DraftSummary] = Field(max_length=100)
    count_by_platform: dict[str, int]
    count_by_status: dict[str, int]
    snapshot_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=80),
    ]
    selected_items: list[DraftSummary] = Field(default_factory=list, max_length=10)


__all__ = [
    "DraftPublishReadiness",
    "DraftQueryCriteria",
    "DraftQueryRequest",
    "DraftQueryResult",
    "DraftQueryScope",
    "DraftQuerySnapshot",
    "DraftQuerySort",
    "DraftQueryView",
    "DraftSummary",
]
