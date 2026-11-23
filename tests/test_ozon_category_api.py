from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.error
from unittest.mock import patch

import pytest

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.runtime_units import category_store, ozon_category_api, store_credentials
from erp_web.runtime_units.category_providers import OzonCategoryProvider
from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    OzonCategorySearcher,
)
from erp_web.stores.category_catalog_store import CategoryCatalogStore


OZON_TREE = {
    "result": [
        {
            "description_category_id": 17027949,
            "category_name": "Шины",
            "disabled": False,
            "children": [
                {
                    "type_id": 94765,
                    "type_name": "Шины для легковых автомобилей",
                    "disabled": False,
                    "children": [],
                }
            ],
        },
        {
            "description_category_id": 17030000,
            "category_name": "Одежда",
            "disabled": False,
            "children": [
                {
                    "type_id": 10001,
                    "type_name": "Футболки",
                    "disabled": False,
                    "children": [],
                }
            ],
        },
    ]
}

OZON_TREE_ZH = {
    "result": [
        {
            "description_category_id": 17027949,
            "category_name": "轮胎",
            "disabled": False,
            "children": [
                {
                    "type_id": 94765,
                    "type_name": "轿车轮胎",
                    "disabled": False,
                    "children": [],
                }
            ],
        },
        {
            "description_category_id": 17030000,
            "category_name": "服装",
            "disabled": False,
            "children": [
                {
                    "type_id": 10001,
                    "type_name": "T恤",
                    "disabled": False,
                    "children": [],
                }
            ],
        },
    ]
}


def _store_config() -> dict[str, object]:
    return {"ozon": {"client_id": "client-id", "api_key": "api-key"}}


@pytest.fixture(autouse=True)
def isolated_ozon_category_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CategoryCatalogStore:
    store = CategoryCatalogStore(tmp_path / "category-catalog.sqlite3")
    monkeypatch.setattr(ozon_category_api, "_catalog_store", lambda: store)
    from erp_web.runtime_units import category_providers
    monkeypatch.setattr(category_providers, "_catalog_store", lambda: store)
    return store


def test_ozon_bilingual_tree_chinese_search_and_attributes_use_official_api() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(
        method: str,
        url: str,
        client_id: str,
        api_key: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del kwargs
        assert client_id == "client-id"
        assert api_key == "api-key"
        body = payload or {}
        calls.append((method, url, body))
        if url == ozon_category_api.OZON_CATEGORY_TREE_URL:
            return OZON_TREE_ZH if body.get("language") == "ZH_HANS" else OZON_TREE
        if url == ozon_category_api.OZON_CATEGORY_ATTRIBUTES_URL:
            if body.get("language") == "ZH_HANS":
                return {
                    "result": [
                        {"id": 85, "name": "品牌", "description": "选择品牌", "is_required": True, "type": "String"},
                        {"id": 8229, "name": "商品颜色", "description": "主色调", "is_required": False, "type": "String"},
                    ]
                }
            assert body == {"description_category_id": 17027949, "type_id": 94765, "language": "DEFAULT"}
            return {
                "result": [
                    {"id": 85, "name": "Бренд", "is_required": True, "type": "String"},
                    {"id": 8229, "name": "Цвет товара", "is_required": False, "type": "String"},
                ]
            }
        raise AssertionError(url)

    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=request),
    ):
        ru_results = category_store.search_categories_live("ozon", "легковых автомобилей", limit=5)
        zh_results = category_store.search_categories_live("ozon", "轿车轮胎", limit=5)
        page = category_store.fetch_category_attribute_page("ozon", "94765")

    assert len(ru_results) == 1
    assert ru_results[0]["category_id"] == "94765"
    assert ru_results[0]["description_category_id"] == "17027949"
    assert ru_results[0]["path"] == "Шины / Шины для легковых автомобилей"
    assert ru_results[0]["name_zh"] == "轿车轮胎"
    assert ru_results[0]["path_zh"] == "轮胎 / 轿车轮胎"
    # 中文搜索命中同一官方类目
    assert len(zh_results) == 1 and zh_results[0]["category_id"] == "94765"
    # 属性页双语分离：发布原文 name 不变，展示中文进入 name_zh / description_zh
    assert page["category_path_zh"] == "轮胎 / 轿车轮胎"
    assert page["description_category_id"] == "17027949"
    attr_by_id = {attr["id"]: attr for attr in page["attributes"]}
    assert attr_by_id["85"]["name"] == "Бренд"
    assert attr_by_id["85"]["name_zh"] == "品牌"
    assert attr_by_id["85"]["skip_value_localization"] is True
    assert attr_by_id["8229"]["name"] == "Цвет товара"
    assert attr_by_id["8229"]["name_zh"] == "商品颜色"
    assert attr_by_id["8229"]["description_zh"] == "主色调"


