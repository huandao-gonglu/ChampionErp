"""实际规格数据、草稿隔离与逐 SKU 发布恢复的业务回归。"""

from copy import deepcopy
from pathlib import Path

import pytest

from erp_web.product_model.sku_model import collected_skus, merge_collected_skus, normalize_product_skus
from erp_web.runtime_units.publish_context import PreparedPublishContext
from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
from erp_web.runtime_units.sku_publish_projection import grouping_contract, sku_context, sku_quote_errors, validate_grouping
from erp_web.schemas.category_definition import CategoryAttributeDefinition, CategoryDefinition
from tests.runtime_test_utils import temp_app_context


def product_fixture():
    skus, selected = [], []
    for index, (cost, weight) in enumerate(((20, 1), (70, 3))):
        dimensions = {"length_cm": "30", "width_cm": "20", "height_cm": "10", "weight_kg": str(weight)}
        skus.append({"id": f"fact-{index}", "name": f"规格 {index}", "cost_cny": str(cost), "package_dimensions": dimensions, "active": True})
        basis = {"cost_cny": str(cost), **dimensions, "domestic_freight_cny": "0", "packaging_cost_cny": "0", "other_cost_cny": "0"}
        selected.append({"sku_id": f"fact-{index}", "selected": True, "sku": f"SELL-{index}", "stock": "3",
                         "pricing": {"applied": True, "targets": {"ozon:global": {"calculation_basis": basis, "listing_currency": "RUB", "applied_price": {"amount": str(cost * 30), "currency": "RUB"}}}}})
    return {"product_id": "sku-product", "name": "多规格商品", "sku_items": skus,
            "source": {"title": "多规格商品", "source_url": "https://example.test/product"},
            "drafts": {"ozon": {"draft_id": "sku-draft", "title": "多规格商品", "grouping": {"mode": "separate", "name": "组"}, "sku_items": selected,
                                "target_sites": [{"platform": "ozon", "site": "global"}]}}}


class ItemBoundary:
    platform = "ozon"
    prepare_is_local_only = True

    def __init__(self):
        self.calls = []
        self.fail_once = True

    def prepare_product(self, product, config):
        return product

    def validate_draft(self, context, config):
        return {"ok": True, "errors": [], "warnings": []}

    def required_attributes_missing(self, context, config):
        return []

    def build_payload(self, context, config):
        return {"offer_id": context.draft["sku"], "cost": context.product["cost"], "weight": context.draft["package_dimensions"]["weight_kg"], "attributes": context.draft["attributes"]}

    def validate_payload(self, payload, config):
        return [] if payload.get("offer_id") else ["缺少卖家编码"]

    def publish_payload(self, payload, config):
        self.calls.append(deepcopy(payload))
        if payload["offer_id"] == "SELL-1" and self.fail_once:
            self.fail_once = False
            return {"ok": False, "status": "failed", "error": "平台拒绝该 SKU"}
        return {"ok": True, "status": "published", "offer_id": payload["offer_id"], "external_id": "remote-" + payload["offer_id"]}

    def map_publish_error(self, error):
        return {"summary": str(error), "retryable": False}


def context_for(product):
    draft = product["drafts"]["ozon"]
    return PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0], platform="ozon")


def test_source_sku_identity_and_manual_fact_survive_recollection():
    source = {"source_url": "https://detail.1688.com/offer/123.html?a=1", "currency": "CNY", "skus": [
        {"id": "100", "name": "红色 S", "price": "20", "stock": 0, "options": {"颜色": "红色", "尺寸": "S"}, "package_dimensions": {"length_cm": "46", "weight_kg": "4.5"}},
        {"id": "101", "name": "蓝色 M", "price": "30"},
    ]}
    original = collected_skus(source)
    original.extend(normalize_product_skus([{"id": "manual", "name": "人工补充规格", "active": True}]))
    assert original[0]["supplier_stock"] == "0"
    original[0]["cost_cny"] = "19"
    source["source_url"] = "https://detail.1688.com/offer/123.html?a=2"
    source["skus"] = [{**source["skus"][0], "price": "25", "package_dimensions": {"length_cm": "57", "weight_kg": "8"}}]
    merged = merge_collected_skus(original, source)
    assert merged[0]["id"] == original[0]["id"]
    assert merged[0]["cost_cny"] == "19"
    assert merged[0]["source_snapshot"]["cost_cny"] == "25"
    assert merged[0]["package_dimensions"]["length_cm"] == "57"
    assert merged[1]["active"] is False
    assert merged[2]["id"] == "manual" and merged[2]["active"] is True


