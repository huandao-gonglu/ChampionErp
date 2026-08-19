from __future__ import annotations

"""商品/草稿保存、读取与删除的 Capability 契约。

删除属于破坏性写入：审批摘要与规范化参数由服务端快照函数生成，digest
绑定冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照复核，
模型既不能提供审批 payload，也不能在批准后篡改受保护参数。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_product(self) -> "ProductSaveRequest":
        if not self.product:
            raise ValueError("product 不能为空")
        return self


class ProductSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product: dict[str, JsonValue] = Field(default_factory=dict)


class ProductDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class ProductDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deleted: int = Field(default=0, ge=0)
    deleted_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()


class DraftReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class DraftReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: dict[str, JsonValue] = Field(default_factory=dict)
    product_context: dict[str, JsonValue] = Field(default_factory=dict)


class DraftSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_draft(self) -> "DraftSaveRequest":
        if not self.draft:
            raise ValueError("draft 不能为空")
        return self


class DraftSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: dict[str, JsonValue] = Field(default_factory=dict)
    product_context: dict[str, JsonValue] = Field(default_factory=dict)


class DraftDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class DraftDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deleted: int = Field(default=0, ge=0)
    deleted_ids: tuple[str, ...] = ()
    missing_ids: tuple[str, ...] = ()
    affected_product_ids: tuple[str, ...] = ()


__all__ = [
    "DraftDeleteRequest",
    "DraftDeleteResult",
    "DraftReadRequest",
    "DraftReadResult",
    "DraftSaveRequest",
    "DraftSaveResult",
    "ProductDeleteRequest",
    "ProductDeleteResult",
    "ProductSaveRequest",
    "ProductSaveResult",
]
