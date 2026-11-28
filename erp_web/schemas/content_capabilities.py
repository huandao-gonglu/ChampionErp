from __future__ import annotations

"""文案生成、批量文案、图片提示词与文本翻译的 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class CopyGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    language: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = "rewrite"

    @model_validator(mode="after")
    def require_subject(self) -> "CopyGenerateRequest":
        if not self.product_id and not self.draft_id:
            raise ValueError("product_id 与 draft_id 至少填写一个")
        return self


class CopyGenerateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText = ""
    draft_id: TrimmedText = ""
    platform: TrimmedText = ""
    target_market: TrimmedText = ""
    language: TrimmedText = ""
    mode: TrimmedText = ""
    copy_record: dict[str, JsonValue] = Field(default_factory=dict)
    listing: dict[str, JsonValue] = Field(default_factory=dict)
    updated_at: TrimmedText = ""


class CopyGenerateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_ids: tuple[str, ...] = Field(
        default=(),
        description="商品 ID 列表（不可传 draft_id）；与 draft_ids 二选一。",
    )
    draft_ids: tuple[str, ...] = Field(
        default=(),
        description="草稿 ID 列表（不可传 product_id）；与 product_ids 二选一，未指定 platform 时自动采用各草稿所属平台。",
    )
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    language: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""
    mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = "rewrite"

    @model_validator(mode="after")
    def require_targets(self) -> "CopyGenerateBatchRequest":
        has_products = any(str(item or "").strip() for item in self.product_ids)
        has_drafts = any(str(item or "").strip() for item in self.draft_ids)
        if has_products and has_drafts:
            raise ValueError("product_ids 与 draft_ids 请二选一，不要同时传入")
        if not has_products and not has_drafts:
            raise ValueError("product_ids 与 draft_ids 至少填写一个非空列表")
        return self


class CopyGenerateBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    items: tuple[dict[str, JsonValue], ...] = ()


class ImagePromptsGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    selected_image_ids: tuple[str, ...] = ()
    include_description: bool = True
    target_language: Annotated[TrimmedText, StringConstraints(max_length=80)] = ""


class ImagePromptsGenerateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: TrimmedText = ""
    selected_image_ids: tuple[str, ...] = ()


class TextTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_language: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=80),
    ]
    content: dict[str, JsonValue] = Field(default_factory=dict)
    preserve_terms: tuple[
        Annotated[TrimmedText, StringConstraints(min_length=1, max_length=500)],
        ...,
    ] = Field(
        default=(),
        max_length=100,
        description="翻译结果中必须逐字保留的品牌、型号、标识符或技术术语。",
    )

    @model_validator(mode="after")
    def require_content(self) -> "TextTranslateRequest":
        if not self.content:
            raise ValueError("content 不能为空")
        return self


class TextTranslateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_language: TrimmedText
    translations: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "CopyGenerateBatchRequest",
    "CopyGenerateBatchResult",
    "CopyGenerateRequest",
    "CopyGenerateResult",
    "ImagePromptsGenerateRequest",
    "ImagePromptsGenerateResult",
    "TextTranslateRequest",
    "TextTranslateResult",
]