def test_retired_index_based_sku_format_is_rejected():
    with pytest.raises(ValueError, match="退役"):
        normalize_product_skus([{"spec1": "红", "price": "10"}])
    with pytest.raises(ValueError, match="重复"):
        normalize_product_skus([{"id": "same"}, {"id": "same"}])


def test_each_sku_projects_own_cost_package_and_draft_overrides(tmp_path):
    with temp_app_context(tmp_path) as app:
        product = app.products.save_product(product_fixture())
        context = context_for(product)
        draft = context.draft
        draft["sku_items"][1]["overrides"] = {"cost_cny": "65", "package_dimensions": {"weight_kg": "2.5"}}
        from erp_web.product_model.sku_model import selected_skus
        fact, row = selected_skus(product, draft)[1]
        projected = sku_context(context, fact, row, grouping_contract(context))
        assert projected.product["cost"] == "65"
        assert projected.draft["package_dimensions"]["weight_kg"] == "2.5"
        assert product["sku_items"][1]["cost_cny"] == "70"
        assert sku_quote_errors(fact, row, draft, "ozon:global")
        payload = SkuGroupPublishingAdapter(ItemBoundary()).build_payload(context, {})
        assert [item["payload"]["cost"] for item in payload["items"]] == ["20", "65"]


def test_partial_publish_retries_only_failed_sku_and_preserves_remote_state(tmp_path):
    with temp_app_context(tmp_path) as app:
        product = app.products.save_product(product_fixture())
        leaf = ItemBoundary()
        adapter = SkuGroupPublishingAdapter(leaf)
        payload = adapter.build_payload(context_for(product), {})
        first = adapter.publish_payload(payload, {})
        assert first["status"] == "partial"
        assert len(leaf.calls) == 2
        # 普通保存拿着发布前的旧快照，也不能抹除后台已收到的远端结果。
        app.products.save_draft_detail(product["drafts"]["ozon"])
        assert app.products.sku_publication("sku-draft", "fact-0", "ozon:global")["external_id"] == "remote-SELL-0"
        second = adapter.publish_payload(payload, {})
        assert second["status"] == "published"
        assert [call["offer_id"] for call in leaf.calls] == ["SELL-0", "SELL-1", "SELL-1"]
        adapter.publish_payload(payload, {})
        assert len(leaf.calls) == 3
        changed = app.db.load_draft_model("sku-draft")
        changed["sku_items"][0]["sku"] = "NEW-CODE"
        with pytest.raises(ValueError, match="卖家编码"):
            app.db.upsert_draft_model(product["product_id"], "ozon", changed)


def test_unknown_outcome_is_never_recreated(tmp_path):
    class UnknownBoundary(ItemBoundary):
        def publish_payload(self, payload, config):
            self.calls.append(payload)
            raise TimeoutError("请求已发出，但未收到结果")
    with temp_app_context(tmp_path) as app:
        product = app.products.save_product(product_fixture())
        leaf = UnknownBoundary()
        adapter = SkuGroupPublishingAdapter(leaf)
        payload = adapter.build_payload(context_for(product), {})
        assert adapter.publish_payload(payload, {})["status"] == "outcome_unknown"
        assert adapter.publish_payload(payload, {})["status"] == "outcome_unknown"
        assert len(leaf.calls) == 2


def test_combination_uses_platform_variant_dimensions_not_source_names():
    source = product_fixture()
    draft = source["drafts"]["ozon"]
    draft["grouping"]["mode"] = "combined"
    definition = CategoryDefinition(platform="ozon", category_id="1", optional=(
        CategoryAttributeDefinition(id="group", name="Объединить на одной карточке"),
        CategoryAttributeDefinition(id="color", name="颜色", variation_role="variant"),
    ))
    context = context_for(source)
    context = PreparedPublishContext(**{**context.__dict__, "category_definition": definition})
    grouping = grouping_contract(context)
    projections = [sku_context(context, fact, row, grouping) for fact, row in zip(source["sku_items"], draft["sku_items"])]
    assert validate_grouping(context, grouping, projections)
    for row, color in zip(draft["sku_items"], ("红", "蓝")):
        row["attributes_by_target"] = {"ozon:global": {"color": color}}
    projections = [sku_context(context, fact, row, grouping) for fact, row in zip(source["sku_items"], draft["sku_items"])]
    assert validate_grouping(context, grouping, projections) == []
    assert all(item.draft["attributes"]["group"] == "组" for item in projections)


