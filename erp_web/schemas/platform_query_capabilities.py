from __future__ import annotations

"""平台商品/订单与发布队列的只读查询 Capability 契约。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductsIndexQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    positions: tuple[Annotated[int, Field(ge=1)], ...] = ()


class ProductsIndexQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[dict[str, JsonValue], ...] = ()
    count: int = Field(default=0, ge=0)
    snapshot_id: TrimmedText
    selected_items: tuple[dict[str, JsonValue], ...] = ()






class PlatformOrdersQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    state: Literal["", "pending_shipment", "processing", "shipped", "delivered", "cancelled", "unknown"] = ""
    q: Annotated[TrimmedText, StringConstraints(max_length=200)] = ""
    article_numbers: tuple[Annotated[TrimmedText, StringConstraints(min_length=1, max_length=100)], ...] = Field(default=(), max_length=20)
    include_notifications: bool = False

    @model_validator(mode="after")
    def one_search_mode(self):
        if self.q and self.article_numbers:
            raise ValueError("关键词 q 与批量货号 article_numbers 请选择一种查询方式")
        return self


class OrderQueryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["local_snapshot"] = "local_snapshot"
    queried_at: str = ""
    realtime_verified: Literal[False] = False
    platform_history_complete: Literal[False] = False
    platforms: tuple[str, ...] = ()
    unavailable_platforms: tuple[str, ...] = ()
    scanned_orders: int = 0
    scanned_lines: int = 0
    unresolved_source_lines: int = 0
    ambiguous_source_lines: int = 0
    oldest_snapshot_at: str = ""
    newest_snapshot_at: str = ""
    has_more: bool = False


class PlatformOrdersQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    items: tuple[dict[str, JsonValue], ...] = ()
    notifications: tuple[dict[str, JsonValue], ...] = ()
    pagination: dict[str, JsonValue] = Field(default_factory=dict)
    sync_status: tuple[dict[str, JsonValue], ...] = ()
    coverage: OrderQueryCoverage = Field(default_factory=OrderQueryCoverage)


class PublishLogsQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=200, ge=1, le=1000)


class PublishLogsQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[dict[str, JsonValue], ...] = ()
    count: int = Field(default=0, ge=0)


class PublishJobsQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=50, ge=1, le=100)
    cursor: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    status: Annotated[TrimmedText, StringConstraints(max_length=40)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""


class PublishJobsQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    jobs: tuple[dict[str, JsonValue], ...] = ()
    next_cursor: TrimmedText = ""
    count: int = Field(default=0, ge=0)


class PublishJobStatusQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]


class PublishJobStatusQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "OrderQueryCoverage",
    "PlatformOrdersQueryRequest",
    "PlatformOrdersQueryResult",
    "ProductsIndexQueryRequest",
    "ProductsIndexQueryResult",
    "PublishJobStatusQueryRequest",
    "PublishJobStatusQueryResult",
    "PublishJobsQueryRequest",
    "PublishJobsQueryResult",
    "PublishLogsQueryRequest",
    "PublishLogsQueryResult",
]
