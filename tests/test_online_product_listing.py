"""组合商品展示按真实平台关系分页，筛选不扩张操作范围。"""
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.schemas.online_product_capabilities import OnlineReadRequest, OnlineReadResult
from erp_web.schemas.online_products import MarketSnapshot, OnlineListing, PlatformIssue, snapshot_version
from erp_web.services.online_product_listing import listing_page, listing_summary_page, project_listing_fields
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
    assert result["groups"] == [{"id": group_id, "kind": "group", "title": "同名商品", "item_ids": ["id-1"], "total_count": 2,
                                 "feedback_summary": {"affected_sku_count": 0, "error_count": 0, "warning_count": 0}}]
    assert [row["id"] for row in result["items"]] == ["id-1"]
    assert page(rows, query="不存在")["groups"] == []
    assert page(rows, page=2)["items"] == []


def test_group_feedback_counts_all_members_and_keeps_issue_counts_separate_from_sku_counts():
    rows = [product(1, '组合', platform_issues=[PlatformIssue(severity='error', message='格式错误'),
                                                   PlatformIssue(severity='error', message='缺少属性'),
                                                   PlatformIssue(severity='warning', message='配送警告')]),
            product(2, '组合', platform_issues=[PlatformIssue(severity='warning', message='配送警告')]),
            product(3, '组合'),
            product(4, platform_issues=[PlatformIssue(severity='error', message='其他商品错误')])]
    before = [row.model_dump() for row in rows]
    result = page(rows, query='sku-3')
    assert [row['id'] for row in result['items']] == ['id-3']
    group = result['groups'][0]
    assert group['total_count'] == 3 and group['item_ids'] == ['id-3']
    assert group['feedback_summary'] == {'affected_sku_count': 2, 'error_count': 2, 'warning_count': 2}
    assert [row.model_dump() for row in rows] == before


def test_group_feedback_updates_when_a_member_has_new_platform_feedback():
    rows = [product(1, '组合', platform_issues=[PlatformIssue(severity='warning', message='配送警告')]), product(2, '组合')]
    assert page(rows)['groups'][0]['feedback_summary'] == {'affected_sku_count': 1, 'error_count': 0, 'warning_count': 1}
    rows[0].platform_issues = []
    assert page(rows)['groups'][0]['feedback_summary'] == {'affected_sku_count': 0, 'error_count': 0, 'warning_count': 0}


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
        compact = service.read_page("yandex", limit=1)
        validated = OnlineReadResult.model_validate(compact)
        assert validated.total == 1 and validated.listing_total == 30
        assert len(validated.items) == 1 and validated.groups[0].total_count == 30
        assert validated.items[0].id == result["items"][0]["id"]
        assert service.list("yandex", page=2)["items"] == []
        assert service.detail(rows[0].id)["item"]["id"] == rows[0].id
        for row in rows:
            stored = service.store.get(row.id)
            assert stored.version == snapshot_version(row)
            assert stored.synced_at == row.synced_at
    finally:
        service.close()


def compact_page(rows, **overrides):
    return listing_summary_page(rows, **{"query": "", "status": "", "market": "", "page": 1,
                                         "limit": 25, "view": "groups", **overrides})


def test_summary_pages_split_large_groups_without_losing_order_or_members():
    variants = [product(i, "组合 A", content={"description": "商品说明" * 10000}) for i in range(198)]
    singles = [product(i) for i in range(198, 224)]
    rows = [variants[0], *singles[:12], *variants[1:], *singles[12:]]
    first = compact_page(rows, limit=1)
    assert first["total"] == 27 and first["listing_total"] == 224 and first["next_page"] == 2
    assert len(first["items"]) == len(first["groups"]) == 1
    group = first["groups"][0]
    assert group["total_count"] == group["matched_count"] == 198
    assert group["representative_id"] == first["items"][0]["id"] == "id-0"
    assert "item_ids" not in group and "content" not in first["items"][0]
    expected = [row["id"] for number in (1, 2) for row in page(rows, page=number)["items"]]
    actual = []
    number = 1
    while number is not None:
        result = compact_page(rows, view="listings", page=number, limit=17)
        assert result["total"] == 224 and len(result["items"]) <= 17
        actual.extend(row["id"] for row in result["items"])
        number = result["next_page"]
    assert actual == expected and len(set(actual)) == 224
    members = compact_page(rows, view="listings", group_id=group["id"], page=4, limit=50)
    assert members["total"] == 198 and members["next_page"] is None
    assert [row["id"] for row in members["items"]] == [row.id for row in variants[150:]]


