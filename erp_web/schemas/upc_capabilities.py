from __future__ import annotations

"""UPC 分配/导入的 Capability 契约。"""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
)


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class UpcAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class UpcAssignResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    upc: TrimmedText
    upc_pool: dict[str, JsonValue] = Field(default_factory=dict)


class UpcImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    values: Annotated[tuple[str, ...], Field(min_length=1)]


class UpcImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    imported: int = Field(default=0, ge=0)
    upc_pool: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "UpcAssignRequest",
    "UpcAssignResult",
    "UpcImportRequest",
    "UpcImportResult",
]
