from __future__ import annotations

"""图片池维护与图片翻译/编辑的 Capability 契约。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


class ProductImageReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: TrimmedText = Field(min_length=1, max_length=160)
    asset_id: TrimmedText = Field(default="", max_length=200, description="留空列出商品图片，传入素材 ID 读取指定原图。")
    question: TrimmedText = Field(default="转录可辨认的原文，并描述图片主体。", max_length=2000)


class ProductImageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: str
    usage: str
    is_main: bool
    sku: str
    sku_ids: list[str] = Field(default_factory=list, description="将该素材用作默认图片的全部商品 SKU ID。")


class ProductImageReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: str
    images: list[ProductImageChoice]
    content: str = ""


class ChatImagesSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: TrimmedText = Field(min_length=1, max_length=160)
    attachment_ids: list[Annotated[TrimmedText, StringConstraints(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=4, description="从模型所见的「对话图片 ID」按用户指定顺序选取。")



class ChatImagesSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: str
    asset_ids: list[str]


class DraftImagesPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft_id: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=160)]
    decision: Literal["auto", "keep", "regenerate"] = Field(default="auto", description=(
        "完整准备默认 auto：未手动修改时生成共用主图及 SKU 套图；已处理则返回待确认。"
        "用户明确保留现图时用 keep；明确要求生成套图或确认重做时用 regenerate。不得代替用户确认。"))
    expected_fingerprint: str = Field(default="", max_length=64, description="keep/regenerate 须传最近读取的 image_preparation.fingerprint，防止确认期间图片变化。")
    count: int = Field(default=6, ge=2, le=8)
    prompt: str = Field(default="", max_length=4000)


class DraftImagesPrepareResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft_id: str
    status: Literal["needs_confirmation", "kept", "generated", "failed", "needs_selection"]
    fingerprint: str
    message: str
    batch_id: str = ""
    generated_count: int = 0
    failed_count: int = 0


class ImagePoolUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    uploads: Annotated[tuple[dict[str, JsonValue], ...], Field(min_length=1)]


class ImagePoolUploadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    uploaded_count: int = Field(default=0, ge=0)
    image_pool: tuple[dict[str, JsonValue], ...] = ()


class ImagePoolSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    image_pool: tuple[dict[str, JsonValue], ...] = ()


class ImagePoolSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    image_pool: tuple[dict[str, JsonValue], ...] = ()


class ImagePoolActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    action: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=40)]
    params: dict[str, JsonValue] = Field(default_factory=dict)


class ImagePoolActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    action: TrimmedText
    persisted: bool = False
    image_pool: tuple[dict[str, JsonValue], ...] = ()


class ImagePoolSyncGeneratedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]


class ImagePoolSyncGeneratedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    image_pool: tuple[dict[str, JsonValue], ...] = ()


class ImageTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    target_language: Annotated[TrimmedText, StringConstraints(max_length=80)] = "es"
    source_image_ids: tuple[str, ...] = ()
    mode: Annotated[TrimmedText, StringConstraints(max_length=40)] = "translate"
    apply_to_draft: bool = False
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    draft_image_strategy: Annotated[TrimmedText, StringConstraints(max_length=40)] = (
        "append"
    )


class ImageEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: Annotated[
        TrimmedText,
        StringConstraints(min_length=1, max_length=160),
    ]
    prompt: Annotated[TrimmedText, StringConstraints(min_length=1, max_length=4000)]
    platform: Annotated[TrimmedText, StringConstraints(max_length=80)] = (
        "mercadolibre"
    )
    source_image_ids: tuple[str, ...] = Field(
        default=(),
        description="显式源图片 ID；省略时由服务端选择商品主图。",
    )
    set_as_main: bool = Field(
        default=False,
        description="编辑成功后是否把第一张生成图设为商品主图。",
    )
    apply_to_draft: bool = False
    draft_id: Annotated[TrimmedText, StringConstraints(max_length=160)] = ""
    draft_image_strategy: Annotated[TrimmedText, StringConstraints(max_length=40)] = (
        "append"
    )


class ImageGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: TrimmedText
    generated_count: int = Field(default=0, ge=0)
    image_pool_items: tuple[dict[str, JsonValue], ...] = ()
    draft_id: TrimmedText = ""
    main_image_id: TrimmedText = ""
    message: TrimmedText = ""


__all__ = [
    "ProductImageChoice",
    "ProductImageReadRequest",
    "ProductImageReadResult",
    "ChatImagesSaveRequest",
    "ChatImagesSaveResult",
    "DraftImagesPrepareRequest",
    "DraftImagesPrepareResult",
    "ImageEditRequest",
    "ImageGenerationResult",
    "ImagePoolActionRequest",
    "ImagePoolActionResult",
    "ImagePoolSaveRequest",
    "ImagePoolSaveResult",
    "ImagePoolSyncGeneratedRequest",
    "ImagePoolSyncGeneratedResult",
    "ImagePoolUploadRequest",
    "ImagePoolUploadResult",
    "ImageTranslateRequest",
]
