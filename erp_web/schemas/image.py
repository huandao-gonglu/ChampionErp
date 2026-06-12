from __future__ import annotations

from typing import Any, TypedDict


class ImageItem(TypedDict, total=False):
    id: str
    asset_id: str
    url: str
    local_path: str
    preview_url: str
    width: int
    height: int
    size_label: str
    type: str
    asset_type: str
    origin: str
    usage: str
    platforms: list[str]
    selected: bool
    is_main: bool
    order: int
    sort_order: int
    sku: str
    note: str
    raw: dict[str, Any]
