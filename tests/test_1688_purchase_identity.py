"""采集 → 商品持久化 → 冻结上架 → 采购身份链路。"""
from copy import deepcopy
import hashlib
import json

import pytest

from erp_web.context import get_context
from erp_web.product_model.sku_model import collected_skus, merge_collected_skus, effective_sku
from erp_web.product_model.alibaba_purchase_model import frozen_purchase_identity
from erp_web.runtime_units.source_collect_1688_api import parse_1688_api_product
from erp_web.runtime_units.source_collect_parsers import parse_1688_product
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from tests.test_1688_browser_parser import _sample_1688_html
from tests.test_order_procurement import job


def api_source():
    return parse_1688_api_product({"result": {"productID": 123, "subject": "测试商品", "skuInfos": [
        {"skuId": "100", "specId": "spec-red", "skuName": "红色", "attributes": {"颜色": "红色"}},
        {"skuId": "101", "specId": "spec-blue", "skuName": "蓝色", "attributes": {"颜色": "蓝色"}},
    ]}}, "https://detail.1688.com/offer/123.html", "123")


def frozen_job():
    value = job()
    source = api_source()
    facts = collected_skus(source)
    value["product"].update(source=source, sku_items=facts)
    value["platforms"]["yandex"]["result"]["sku_results"][0]["sku_id"] = facts[0]["id"]
    return value


def test_api_collection_persistence_and_publication_keep_exact_spec_identity():
    value = frozen_job()
    saved = get_context().products.save_product(value["product"])
    value["product"] = get_context().products.load_product_from_index(saved["product_id"])
    binding = bindings_from_publish_job(value)[0]
    assert binding.source.source_sku_id == "100"
    assert binding.source.source_offer_id == "123"
    assert binding.source.source_spec_id == "spec-red"
    assert not binding.source.purchase_block_reason
    assert value["product"]["sku_items"][1]["source_spec_id"] == "spec-blue"


def test_browser_collection_retains_explicit_spec_id_but_never_derives_it():
    html = _sample_1688_html().replace('"skuId": 1001,', '"skuId": 1001, "specId": "spec-red",', 1)
    value = parse_1688_product({"html": html, "text": "", "url": "https://detail.1688.com/offer/123456789.html"})
    assert value["sku_items"][0]["source_spec_id"] == "spec-red"
    assert value["sku_items"][0]["source_offer_id"] == "123456789"
    assert not frozen_purchase_identity(value, value["sku_items"][0], "yandex")["purchase_block_reason"]
    assert value["sku_items"][1]["source_spec_id"] == ""
    assert "specId" in frozen_purchase_identity(value, value["sku_items"][1], "yandex")["purchase_block_reason"]


@pytest.mark.parametrize("change,reason", [
    ("missing_spec", "specId"), ("wrong_offer", "商品编号"),
    ("duplicate_sku", "唯一"), ("wrong_spec", "规格标识"),
    ("changed_options", "上架规格已修改"), ("draft_options", "上架规格已修改"),
])
def test_publication_freezes_incomplete_or_conflicting_identity_reason(change, reason):
    value = frozen_job()
    product = value["product"]
    fact = product["sku_items"][0]
    if change == "missing_spec": fact["source_spec_id"] = ""
    elif change == "wrong_offer": fact["source_offer_id"] = "999"
    elif change == "duplicate_sku": product["source"]["skus"].append(deepcopy(product["source"]["skus"][0]))
    elif change == "wrong_spec": fact["source_spec_id"] = "spec-blue"
    elif change == "changed_options": fact["options"]["颜色"] = "蓝色"
    elif change == "draft_options": product["drafts"] = {"yandex": {"sku_items": [{"sku_id": fact["id"], "overrides": {"options": {"颜色": "蓝色"}}}]}}
    assert reason in bindings_from_publish_job(value)[0].source.purchase_block_reason


def test_recollection_updates_purchase_ids_without_carrying_old_spec_forward():
    source = api_source()
    old = collected_skus(source)
    source["skus"][0]["spec_id"] = "spec-red-v2"
    updated = merge_collected_skus(old, source)
    assert updated[0]["id"] == old[0]["id"]
    assert updated[0]["source_spec_id"] == updated[0]["source_snapshot"]["source_spec_id"] == "spec-red-v2"
    del source["skus"][0]["spec_id"]
    assert merge_collected_skus(updated, source)[0]["source_spec_id"] == ""
    with pytest.raises(ValueError, match="不支持覆盖"):
        effective_sku(old[0], {"overrides": {"source_spec_id": "different"}})


def test_missing_sku_id_cannot_be_replaced_with_spec_id():
    source = parse_1688_api_product({"result": {"skuInfos": [{"specId": "only-spec"}]}}, "https://detail.1688.com/offer/123.html", "123")
    with pytest.raises(ValueError, match="稳定编号"):
        collected_skus(source)


def test_legacy_binding_identity_remains_stable_and_bad_republication_is_distinct():
    legacy = bindings_from_publish_job(job())[0]
    old = legacy.model_dump(exclude={"publication_id", "id", "image_url"})
    for key in ("source_offer_id", "source_spec_id", "purchase_block_reason"):
        old["source"].pop(key)
    assert legacy.identity == hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    value = frozen_job()
    good = bindings_from_publish_job(value)[0]
    fact = value["product"]["sku_items"][0]
    value["product"]["drafts"] = {"yandex": {"sku_items": [{"sku_id": fact["id"], "overrides": {"options": {"颜色": "蓝色"}}}]}}
    bad = bindings_from_publish_job(value)[0]
    assert bad.identity != good.identity and bad.source.purchase_block_reason
