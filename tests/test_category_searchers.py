from __future__ import annotations

from typing import Any

import pytest

from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    MercadoLibreCategorySearcher,
    OzonCategorySearcher,
    YandexCategorySearcher,
    create_category_searcher,
)


class Provider:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.calls: list[tuple[str, str, int, float]] = []
        self.search_error: Exception | None = None

    def resolve_site(self, site: str = "") -> str:
        if self.platform in {"ozon", "yandex"}:
            return "global"
        return site.upper() if site else "MLM"

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return {"category_id": category_id, "site": site}

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("searcher 不应读取属性定义")

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("searcher 不应读取枚举值")

    def search_categories(
        self,
        keyword: str,
        site: str = "",
        limit: int = 8,
        *,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        self.calls.append((keyword, site, limit, timeout_seconds))
        if self.search_error is not None:
            raise self.search_error
        if self.platform == "mercadolibre":
            return [{"category_id": "MLM-1", "name": "Ventiladores"}]
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
        "yandex": Provider("yandex"),
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
    yandex = create_category_searcher(
        "yandex",
        site="global",
        provider_resolver=providers.__getitem__,
    )

    assert isinstance(ml, MercadoLibreCategorySearcher)
    assert isinstance(ozon, OzonCategorySearcher)
    assert isinstance(yandex, YandexCategorySearcher)
    assert ml.search_categories("ventilador")["candidates"][0]["category_id"] == "MLM-1"
    assert ozon.search_categories("вентилятор")["candidates"][0]["category_id"] == "1001"
    yandex_result = yandex.search_categories("вентилятор")
    assert yandex_result["candidates"][0]["category_id"] == "1001"
    assert yandex_result["candidates"][0]["platform"] == "yandex"
    assert yandex_result["source"] == "yandex_cache"
    assert providers["mercadolibre"].calls[0][1] == "MLM"
    assert providers["ozon"].calls[0][1] == "global"
    assert providers["yandex"].calls[0][1] == "global"
    # 空关键词返回零候选而不是异常
    assert yandex.search_categories("  ")["candidates"] == []


def test_factory_rejects_platform_without_concrete_implementation() -> None:
    with pytest.raises(CategorySearchError) as raised:
        create_category_searcher(
            "amazon",
            site="US",
            provider_resolver=lambda platform: Provider(platform),
        )

    assert raised.value.code == "CATEGORY_PROVIDER_UNSUPPORTED"


def test_yandex_search_classifies_rate_limit_and_auth_errors() -> None:
    # Yandex Market 限流使用 HTTP 420；必须被识别为可重试限流
    providers = {"yandex": Provider("yandex")}
    searcher = create_category_searcher(
        "yandex",
        site="global",
        provider_resolver=providers.__getitem__,
    )

    for message, expected_code, expected_retryable in [
        ("GET https://api.partner.market.yandex.ru/v2/categories/tree failed: 420 rate limited", "CATEGORY_RATE_LIMITED", True),
        ("Yandex 接口限流", "CATEGORY_RATE_LIMITED", True),
        ("GET https://api.partner.market.yandex.ru/v2/categories/tree failed: 401 unauthorized", "CATEGORY_AUTH_REJECTED", False),
        ("请先填写 Yandex API-Key Token。", "CATEGORY_CREDENTIALS_MISSING", False),
        ("request timed out", "CATEGORY_PROVIDER_TIMEOUT", True),
    ]:
        providers["yandex"].search_error = RuntimeError(message)
        with pytest.raises(CategorySearchError) as raised:
            searcher.search_categories("вентилятор")
        assert raised.value.code == expected_code, message
        assert raised.value.retryable is expected_retryable, message
