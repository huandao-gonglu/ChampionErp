from __future__ import annotations

from typing import Any

import pytest

from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    MercadoLibreCategorySearcher,
    OzonCategorySearcher,
    create_category_searcher,
)


class Provider:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.calls: list[tuple[str, str, int, float]] = []

    def resolve_site(self, site: str = "") -> str:
        if self.platform == "ozon":
            return "global"
        return site.upper() if site else "MLM"

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return {"category_id": category_id, "site": site}

    def discover(
        self,
        keyword: str,
        site: str = "",
        limit: int = 8,
        *,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        self.calls.append((keyword, site, limit, timeout_seconds))
        return [{"category_id": "MLM-1", "name": "Ventiladores"}]

    def search(
        self,
        keyword: str,
        site: str = "",
        limit: int = 8,
        *,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        self.calls.append((keyword, site, limit, timeout_seconds))
        return [
            {
                "category_id": "1001",
                "type_id": "1001",
                "description_category_id": "2001",
                "name_original": "Вентиляторы",
                "path_original": ["Бытовая техника", "Вентиляторы"],
            }
        ]


def test_factory_selects_concrete_searcher_once_by_current_platform() -> None:
    providers = {
        "mercadolibre": Provider("mercadolibre"),
        "ozon": Provider("ozon"),
    }

    ml = create_category_searcher(
        "mercadolibre",
        site="mlm",
        provider_resolver=providers.__getitem__,
    )
    ozon = create_category_searcher(
        "ozon",
        site="global",
        provider_resolver=providers.__getitem__,
    )

    assert isinstance(ml, MercadoLibreCategorySearcher)
    assert isinstance(ozon, OzonCategorySearcher)
    assert ml.search_categories("ventilador")["candidates"][0]["category_id"] == "MLM-1"
    assert ozon.search_categories("вентилятор")["candidates"][0]["category_id"] == "1001"
    assert providers["mercadolibre"].calls[0][1] == "MLM"
    assert providers["ozon"].calls[0][1] == "global"


def test_factory_rejects_platform_without_concrete_implementation() -> None:
    with pytest.raises(CategorySearchError) as raised:
        create_category_searcher(
            "amazon",
            site="US",
            provider_resolver=lambda platform: Provider(platform),
        )

    assert raised.value.code == "CATEGORY_PROVIDER_UNSUPPORTED"