def test_ozon_dictionary_metadata_and_values_are_loaded_separately() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def request(
        method: str,
        url: str,
        client_id: str,
        api_key: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del method, client_id, api_key, kwargs
        body = payload or {}
        calls.append((url, body))
        if url == ozon_category_api.OZON_CATEGORY_TREE_URL:
            return OZON_TREE_ZH if body.get("language") == "ZH_HANS" else OZON_TREE
        if url == ozon_category_api.OZON_CATEGORY_ATTRIBUTES_URL:
            return {
                "result": [
                    {
                        "id": 85,
                        "name": "Бренд",
                        "is_required": True,
                        "type": "String",
                        "dictionary_id": 28732849,
                        "category_dependent": True,
                    },
                    {
                        "id": 9048,
                        "name": "Название модели",
                        "is_required": True,
                        "type": "String",
                        "dictionary_id": 0,
                    },
                ]
            }
        if url == ozon_category_api.OZON_CATEGORY_ATTRIBUTE_VALUES_SEARCH_URL:
            assert body == {
                "attribute_id": 85,
                "description_category_id": 17027949,
                "limit": 50,
                "type_id": 94765,
                "value": "нет бренда",
            }
            return {
                "result": [
                    {
                        "id": 126745801,
                        "value": "Нет бренда",
                        "info": "Товар не имеет бренда",
                        "picture": "",
                    }
                ]
            }
        raise AssertionError(url)

    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=request),
    ):
        record = category_store.fetch_category_record("ozon", "94765", include_attributes=True)
        values = category_store.fetch_category_attribute_values(
            "ozon",
            "94765",
            "85",
            query="нет бренда",
        )

    brand = record["attributes"]["required"][0]
    assert brand["dictionary_id"] == "28732849"
    assert brand["is_dictionary"] is True
    assert brand["category_dependent"] is True
    assert brand["options"] == []
    model_name = record["attributes"]["required"][1]
    assert model_name["dictionary_id"] == ""
    assert model_name["is_dictionary"] is False
    assert values["values"] == [
        {
            "id": "126745801",
            "value": "Нет бренда",
            "info": "",
            "picture": "",
        }
    ]


def test_ozon_no_brand_alias_uses_platform_search_for_large_dictionary() -> None:
    search_payloads: list[dict[str, object]] = []

    def request(
        method: str,
        url: str,
        client_id: str,
        api_key: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del method, client_id, api_key, kwargs
        body = payload or {}
        if url == ozon_category_api.OZON_CATEGORY_TREE_URL:
            return OZON_TREE
        if url != ozon_category_api.OZON_CATEGORY_ATTRIBUTE_VALUES_SEARCH_URL:
            raise AssertionError(url)
        search_payloads.append(body)
        return {"result": [{"id": 2001, "value": "Нет бренда"}]}

    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=request),
    ):
        values = category_store.fetch_category_attribute_values(
            "ozon",
            "94765",
            "85",
            query="无品牌",
            limit=1,
        )

    assert search_payloads == [
        {
            "attribute_id": 85,
            "description_category_id": 17027949,
            "limit": 1,
            "type_id": 94765,
            "value": "нет бренда",
        }
    ]
    assert values["query"] == "无品牌"
    assert values["values"][0]["id"] == "2001"
    assert values["values"][0]["value"] == "Нет бренда"


def test_ozon_dictionary_search_rejects_single_character_before_request() -> None:
    with pytest.raises(ValueError, match="至少需要 2 个字符"):
        ozon_category_api.fetch_ozon_category_attribute_values(
            "94765",
            "85",
            query="A",
        )