def test_summary_group_filters_preserve_counts_and_do_not_expand_matches():
    rows = [product(1, "组合", raw_status="PUBLISHED", markets=[MarketSnapshot(id="1", site_id="RU")]),
            product(2, "组合", raw_status="DISABLED"), product(3)]
    group_id = compact_page(rows)["groups"][0]["id"]
    for view in ("groups", "listings"):
        result = compact_page(rows, view=view, group_id=group_id, query="sku-1", status="PUBLISHED", market="RU")
        assert [item["id"] for item in result["items"]] == ["id-1"]
        assert result["groups"][0]["total_count"] == 2
        assert result["groups"][0]["matched_count"] == 1
        assert result["next_page"] is None
        assert compact_page(rows, view=view, group_id="不存在")["items"] == []
        assert compact_page(rows, view=view, page=100)["next_page"] is None


def test_field_pages_keep_scope_order_and_only_serialize_requested_business_values():
    rows = [product(i, "组合", content={"description": "不需要的长说明" * 10000,
                                       "attributes": [{"name": "尺寸", "value": str(i), "unit": "cm"}]})
            for i in range(4)]
    rows.append(product(5, "其他组合"))
    group_id = compact_page(rows)["groups"][0]["id"]
    before = [row.model_dump() for row in rows]
    first = compact_page(rows, view="listings", group_id=group_id, limit=2,
                         fields=["content.attributes", "buyer_links"])
    second = compact_page(rows, view="listings", group_id=group_id, limit=2, page=2,
                          fields=["content.attributes", "buyer_links"])
    validated = OnlineReadResult.model_validate({"ok": True, **first})
    assert validated.records[0].values == {"content.attributes": rows[0].content["attributes"], "buyer_links": []}
    assert validated.items == []
    assert first["next_page"] == 2 and second["next_page"] is None
    assert first["listing_total"] == first["total"] == 4
    assert [row["id"] for p in (first, second) for row in p["records"]] == ["id-0", "id-1", "id-2", "id-3"]
    assert all(row["group_id"] == group_id for p in (first, second) for row in p["records"])
    assert not {"snapshot", "content", "prices", "stocks", "capabilities"} & first["records"][0].keys()
    assert "不需要的长说明" not in validated.model_dump_json()
    assert [row.model_dump() for row in rows] == before


def test_field_projection_preserves_arrays_null_zero_and_missing_paths():
    row = product(1, "组合", content={"zero": 0, "unknown": None, "empty": "", "flag": False,
                                   "attributes": [{"name": "尺寸", "value": "25", "unit": "cm"}]},
                  prices=[{"id": "base", "label": "基础价", "amount": "0", "currency": "CNY"}],
                  stocks=[{"id": "warehouse", "label": "仓库", "quantity": None, "warehouse_id": "W1"}],
                  details_state="failed", errors=["详情读取失败，保留旧快照"])
    result = project_listing_fields(row, ["prices", "stocks", "content.zero", "content.unknown",
                                         "content.empty", "content.flag", "content.absent", "content.attributes"])
    assert result["values"]["content.zero"] == 0
    assert result["values"]["content.unknown"] is None
    assert result["values"]["content.empty"] == "" and result["values"]["content.flag"] is False
    assert "content.absent" not in result["values"] and result["missing_fields"] == ["content.absent"]
    assert result["values"]["prices"][0]["currency"] == "CNY"
    assert result["values"]["stocks"][0]["warehouse_id"] == "W1"
    assert result["values"]["content.attributes"] == row.content["attributes"]
    assert result["details_state"] == "failed" and result["errors"] == row.errors
    with pytest.raises(ValueError, match="整体读取 content.attributes"):
        project_listing_fields(row, ["content.attributes.value"])
    with pytest.raises(ValueError, match="整体读取 prices"):
        project_listing_fields(row, ["prices.amount"])