def test_new_collection_url_does_not_inherit_previous_product(tmp_path):
    with temp_app_context(tmp_path) as app:
        saved = app.products.save_product(product_fixture())
        assert app.products.collection_product("https://example.test/product?tracking=1")["product_id"] == saved["product_id"]
        fresh = app.products.collection_product("https://example.test/another")
        assert fresh["sku_items"] == []
        assert not fresh.get("product_id")


def test_real_1688_fifteen_skus_keep_package_units_and_exclude_page_noise():
    import json
    from erp_web.runtime_units.source_collect_parsers import extract_1688_context_data, extract_1688_attributes
    fixture = json.loads((Path(__file__).parent / "fixtures/1688_pet_house_skus.json").read_text())
    html = '<script>window.context=' + json.dumps(fixture, ensure_ascii=False) + '</script>'
    context = extract_1688_context_data(html)
    rows = context["skus"]
    assert len(rows) == 15
    assert {row["id"] for row in rows} == {str(row["skuId"]) for row in fixture["pieceWeightScale"]["pieceWeightScaleInfo"]}
    for row in rows:
        size = row["name"].split(" / ")[-1]
        expected = {"S": (46, 4.5), "M": (57, 8), "L": (63, 9.5), "XL": (85, 16.5)}[size]
        assert float(row["package_dimensions"]["length_cm"]) == expected[0]
        assert float(row["package_dimensions"]["weight_kg"]) == expected[1]
    attrs = extract_1688_attributes("00:00\n内容声明：非商品属性\n划线价格：价格说明", html)
    assert "00" not in attrs and "内容声明" not in attrs and "划线价格" not in attrs
    assert len(attrs) == 28


def test_successful_collection_replaces_source_attributes_without_erasing_manual_attributes():
    from erp_web.product_model.merge_model import merge_source_partial_result
    source = product_fixture()
    source['source']['attributes'] = {'00': '00', '材质': '旧材料'}
    source['attributes'] = {'人工备注': '保留'}
    updated = merge_source_partial_result(source, {'attributes': {'材质': '塑料'}}, {'success': True})
    assert updated['source']['attributes'] == {'材质': '塑料'}
    assert updated['attributes'] == {'人工备注': '保留'}


def test_pending_skus_keep_all_task_ids_and_resume_confirmation_without_republishing(tmp_path):
    class PendingBoundary(ItemBoundary):
        def publish_payload(self, payload, config):
            self.calls.append(payload)
            return {'ok': True, 'status': 'pending_confirmation', 'task_ids': ['task-' + payload['offer_id']], 'offer_id': payload['offer_id']}

        def poll_publish_status(self, result, config):
            if self.fail_once:
                self.fail_once = False
                raise TimeoutError('暂时无法读取任务')
            return {**result, 'ok': True, 'status': 'published', 'external_id': 'remote-' + result['offer_id']}

    with temp_app_context(tmp_path) as app:
        product = app.products.save_product(product_fixture())
        leaf = PendingBoundary()
        adapter = SkuGroupPublishingAdapter(leaf)
        payload = adapter.build_payload(context_for(product), {})
        pending = adapter.publish_payload(payload, {})
        assert pending['task_ids'] == ['task-SELL-0', 'task-SELL-1']
        uncertain = adapter.poll_publish_status(pending, {})
        assert uncertain['status'] == 'outcome_unknown'
        assert uncertain['task_ids'] == pending['task_ids']
        recovered = adapter.poll_publish_status(uncertain, {})
        assert recovered['status'] == 'published'
        assert len(leaf.calls) == 2


def test_sku_quote_rejects_changed_shared_manual_price_and_sales_destinations():
    product = product_fixture()
    draft = product['drafts']['ozon']
    fact, row = product['sku_items'][0], draft['sku_items'][0]
    quote = row['pricing']['targets']['ozon:global']
    quote['calculation_basis'].update(pricing_mode='manual', manual_price={'amount': '600', 'currency': 'RUB'})
    draft['pricing'] = {'targets': {'ozon:global': {'pricing_mode': 'manual', 'applied_price': {'amount': '700', 'currency': 'RUB'}}}}
    assert '手动售价' in sku_quote_errors(fact, row, draft, 'ozon:global')[0]
    draft['pricing'] = {}
    row['pricing']['targets']['mercadolibre:cbt'] = quote
    quote['sites_to_sell'] = [{'site_id': 'MLM', 'logistic_type': 'remote', 'price': '10'}]
    draft['target_sites'] = [{'platform': 'mercadolibre', 'site': 'CBT', 'sites_to_sell': [{'site_id': 'MLB', 'logistic_type': 'remote'}]}]
    assert '销售国家' in sku_quote_errors(fact, row, draft, 'mercadolibre:cbt')[0]
