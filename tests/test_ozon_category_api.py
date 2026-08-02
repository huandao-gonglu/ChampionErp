from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path
import urllib.error
from unittest.mock import patch

import pytest

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.runtime_units import category_store, ozon_category_api, store_credentials
from erp_web.runtime_units.ozon_category_cache import (
    OzonCategoryCacheEntry,
    write_ozon_category_cache,
)
from erp_web.runtime_units.category_providers import OzonCategoryProvider
from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    OzonCategorySearcher,
)


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


def _store_config() -> dict[str, object]:
    return {"ozon": {"client_id": "client-id", "api_key": "api-key"}}


@pytest.fixture(autouse=True)
def isolated_ozon_category_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ozon_category_api, "_category_cache_root", lambda: tmp_path)
    ozon_category_api.clear_ozon_category_tree_cache()


def test_ozon_category_search_and_attributes_use_official_api() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(
        method: str,
        url: str,
        client_id: str,
        api_key: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        assert client_id == "client-id"
        assert api_key == "api-key"
        calls.append((method, url, payload or {}))
        if url == ozon_category_api.OZON_CATEGORY_TREE_URL:
            return OZON_TREE
        if url == ozon_category_api.OZON_CATEGORY_ATTRIBUTES_URL:
            assert payload == {"description_category_id": 17027949, "type_id": 94765, "language": "DEFAULT"}
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
        results = category_store.search_categories_live("ozon", "легковых автомобилей", limit=5)
        attrs = category_store.fetch_category_attributes("ozon", "94765")

    assert len(results) == 1
    assert results[0]["category_id"] == "94765"
    assert results[0]["description_category_id"] == "17027949"
    assert results[0]["path"] == "Шины / Шины для легковых автомобилей"
    assert attrs["platform"] == "ozon"
    assert attrs["source"] == "ozon_live"
    assert attrs["required"][0]["id"] == "85"
    assert attrs["optional"][0]["id"] == "8229"
    assert [call[1] for call in calls] == [
        ozon_category_api.OZON_CATEGORY_TREE_URL,
        ozon_category_api.OZON_CATEGORY_ATTRIBUTES_URL,
    ]


def test_ozon_category_tree_summary_reuses_the_live_tree() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(ozon_category_api, "request_ozon_json", return_value=OZON_TREE) as request,
    ):
        summary = ozon_category_api.fetch_ozon_category_tree_summary()

    assert summary["product_type_count"] == 2
    assert summary["sample"]["type_id"] == "94765"
    request.assert_called_once_with(
        "POST",
        ozon_category_api.OZON_CATEGORY_TREE_URL,
        "client-id",
        "api-key",
        {"language": "DEFAULT"},
    )


def test_ozon_bound_searcher_searches_the_server_cache_by_keyword() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    tree = {
        "result": [
            {
                "description_category_id": 200,
                "category_name": "Климатическая техника",
                "children": [
                    {
                        "type_id": 201,
                        "type_name": "Вентиляторы бытовые",
                        "disabled": False,
                        "children": [],
                    }
                ],
            },
            {
                "description_category_id": 300,
                "category_name": "Электроника",
                "children": [
                    {
                        "type_id": 301,
                        "type_name": "USB аксессуары",
                        "disabled": False,
                        "children": [],
                    }
                ],
            },
        ]
    }
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            return_value=tree,
        ) as request,
        patch.object(
            ozon_category_api,
            "_flatten_product_types",
            wraps=ozon_category_api._flatten_product_types,
        ) as flatten,
        patch.object(
            ozon_category_api,
            "_stable_corpus_hash",
            wraps=ozon_category_api._stable_corpus_hash,
        ) as corpus_hash,
    ):
        searcher = OzonCategorySearcher(
            OzonCategoryProvider(),
            "global",
        )
        result = searcher.search_categories("вентилятор")
        searcher.search_categories("usb аксессуары")

    assert result["candidates"][0]["category_id"] == "201"
    assert result["keyword"] == "вентилятор"
    assert result["source"] == "remote_cache"
    assert "corpus" not in str(result)
    request.assert_called_once()
    flatten.assert_called_once()
    corpus_hash.assert_called_once()


def test_ozon_search_reports_missing_credentials_on_tool_call() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    with patch.object(
        ozon_category_api,
        "_load_store_config",
        return_value={"ozon": {"client_id": "", "api_key": ""}},
    ):
        with pytest.raises(CategorySearchError) as raised:
            OzonCategorySearcher(
                OzonCategoryProvider(),
                "global",
            ).search_categories("вентилятор")

    assert raised.value.code == "CATEGORY_CREDENTIALS_MISSING"


def test_ozon_empty_cache_source_is_not_reported_as_zero_match() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            return_value={"result": []},
        ),
    ):
        with pytest.raises(CategorySearchError) as raised:
            OzonCategorySearcher(
                OzonCategoryProvider(),
                "global",
            ).search_categories("вентилятор")

    assert raised.value.code == "CATEGORY_CORPUS_UNAVAILABLE"


