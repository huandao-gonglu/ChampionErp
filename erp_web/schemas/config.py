from __future__ import annotations

from typing import Any, TypedDict


class AppConfig(TypedDict, total=False):
    ai: dict[str, Any]
    text_ai: dict[str, str]
    image_ai: dict[str, str]
    video_ai: dict[str, str]
    pricing: dict[str, Any]
    product_research: dict[str, Any]
    browser: dict[str, Any]


class StoreConfig(TypedDict, total=False):
    mercadolibre: dict[str, Any]
    wildberries: dict[str, Any]
    ozon: dict[str, Any]
