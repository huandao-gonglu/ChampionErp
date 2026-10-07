"""在线商品的离线 Ozon 模板导出契约，不参与平台发布或商品持久化。"""
from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OzonExportOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str | None = Field(default=None, max_length=500)
    price: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    barcode: str | None = Field(default=None, max_length=255)
    weight_g: str | None = Field(default=None, max_length=64)
    width_mm: str | None = Field(default=None, max_length=64)
    height_mm: str | None = Field(default=None, max_length=64)
    length_mm: str | None = Field(default=None, max_length=64)


class OzonExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(min_length=1, max_length=255)
    listing_ids: list[str] = Field(min_length=1, max_length=1000)
    template_name: str = Field(min_length=1, max_length=255)
    template_base64: str = Field(min_length=1, max_length=4 * 1024 * 1024)
    overrides: dict[str, OzonExportOverride] = Field(default_factory=dict)
    preview_fingerprint: str = Field(default="", max_length=64)

    @field_validator("listing_ids")
    @classmethod
    def unique_ids(cls, ids: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 128 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("请选择有效且不重复的商品")
        return ids


class OzonExportTemplate(TypedDict):
    category: str
    category_id: str
    currency: str
    required_fields: list[str]


class OzonExportRow(TypedDict):
    listing_id: str
    seller_sku: str
    version: str
    title_before: str
    title_changed: bool
    fields: dict[str, str]
    errors: list[str]
    warnings: list[str]


class OzonExportSummary(TypedDict):
    total: int
    ready: int
    missing: int
    barcode_missing: int
    title_changed: int


class OzonExportPreview(TypedDict):
    ok: bool
    template: OzonExportTemplate
    rows: list[OzonExportRow]
    summary: OzonExportSummary
    preview_fingerprint: str


class OzonExportDownload(TypedDict):
    ok: bool
    filename: str
    file_base64: str


__all__ = ["OzonExportRequest", "OzonExportOverride", "OzonExportTemplate", "OzonExportRow",
           "OzonExportSummary", "OzonExportPreview", "OzonExportDownload"]