def test_ozon_category_auth_test_reads_the_category_tree_without_a_category_id() -> None:
    config: dict[str, object] = {"ozon": {"client_id": "client-id", "api_key": "api-key", "shop_name": ""}}
    with (
        patch.object(get_context().config, "load_store_config", return_value=config),
        patch.object(get_context().config, "save_store_config") as save_config,
        patch.object(store_credentials, "summarize_store_auth_states", return_value={"ozon": {"status": "测试成功"}}),
        patch("erp_web.runtime_units.ozon_category_api.fetch_ozon_category_tree_summary", return_value={"product_type_count": 2, "sample": {"type_id": "94765"}}) as fetch_tree,
        patch.object(publisher, "fetch_ozon_shop_name") as fetch_shop,
    ):
        result = store_credentials.test_store_auth("ozon", "category")

    fetch_tree.assert_called_once_with(force_refresh=True)
    fetch_shop.assert_not_called()
    save_config.assert_called_once_with(config)
    assert result["ok"] is True
    assert result["message"] == "类目读取测试成功：已读取 2 个可发布商品类型。"
    assert result["category_tree"] == {"product_type_count": 2, "sample": {"type_id": "94765"}}


def test_ozon_category_corpus_persists_across_memory_cache_clear(
    tmp_path: Path,
) -> None:
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            return_value=OZON_TREE,
        ) as request,
    ):
        first_records, first_info = ozon_category_api.load_ozon_category_corpus()
        ozon_category_api.clear_ozon_category_tree_cache(
            include_persistent=False
        )
        second_records, second_info = ozon_category_api.load_ozon_category_corpus()

    assert first_records == second_records
    assert first_info["cache_source"] == "remote_cache"
    assert second_info["cache_source"] == "persistent_cache"
    assert second_info["stale"] is False
    request.assert_called_once()
    cache_files = list((tmp_path / "ozon_categories").glob("*.json.gz"))
    assert len(cache_files) == 1
    raw_cache = gzip.decompress(cache_files[0].read_bytes()).decode("utf-8")
    assert "client-id" not in raw_cache
    assert "api-key" not in raw_cache


def _write_aged_cache(cache_root: Path, *, age: timedelta) -> None:
    records = ozon_category_api._flatten_product_types(OZON_TREE["result"])
    write_ozon_category_cache(
        cache_root,
        OzonCategoryCacheEntry(
            credential_scope_hash=ozon_category_api._credential_scope_hash(
                "client-id"
            ),
            corpus_hash=ozon_category_api._stable_corpus_hash(records),
            taxonomy_version=None,
            locale="ru-RU",
            retrieved_at=datetime.now(timezone.utc) - age,
            records=records,
        ),
    )


def test_ozon_category_corpus_uses_seven_day_stale_cache_on_network_error(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(days=2))
    network_error = urllib.error.URLError(
        "[SSL: UNEXPECTED_EOF_WHILE_READING]"
    )
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            side_effect=network_error,
        ) as request,
    ):
        records, info = ozon_category_api.load_ozon_category_corpus()
        _, repeated_info = ozon_category_api.load_ozon_category_corpus()

    assert records
    assert info["cache_source"] == "stale_cache"
    assert info["stale"] is True
    assert repeated_info["cache_source"] == "stale_cache"
    # 首次刷新包含一次瞬时错误重试；60 秒冷却内的多轮搜索不重复打远端。
    assert request.call_count == 2


def test_ozon_category_corpus_treats_cache_as_fresh_for_24_hours(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(hours=23))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            side_effect=AssertionError("24 小时内不应请求远端"),
        ) as request,
    ):
        _, info = ozon_category_api.load_ozon_category_corpus()

    assert info["cache_source"] == "persistent_cache"
    assert info["stale"] is False
    assert (
        datetime.fromisoformat(info["expires_at"])
        - datetime.fromisoformat(info["retrieved_at"])
    ) == timedelta(days=1)
    request.assert_not_called()


def test_ozon_category_corpus_does_not_mask_auth_error_with_stale_cache(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(days=2))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            side_effect=RuntimeError("POST failed: 401 unauthorized"),
        ) as request,
    ):
        with pytest.raises(RuntimeError, match="401 unauthorized"):
            ozon_category_api.load_ozon_category_corpus()

    request.assert_called_once()


def test_ozon_category_corpus_rejects_cache_older_than_seven_days(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(days=8))
    network_error = urllib.error.URLError("connection reset")
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            side_effect=network_error,
        ),
    ):
        with pytest.raises(urllib.error.URLError):
            ozon_category_api.load_ozon_category_corpus()


def test_ozon_category_force_refresh_bypasses_fresh_cache(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(hours=1))
    with (
        patch.object(ozon_category_api, "_load_store_config", _store_config),
        patch.object(
            ozon_category_api,
            "request_ozon_json",
            return_value=OZON_TREE,
        ) as request,
    ):
        _, info = ozon_category_api.refresh_ozon_category_corpus()

    assert info["cache_source"] == "remote_cache"
    request.assert_called_once()


def test_ozon_category_cache_clear_only_removes_managed_cache_files(
    tmp_path: Path,
) -> None:
    _write_aged_cache(tmp_path, age=timedelta(hours=1))
    cache_directory = tmp_path / "ozon_categories"
    unrelated = cache_directory / "keep-me.txt"
    unrelated.write_text("保留", encoding="utf-8")

    removed = ozon_category_api.clear_ozon_category_tree_cache()

    assert removed == 1
    assert unrelated.read_text(encoding="utf-8") == "保留"
    assert not list(cache_directory.glob("*.json.gz"))
