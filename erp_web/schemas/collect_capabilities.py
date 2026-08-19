from __future__ import annotations

"""采集、认领与商品研究的 Capability 契约。

采集类 Capability 不接受模型提供的 Cookie / API 密钥；凭据一律来自已保存
配置（由 Scope provider 在可信边界内解析）。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class SourceCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=2000)]
    mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = "browser"
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
    mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = "browser"
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


class CollectFromBrowserTabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_url: Annotated[TrimmedText, StringConstraints(max_length=2000)] = ""
    product_url: Annotated[TrimmedText, StringConstraints(max_length=2000)] = ""
    platform_hint: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    claim_platforms: tuple[str, ...] = ()
    save_only: bool = False

    @model_validator(mode="after")
    def require_target(self) -> "CollectFromBrowserTabRequest":
        if not self.tab_url and not self.product_url:
            raise ValueError("tab_url 与 product_url 至少填写一个")
        return self


class CollectFromBrowserTabResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    product: dict[str, JsonValue] = Field(default_factory=dict)
    image_pool: tuple[dict[str, JsonValue], ...] = ()
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
    browser_status: dict[str, JsonValue] = Field(default_factory=dict)
    next_action: TrimmedText = ""
    products_index: tuple[dict[str, JsonValue], ...] = ()


class Collect1688Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: Annotated[TrimmedText, StringConstraints(max_length=2000)] = ""
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200000)] = ""
    save: bool = True
    claim_platforms: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_subject(self) -> "Collect1688Request":
        if not self.url and not self.text:
            raise ValueError("url 与 text 至少填写一个")
        return self


class Collect1688Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    product: dict[str, JsonValue] = Field(default_factory=dict)
    cleaned: dict[str, JsonValue] = Field(default_factory=dict)
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
    next_action: TrimmedText = ""
    message: TrimmedText = ""
    products_index: tuple[dict[str, JsonValue], ...] = ()


class Collect1688CleanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200000)]
    url: Annotated[TrimmedText, StringConstraints(max_length=2000)] = ""


class Collect1688CleanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    cleaned: dict[str, JsonValue] = Field(default_factory=dict)


class ClaimProductsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    platforms: tuple[str, ...] = ()


class ClaimProductsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = False
    claimed_count: int = Field(default=0, ge=0)
    items: tuple[dict[str, JsonValue], ...] = ()
    products_index: tuple[dict[str, JsonValue], ...] = ()
    drafts_index: tuple[dict[str, JsonValue], ...] = ()


class ResearchHotProductsSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
    "Collect1688CleanRequest",
    "Collect1688CleanResult",
    "Collect1688Request",
    "Collect1688Result",
    "CollectBatchRequest",
    "CollectBatchResult",
    "CollectFromBrowserTabRequest",
    "CollectFromBrowserTabResult",
    "ResearchHotProductsSearchRequest",
    "ResearchRunStatusQueryRequest",
    "ResearchRunStatusQueryResult",
    "SourceCollectRequest",
    "SourceCollectResult",
]
