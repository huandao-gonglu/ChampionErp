from __future__ import annotations

from typing import Any, TypedDict


class AppConfig(TypedDict, total=False):
    ai_models: list[dict[str, Any]]
    ai_use_case_bindings: dict[str, dict[str, str]]
    pricing: dict[str, Any]
    pricing_defaults: dict[str, Any]
    product_research: dict[str, Any]
    browser: dict[str, Any]


class StoreConfig(TypedDict, total=False):
    mercadolibre: dict[str, Any]
    wildberries: dict[str, Any]
    ozon: dict[str, Any]
