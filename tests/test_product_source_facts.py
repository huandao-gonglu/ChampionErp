"""来源资料唯一归属、迁移冲突、并发版本和草稿引用的回归。"""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3

import pytest
from pydantic import ValidationError

from erp_web.product_model.source_facts import migrate_source_facts
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.product_facts import ProductSkuFactsUpdateRequest
from erp_web.schemas.draft_pricing import DraftPricingHttpRequest
from scripts.migrate_product_source_facts import migrate
from tests.test_draft_edit_capabilities import seed_draft, execution


def test_migration_preserves_product_and_removes_conflicts_without_guessing_cost():
    product = {"schema_version": 4, "cost": "14", "brand": "实物品牌", "sku_items": [
        {"id": "a", "cost_cny": "8", "package_dimensions": {"height_cm": "2"}},
        {"id": "b", "cost_cny": ""}, {"id": "c", "cost_cny": ""},
    ]}
    drafts = [{"upc": "", "package_dimensions": {"height_cm": "99"}, "brand": "草稿品牌", "model": "General", "pricing": {"common": {"battery": True}}, "sku_items": [
        {"sku_id": "a", "selected": True, "overrides": {"cost_cny": "9", "package_dimensions": {"height_cm": "3", "width_cm": "10"}, "image_asset_id": "pic"}, "pricing": {"applied": True}},
        {"sku_id": "b", "overrides": {"cost_cny": "12"}}, {"sku_id": "c", "overrides": {}}]},
        {"sku_items": [{"sku_id": "b", "overrides": {"cost_cny": "13"}}]}]
    original = deepcopy((product, drafts))
    current, cleaned, report = migrate_source_facts(product, drafts)
    assert (product, drafts) == original
    assert current["schema_version"] == 5 and "cost" not in current
    assert current["source"]["legacy_product_cost_cny"] == "14"
    assert current["brand"] == "实物品牌"
    assert current["sku_items"][0]["cost_cny"] == "8"
    assert current["sku_items"][0]["package_dimensions"] == {"height_cm": "2", "width_cm": "10"}
    assert current["sku_items"][0]["battery"] is True
    assert current["sku_items"][1]["cost_cny"] == current["sku_items"][2]["cost_cny"] == ""
    assert cleaned[0]["sku_items"][0]["overrides"] == {"image_asset_id": "pic"}
    assert "upc" not in cleaned[0] and "package_dimensions" not in cleaned[0]
    assert "brand" not in cleaned[0] and "battery" not in cleaned[0]["pricing"]["common"]
    assert any(row["action"] == "删除冲突草稿值，商品保持为空" for row in report)
    assert migrate_source_facts(current, cleaned)[:2] == (current, cleaned)


def test_database_migration_is_previewable_atomic_and_repeatable():
    connection = sqlite3.connect(":memory:")
    connection.executescript('CREATE TABLE products(product_id, product_json, source_json, updated_at); CREATE TABLE platform_drafts(draft_id, product_id, draft_json, updated_at);')
    product = {"schema_version": 4, "sku_items": [{"id": "a", "cost_cny": "7"}]}
    draft = {"sku_items": [{"sku_id": "a", "overrides": {"cost_cny": "9"}, "pricing": {"applied": True, "targets": {"x": {"calculation_basis": {"cost_cny": "9"}}}}, "publications": {"x": {"remote_id": "preserved"}}}]}
    connection.execute('INSERT INTO products VALUES(?,?,?,?)', ('p', json.dumps(product), '{}', 'before'))
    connection.execute('INSERT INTO platform_drafts VALUES(?,?,?,?)', ('d', 'p', json.dumps(draft), 'before'))
    preview = migrate(connection)
    assert connection.execute('SELECT updated_at FROM products').fetchone()[0] == 'before'
    assert migrate(connection, apply=True) == preview
    saved = json.loads(connection.execute('SELECT draft_json FROM platform_drafts').fetchone()[0])
    assert saved['sku_items'][0]['overrides'] == {}
    assert saved['sku_items'][0]['pricing']['applied'] is False
    assert saved['sku_items'][0]['pricing']['targets'] == draft['sku_items'][0]['pricing']['targets']
    assert saved['sku_items'][0]['publications'] == draft['sku_items'][0]['publications']
    assert migrate(connection, apply=True) == {}