def test_ozon_non_brand_dictionary_merges_zh_hans_and_caches_each_cursor_page() -> None:
    value_payloads: list[dict[str, object]] = []

    def request(
        method: str,
        url: str,
        client_id: str,
        api_key: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del method, client_id, api_key, kwargs
        body = payload or {}
        if url == ozon_category_api.OZON_CATEGORY_TREE_URL:
            return OZON_TREE
        if url != ozon_category_api.OZON_CATEGORY_ATTRIBUTE_VALUES_URL:
            raise AssertionError(url)
        value_payloads.append(body)
        is_zh = body.get("language") == "ZH_HANS"
        last_id = int(body.get("last_value_id") or 0)
        start = last_id + 1
        return {
            "result": [
                {
                    "id": value_id,
                    "value": f"中文色 {value_id}" if is_zh else f"Значение {value_id}",
                }
                for value_id in range(start, start + 50)
            ],
            "has_next": last_id == 0,
        }

    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=request),
    ):
        page1 = category_store.fetch_category_attribute_values("ozon", "94765", "8229", limit=50)
        # 二次读同页应命中 SQLite 缓存
        page1_cached = category_store.fetch_category_attribute_values("ozon", "94765", "8229", limit=50)
        page2 = category_store.fetch_category_attribute_values(
            "ozon", "94765", "8229", limit=50, cursor=page1["next_cursor"]
        )

    assert len(page1["values"]) == 50
    assert page1["values"][0]["value"] == "Значение 1"
    assert page1["values"][0]["label_zh"] == "中文色 1"
    assert page1["next_cursor"] == "50"
    assert page1["has_more"] is True
    assert page1_cached["values"][0]["value"] == "Значение 1"
    # 第二页（last_value_id=50）拥有独立缓存键，不得串到第一页。
    assert page2["values"][0]["id"] == "51"
    assert page2["values"][0]["label_zh"] == "中文色 51"
    default_calls = [p for p in value_payloads if p.get("language") == "DEFAULT"]
    assert [p["last_value_id"] for p in default_calls] == [0, 50]


def test_ozon_composite_key_preserves_same_type_id_under_different_description_categories() -> None:
    tree = {
        "result": [
            {
                "description_category_id": 1001,
                "category_name": "Категория А",
                "children": [{"type_id": 99999, "type_name": "Универсальный товар", "disabled": False, "children": []}],
            },
            {
                "description_category_id": 2002,
                "category_name": "Категория Б",
                "children": [{"type_id": 99999, "type_name": "Универсальный товар", "disabled": False, "children": []}],
            },
        ]
    }
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", return_value=tree),
    ):
        records, _ = ozon_category_api.load_ozon_category_corpus()
    desc_ids = sorted(row["description_category_id"] for row in records)
    assert desc_ids == ["1001", "2002"]


def test_ozon_category_tree_summary_and_navigation() -> None:
    tree = {
        "result": [
            {
                "description_category_id": 17027495,
                "category_name": "Автотовары",
                "children": [
                    {
                        "description_category_id": 17039878,
                        "category_name": "Автомагнитолы",
                        "children": [
                            {"type_id": 971326576, "type_name": "Аксессуар для автомагнитолы", "disabled": False, "children": []},
                            {"type_id": 95858, "type_name": "Автомагнитола", "disabled": False, "children": []},
                        ],
                    }
                ],
            }
        ]
    }
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", return_value=tree),
    ):
        summary = ozon_category_api.fetch_ozon_category_tree_summary()
        roots = ozon_category_api.fetch_ozon_category_roots()
        groups = ozon_category_api.fetch_ozon_category_children(["17027495"])
        product_types = ozon_category_api.fetch_ozon_category_children(["17039878"])
        siblings = ozon_category_api.fetch_ozon_category_siblings(["95858"])

    assert summary["product_type_count"] == 2
    assert [(node["node_id"], node["level"]) for node in roots["nodes"]] == [("17027495", "branch")]
    assert groups["nodes"][0]["node_id"] == "17039878"
    assert {node["category_id"]: node["name"] for node in product_types["nodes"]} == {
        "95858": "Автомагнитола",
        "971326576": "Аксессуар для автомагнитолы",
    }
    assert [row["category_id"] for row in siblings] == ["971326576"]


