from __future__ import annotations

"""平台商品/订单与发布队列的只读查询 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


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


class MercadoLibreUserProductsQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    status: Annotated[TrimmedText, StringConstraints(max_length=40)] = "all"
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=100)


class MercadoLibreUserProductsQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    status: TrimmedText = ""
    items: tuple[dict[str, JsonValue], ...] = ()
    pagination: dict[str, JsonValue] = Field(default_factory=dict)
    refresh_errors: tuple[dict[str, JsonValue], ...] = ()
    checked_at: TrimmedText = ""


class PlatformOrdersQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PlatformOrdersQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    items: tuple[dict[str, JsonValue], ...] = ()
    notifications: tuple[dict[str, JsonValue], ...] = ()
    pagination: dict[str, JsonValue] = Field(default_factory=dict)
    checked_at: TrimmedText = ""


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
    "PlatformOrdersQueryRequest",
    "PlatformOrdersQueryResult",
    "MercadoLibreUserProductsQueryRequest",
    "MercadoLibreUserProductsQueryResult",
    "ProductsIndexQueryRequest",
    "ProductsIndexQueryResult",
    "PublishJobStatusQueryRequest",
    "PublishJobStatusQueryResult",
    "PublishJobsQueryRequest",
    "PublishJobsQueryResult",
    "PublishLogsQueryRequest",
    "PublishLogsQueryResult",
]