def test_product_tool_updates_all_draft_readers_and_invalidates_quotes():
    app, draft_id = seed_draft()
    other_id = app.products.duplicate_draft_from_index(draft_id)[0]['draft']['draft_id']
    before = app.products.draft_record(draft_id)
    product = app.db.load_product_model(before['product_id'])
    toolset = build_global_chat_toolset(app)
    assert 'draft_sku_package_update' not in toolset.bindings
    arguments = {'product_id': product['product_id'], 'expected_updated_at': product['updated_at'], 'changes': [
        {'sku_id': 'sku-0', 'cost_cny': 18, 'package_dimensions': {'length_cm': 13, 'weight_kg': .5}, 'battery': True}]}
    context = replace(execution(draft_id), permissions=frozenset({'product.write', 'draft.read', 'draft.write'}))
    result = toolset.bindings['product_sku_facts_update'].executor(arguments, context)
    assert result['changed_count'] == 1 and result['updated_at'] != product['updated_at']
    saved = app.db.load_product_model(product['product_id'])
    assert saved['sku_items'][0]['cost_cny'] == '18.0'
    assert saved['sku_items'][0]['battery'] is True
    for identifier in (draft_id, other_id):
        draft = app.products.draft_record(identifier)
        assert draft['sku_items'][0]['pricing']['applied'] is False
        assert 'cost_cny' not in draft['sku_items'][0]['overrides']
        assert draft['sku_items'][0]['pricing']['targets'] == before['sku_items'][0]['pricing']['targets']
        assert draft['sku_items'][1]['pricing']['applied'] is True
    with pytest.raises(ValueError, match='资料已更新'):
        app.products.update_product_sku_facts(arguments)
    assert app.db.load_product_model(product['product_id']) == saved
    arguments['expected_updated_at'] = saved['updated_at']
    assert app.products.update_product_sku_facts(arguments)['changed_count'] == 0


@pytest.mark.parametrize('patch', [{}, {'length_cm': 0}, {'height_cm': -1}, {'weight_kg': float('nan')}, {'width_cm': None}, {'length_cm': True}, {'length_cm': 13, 'brand': '错误字段'}])
def test_invalid_product_facts_are_rejected(patch):
    with pytest.raises(ValidationError):
        ProductSkuFactsUpdateRequest(product_id='p', expected_updated_at='v', changes=[{'sku_id': 's', 'package_dimensions': patch}])


def test_draft_and_pricing_cannot_write_product_facts():
    app, identifier = seed_draft()
    before = app.products.draft_record(identifier)
    changed = deepcopy(before)
    changed['sku_items'][0]['overrides']['cost_cny'] = '999'
    _, error, status = app.products.save_draft_content(changed)
    assert status == 400 and error['error_code'] == 'PRODUCT_FACTS_READ_ONLY'
    assert app.products.draft_record(identifier) == before
    with pytest.raises(ValidationError):
        DraftPricingHttpRequest(draft_id=identifier, sku_updates=[{'sku_id': 'sku-0', 'overrides': {'cost_cny': 999}}])


def test_unknown_sku_rolls_back_entire_product_patch():
    app, identifier = seed_draft()
    product = app.db.load_product_model(app.products.draft_record(identifier)['product_id'])
    with pytest.raises(ValueError, match='不属于'):
        app.products.update_product_sku_facts({'product_id': product['product_id'], 'expected_updated_at': product['updated_at'],
            'changes': [{'sku_id': 'sku-0', 'cost_cny': 10}, {'sku_id': 'foreign', 'cost_cny': 20}]})
    assert app.db.load_product_model(product['product_id']) == product
