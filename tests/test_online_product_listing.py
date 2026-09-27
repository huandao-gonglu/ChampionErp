"""组合商品展示按真实平台关系分页，筛选不扩张操作范围。"""
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.schemas.online_product_capabilities import OnlineReadResult
from erp_web.schemas.online_products import MarketSnapshot, OnlineListing, snapshot_version
from erp_web.services.online_product_listing import listing_page
from erp_web.services.online_product_service import OnlineProductService


def product(number, group=None, **overrides):
    return OnlineListing(id=f"id-{number}", remote_id=f"sku-{number}", platform="yandex",
                         account_id="business:campaign", model="business_offer", title="同名商品",
                         snapshot={"offer": {"groupId": group}}, **overrides)


def page(rows, **overrides):
    return listing_page(rows, **{"query": "", "status": "", "market": "", "page": 1, **overrides})


def test_large_group_stays_on_one_page_and_each_listing_appears_once():
    variants = [product(i, "组合 A") for i in range(198)]
    singles = [product(i) for i in range(198, 224)]
    rows = [variants[0], *singles[:12], *variants[1:], *singles[12:]]
    first, second = page(rows), page(rows, page=2)
    assert first["total"] == 27
    assert first["listing_total"] == 224
    assert len(first["groups"]) == 25
    assert len(second["groups"]) == 2
    group = first["groups"][0]
    assert group["kind"] == "group" and group["total_count"] == 198
    assert group["item_ids"] == [row.id for row in variants]
    returned = [r["id"] for p in (first, second) for r in p["items"]]
    assert len(returned) == len(set(returned)) == 224
    assert set(returned) == {row.id for row in rows}
    assert all("snapshot" not in row for row in first["items"])


@pytest.mark.parametrize("group", [None, "", "  ", [], {}, True])
def test_matching_titles_and_sku_prefixes_do_not_invent_groups(group):
    result = page([product(1, group), product(2, group)])
    assert result["total"] == 2
    assert all(row["kind"] == "single" for row in result["groups"])


def test_platform_and_account_scopes_are_part_of_group_identity():
    yandex = [product(i, "shared") for i in range(2)]
    other_shop = [row.model_copy(update={"id": "other-" + row.id, "account_id": "other"}) for row in yandex]
    mercado = [row.model_copy(update={"id": "ml-" + row.id, "platform": "mercadolibre", "model": "user_products",
                                    "snapshot": {"user_product": {"family_id": "shared"}}}) for row in yandex]
    result = page(yandex + other_shop + mercado)
    assert result["total"] == 3
    assert len({group["id"] for group in result["groups"]}) == 3
    assert all(group["total_count"] == 2 for group in result["groups"])


def test_filter_keeps_parent_and_counts_but_only_returns_matching_skus():
    rows = [product(1, "组合", raw_status="PUBLISHED", markets=[MarketSnapshot(id="1", site_id="RU")]),
            product(2, "组合", raw_status="DISABLED"), product(3)]
    group_id = page(rows)["groups"][0]["id"]
    result = page(rows, query=" SKU-1 ", status="PUBLISHED", market="RU")
    assert result["total"] == result["listing_total"] == 1
    assert result["groups"] == [{"id": group_id, "kind": "group", "title": "同名商品", "item_ids": ["id-1"], "total_count": 2}]
    assert [row["id"] for row in result["items"]] == ["id-1"]
    assert page(rows, query="不存在")["groups"] == []
    assert page(rows, page=2)["items"] == []


def test_existing_snapshots_support_grouped_http_and_ai_without_migration():
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {
        "yandex": {"business_id": "business", "campaign_id": "campaign"},
    }))
    service = OnlineProductService(context, adapter_factories={}, start_worker=False)
    try:
        rows = [service.store.save(product(i, "组合")) for i in range(30)]
        result = service.list("yandex")
        assert result["total"] == 1 and result["listing_total"] == result["summary"]["total"] == 30
        assert len(result["items"]) == 30
        validated = OnlineReadResult.model_validate(result).model_dump()
        assert {key: validated[key] for key in result} == result
        assert service.list("yandex", page=2)["items"] == []
        assert service.detail(rows[0].id)["item"]["id"] == rows[0].id
        for row in rows:
            stored = service.store.get(row.id)
            assert stored.version == snapshot_version(row)
            assert stored.synced_at == row.synced_at
    finally:
        service.close()
