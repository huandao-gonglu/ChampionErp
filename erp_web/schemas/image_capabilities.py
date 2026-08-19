from __future__ import annotations

"""图片池维护与图片翻译/编辑的 Capability 契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


TrimmedText = Annotated[str, StringConstraints(strip_whitespace=True)]


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
    source_image_ids: tuple[str, ...] = ()
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
    message: TrimmedText = ""


__all__ = [
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
