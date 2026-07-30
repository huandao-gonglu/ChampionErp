from __future__ import annotations

from typing import Any, TypedDict


class ImageItem(TypedDict, total=False):
    id: str
    url: str
    path: str
    preview_url: str
    width: int
    height: int
    size_label: str
    origin: str
    usage: str
    platforms: list[str]
    selected: bool
    is_main: bool
    is_sku: bool
    order: int
    status: str
    sku: str
    note: str
    derived_from_id: str
    source_asset_id: str
    target_language: str
    provider: str
    translate_job_id: str
    raw: dict[str, Any]


class DraftImageRef(TypedDict, total=False):
    asset_id: str
    role: str
    order: int
    label: str
    note: str
    alt_text: str
    source_asset_id: str
