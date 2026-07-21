from __future__ import annotations

from unittest.mock import patch

from erp_web import runtime as erp_web_app
from erp_web.runtime_units import category_store, ozon_category_api


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


def test_ozon_category_search_and_attributes_use_official_api() -> None:
    ozon_category_api.clear_ozon_category_tree_cache()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(method: str, url: str, client_id: str, api_key: str, payload: dict[str, object] | None = None) -> dict[str, object]:
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


def test_ozon_category_auth_test_reads_the_category_tree_without_a_category_id() -> None:
    config: dict[str, object] = {"ozon": {"client_id": "client-id", "api_key": "api-key", "shop_name": ""}}
    with (
        patch.object(erp_web_app, "load_store_config", return_value=config),
        patch.object(erp_web_app, "save_store_config") as save_config,
        patch.object(erp_web_app, "summarize_store_auth_states", return_value={"ozon": {"status": "测试成功"}}),
        patch("erp_web.runtime_units.ozon_category_api.fetch_ozon_category_tree_summary", return_value={"product_type_count": 2, "sample": {"type_id": "94765"}}) as fetch_tree,
        patch.object(erp_web_app.publisher, "fetch_ozon_shop_name") as fetch_shop,
    ):
        result = erp_web_app.test_store_auth("ozon", "category")

    fetch_tree.assert_called_once_with()
    fetch_shop.assert_not_called()
    save_config.assert_called_once_with(config)
    assert result["ok"] is True
    assert result["message"] == "类目读取测试成功：已读取 2 个可发布商品类型。"
    assert result["category_tree"] == {"product_type_count": 2, "sample": {"type_id": "94765"}}