def test_ozon_search_reports_missing_credentials_and_empty_corpus() -> None:
    with patch.object(
        ozon_category_api,
        "_load_store_config",
        return_value={"ozon": {"client_id": "", "api_key": ""}},
    ):
        with pytest.raises(CategorySearchError) as raised:
            OzonCategorySearcher(OzonCategoryProvider(), "global").search_categories("вентилятор")
    assert raised.value.code == "CATEGORY_CREDENTIALS_MISSING"

    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", return_value={"result": []}),
    ):
        with pytest.raises(CategorySearchError) as raised:
            OzonCategorySearcher(OzonCategoryProvider(), "global").search_categories("вентилятор")
    assert raised.value.code == "CATEGORY_CORPUS_UNAVAILABLE"


def test_ozon_category_auth_test_reads_the_category_tree_without_a_category_id() -> None:
    config: dict[str, object] = {"ozon": {"client_id": "client-id", "api_key": "api-key", "shop_name": ""}}
    with (
        patch.object(get_context().config, "load_store_config", return_value=config),
        patch.object(get_context().config, "save_store_config") as save_config,
        patch.object(store_credentials, "summarize_store_auth_states", return_value={"ozon": {"status": "测试成功"}}),
        patch("erp_web.runtime_units.ozon_category_api.fetch_ozon_category_tree_summary", return_value={"product_type_count": 2, "sample": {"type_id": "94765"}}) as fetch_tree,
        patch.object(publisher, "fetch_ozon_seller_info", return_value={"company": {"currency": "CNY"}}) as fetch_seller,
    ):
        result = store_credentials.test_store_auth("ozon", "category")

    fetch_tree.assert_called_once_with(force_refresh=True, credentials=("client-id", "api-key"))
    fetch_seller.assert_called_once_with("client-id", "api-key")
    save_config.assert_called_once_with(config)
    assert result["ok"] is True


def _seed_aged_taxonomy(store: CategoryCatalogStore, *, age: timedelta) -> None:
    nodes, _ = ozon_category_api._build_taxonomy_nodes(OZON_TREE["result"], OZON_TREE_ZH["result"])
    scope = ozon_category_api.OZON_CATALOG_SCOPE
    store.save_taxonomy(
        "ozon",
        scope,
        ozon_category_api._TAXONOMY_LOCALE,
        nodes,
        corpus_hash=ozon_category_api._stable_corpus_hash(nodes),
        taxonomy_version=None,
        retrieved_at=datetime.now(timezone.utc) - age,
        fresh_ttl=ozon_category_api.OZON_CATEGORY_CACHE_FRESH_TTL,
        max_age=ozon_category_api.OZON_CATEGORY_CACHE_MAX_AGE,
    )


def test_ozon_category_corpus_stale_cooldown_and_auth_rules(isolated_ozon_category_cache: CategoryCatalogStore) -> None:
    _seed_aged_taxonomy(isolated_ozon_category_cache, age=timedelta(days=2))
    network_error = urllib.error.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=network_error),
    ):
        records, info = ozon_category_api.load_ozon_category_corpus()
        _, repeated_info = ozon_category_api.load_ozon_category_corpus()
    assert records
    assert info["cache_source"] == "stale_cache"
    assert info["stale"] is True
    assert repeated_info["cache_source"] == "stale_cache"

    # 鉴权错误不得被 stale 缓存掩盖
    isolated_ozon_category_cache.clear_taxonomy("ozon")
    _seed_aged_taxonomy(isolated_ozon_category_cache, age=timedelta(days=2))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=RuntimeError("POST failed: 401 unauthorized")),
    ):
        with pytest.raises(RuntimeError, match="401 unauthorized"):
            ozon_category_api.load_ozon_category_corpus()

    # 超过 7 天的过期缓存不得再提供 stale
    isolated_ozon_category_cache.clear_taxonomy("ozon")
    _seed_aged_taxonomy(isolated_ozon_category_cache, age=timedelta(days=8))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", side_effect=network_error),
    ):
        with pytest.raises(urllib.error.URLError):
            ozon_category_api.load_ozon_category_corpus()


def test_ozon_category_force_refresh_bypasses_fresh_cache(isolated_ozon_category_cache: CategoryCatalogStore) -> None:
    _seed_aged_taxonomy(isolated_ozon_category_cache, age=timedelta(hours=1))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", return_value=OZON_TREE) as request,
    ):
        _, info = ozon_category_api.load_ozon_category_corpus(force_refresh=True)

    assert info["cache_source"] == "remote_cache"
    assert request.call_count >= 1
