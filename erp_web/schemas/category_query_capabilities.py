from __future__ import annotations

"""类目搜索、属性定义/枚举查询与类目预检的 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class CategorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    query: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=500)]
    limit: int = Field(default=20, ge=1, le=50)


class CategorySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    query: TrimmedText = ""
    source: TrimmedText = ""
    results: tuple[dict[str, JsonValue], ...] = ()


class CategoryAttributesQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class CategoryAttributesQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    category_id: TrimmedText = ""
    category_path: TrimmedText = ""
    attributes: tuple[dict[str, JsonValue], ...] = ()


class CategoryAttributeValuesQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    attribute_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    query: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    limit: int = Field(default=50, ge=1, le=200)


class CategoryAttributeValuesQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    category_id: TrimmedText = ""
    attribute_id: TrimmedText = ""
    query: TrimmedText = ""
    values: tuple[dict[str, JsonValue], ...] = ()


class CategoryPrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    category_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]

    @model_validator(mode="after")
    def require_subject(self) -> "CategoryPrecheckRequest":
        if not self.draft_id and not self.product_id:
            raise ValueError("draft_id 与 product_id 至少填写一个")
        return self


class CategoryPrecheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: TrimmedText
    site: TrimmedText = ""
    category_id: TrimmedText = ""
    category_path: TrimmedText = ""
    missing_fields: tuple[str, ...] = ()


__all__ = [
    "CategoryAttributeValuesQueryRequest",
    "CategoryAttributeValuesQueryResult",
    "CategoryAttributesQueryRequest",
    "CategoryAttributesQueryResult",
    "CategoryPrecheckRequest",
    "CategoryPrecheckResult",
    "CategorySearchRequest",
    "CategorySearchResult",
]
