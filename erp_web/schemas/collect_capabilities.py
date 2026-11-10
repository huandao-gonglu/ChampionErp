from __future__ import annotations

"""采集、认领与商品研究的 Capability 契约。

浏览器采集使用桌面会话，1688 API 采集使用当前账号授权。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class SourceCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=2000)]
    mode: Literal["browser", "api"] = "browser"
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    claim_platforms: tuple[str, ...] = ()


class SourceCollectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    product_id: TrimmedText = ""
    product: dict[str, JsonValue] = Field(default_factory=dict)
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
    next_action: TrimmedText = ""
    message: TrimmedText = ""
    products_index: tuple[dict[str, JsonValue], ...] = ()


class CollectBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    urls: Annotated[tuple[str, ...], Field(min_length=1)]
    mode: Literal["browser", "api"] = "browser"
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    claim_platforms: tuple[str, ...] = ()


class CollectBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    total: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    partial_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    items: tuple[dict[str, JsonValue], ...] = ()
    products_index: tuple[dict[str, JsonValue], ...] = ()


class Collect1688Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=2000)]
    claim_platforms: tuple[str, ...] = ()


class Collect1688Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    product: dict[str, JsonValue] = Field(default_factory=dict)
    cleaned: dict[str, JsonValue] = Field(default_factory=dict)
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
    next_action: TrimmedText = ""
    message: TrimmedText = ""
    products_index: tuple[dict[str, JsonValue], ...] = ()


class ClaimMarket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: str
    site: str
    language: str


class ClaimProductsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    all_markets: bool = Field(default=False, description="用户要求所有平台和市场时设为 true，由服务端读取当前可选市场。")
    targets: tuple[ClaimMarket, ...] = ()

    @model_validator(mode="after")
    def validate_market_scope(self):
        if self.all_markets == bool(self.targets):
            raise ValueError("请选择全部市场或指定 targets，不能同时选择或同时省略。")
        return self


class ClaimProductsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    claimed_count: int = Field(default=0, ge=0)
    items: tuple[dict[str, JsonValue], ...] = ()
    products_index: tuple[dict[str, JsonValue], ...] = ()
    drafts_index: tuple[dict[str, JsonValue], ...] = ()


class ResearchHotProductsSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=200)]

    target_markets: tuple[str, ...] = ()
    limit: int = Field(default=0, ge=0, le=500)


class ResearchRunStatusQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""


class ResearchRunStatusQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    active: bool = False
    run: dict[str, JsonValue] = Field(default_factory=dict)
    items: tuple[dict[str, JsonValue], ...] = ()
    source_status: tuple[dict[str, JsonValue], ...] = ()
    description: TrimmedText = ""


__all__ = [
    "ClaimProductsRequest",
    "ClaimProductsResult",
    "Collect1688Request",
    "Collect1688Result",
    "CollectBatchRequest",
    "CollectBatchResult",
    "ResearchHotProductsSearchRequest",
    "ResearchRunStatusQueryRequest",
    "ResearchRunStatusQueryResult",
    "SourceCollectRequest",
    "SourceCollectResult",
]
