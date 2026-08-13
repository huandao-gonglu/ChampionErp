from __future__ import annotations

"""商品读取、属性设置和草稿图片准备的类型化契约。"""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""

    @model_validator(mode="after")
    def require_stable_identity(self) -> "ProductReadRequest":
        if not self.product_id and not self.draft_id:
            raise ValueError("product_id 与 draft_id 至少填写一个")
        return self


class ProductFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    name: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    brand: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    model: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    sku: Annotated[TrimmedText, StringConstraints(max_length=255)] = ""
    stock: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    cost: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    source_platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    source_url: Annotated[TrimmedText, StringConstraints(max_length=2000)] = ""
    source_image_count: int = Field(default=0, ge=0)
    workflow_statuses: dict[str, str] = Field(default_factory=dict)


class ProductDraftFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    language: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    workflow_status: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    publish_status: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    title: Annotated[TrimmedText, StringConstraints(max_length=500)] = ""
    has_description: bool = False
    category_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    category_path: Annotated[TrimmedText, StringConstraints(max_length=1000)] = ""
    attribute_ids: list[str] = Field(default_factory=list, max_length=500)
    image_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    listing_currency: Annotated[TrimmedText, StringConstraints(max_length=16)] = ""
    price: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    stock: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""


class ProductReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product: ProductFacts
    draft: ProductDraftFacts | None = None


class ProductAttributesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    updates: dict[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)],
        JsonValue,
    ] = Field(min_length=1, max_length=200)


class ProductAttributesUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)]
    site: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    attributes: dict[str, JsonValue]
    changed_keys: list[str] = Field(max_length=200)
    changed: bool


class ProductImagesPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    asset_ids: list[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]
    ] = Field(default_factory=list, max_length=100)


class ProductImagesPrepareResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)]
    image_asset_ids: list[str] = Field(max_length=100)
    image_count: int = Field(ge=1)
    changed: bool


__all__ = [
    "ProductAttributesUpdateRequest",
    "ProductAttributesUpdateResult",
    "ProductDraftFacts",
    "ProductFacts",
    "ProductImagesPrepareRequest",
    "ProductImagesPrepareResult",
    "ProductReadRequest",
    "ProductReadResult",
]