@pytest.mark.parametrize("path", ["snapshot", "snapshot.offer", "credentials", "content..title",
                                 "content.attributes[0]", "content.*", " content.title"])
def test_field_selection_rejects_private_or_expression_paths_even_with_empty_results(path):
    with pytest.raises(ValueError):
        OnlineReadRequest(fields=[path])
    with pytest.raises(ValueError):
        compact_page([], fields=[path])


def test_field_selection_accepts_public_fields_without_scenario_enumerations():
    assert OnlineReadRequest(fields=["content.unanticipated_field", "title", "title"]).fields == [
        "content.unanticipated_field", "title"]
    assert compact_page([], fields=["content.unanticipated_field"])["records"] == []


@pytest.mark.parametrize("fields,parent,child", [
    (["content", "content.attributes"], "content", "content.attributes"),
    (["content.attributes", "stocks", "content"], "content", "content.attributes"),
    (["capabilities.stock.enabled", "capabilities.stock"], "capabilities.stock", "capabilities.stock.enabled"),
    (["content.extra.nested", "content"], "content", "content.extra.nested"),
])
def test_overlapping_fields_rejected_before_read_even_for_empty_pages(fields, parent, child):
    for read in (lambda: OnlineReadRequest(fields=fields), lambda: compact_page([], fields=fields)):
        with pytest.raises(ValueError) as exc:
            read()
        assert f"{parent} 已包含 {child}" in str(exc.value)
        assert f"删除 {parent} 并保留所需子路径" in str(exc.value)


def test_sibling_paths_and_similar_prefixes_do_not_overlap():
    fields = ["content.title", "content.title_extra", "content.titles.value",
              "capabilities.stock", "capabilities.content", "stocks", "stocks"]
    assert OnlineReadRequest(fields=fields).fields == fields[:-1]


def test_warranty_and_stock_projection_pages_all_skus_without_unrelated_content():
    """74 个 SKU 的读取保留版本和逐项权限，不受无关长描述、图片及属性影响。"""
    from erp_web.schemas.online_products import Capability

    fields = ["content.guarantee_period", "stocks", "capabilities.content", "capabilities.stock"]
    rows = [product(i, "组合", version=f"version-{i}",
        content={"description": "无关长描述" * 1000, "pictures": ["https://example.com/" + "x" * 200] * 30,
                 "attributes": [{"name": "无关属性", "value": "x" * 1000}],
                 "guarantee_period": None if i == 73 else {"timePeriod": 1, "timeUnit": "MONTH"}},
        stocks=[{"id": "warehouse", "label": "仓库", "quantity": None if i == 73 else 0,
                 "warehouse_id": "W1", "writable": i != 73}],
        capabilities={"stock": Capability(enabled=i != 73, reason="库存不可写" if i == 73 else ""),
                      "content": Capability(enabled=True, fields=["guarantee_period"]),
                      "price": Capability(enabled=True, scope="无关价格能力")}) for i in range(74)]
    group_id = compact_page(rows)["groups"][0]["id"]
    pages = [OnlineReadResult.model_validate({"ok": True, **compact_page(
        rows, view="listings", group_id=group_id, fields=fields, page=number, limit=50)}) for number in (1, 2)]
    assert [p.next_page for p in pages] == [2, None]
    records = [r for p in pages for r in p.records]
    assert [(r.id, r.version) for r in records] == [(r.id, r.version) for r in rows]
    assert all(set(r.values) == set(fields) and not r.missing_fields for r in records)
    assert records[0].values["stocks"][0]["quantity"] == 0
    assert records[-1].values["stocks"][0]["quantity"] is None
    assert records[-1].values["content.guarantee_period"] is None
    assert records[-1].values["capabilities.stock"]["enabled"] is False
    assert records[-1].values["capabilities.stock"]["reason"] == "库存不可写"
    for p in pages:
        encoded = p.model_dump_json()
        assert len(encoded.encode()) < 64 * 1024
        assert "无关" not in encoded and "example.com" not in encoded
