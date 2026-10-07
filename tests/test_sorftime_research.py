"""真实响应形状的离线回归；测试不得消耗 Sorftime 额度。"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import base64
import gzip
import json

import pytest

from erp_web.context import get_context
from erp_web.facades import product_research_facade
from erp_web.product_research_config import normalize_product_research_config
from erp_web.services import sorftime_client, product_research_sourcing as sourcing
from erp_web.services.product_research_methods import amazon_candidates
from erp_web.services.product_research_service import create_hot_product_run, ProductResearchRunRegistry

AMAZON = {"Asin": "B0DNTL7T64", "Title": "Under Sink Organizers", "Photo": "https://example.com/amazon.jpg",
          "Price": 2299, "Ratings": 4.6, "RatingsCount": 6627, "ReviewsCount": 0,
          "ListingSalesVolumeOfMonth": 12182, "UpdateDate": None}
SUPPLIER = {"ProductId": "692505958039", "Title": "水槽下收纳架", "Photo": "https://example.com/supplier.jpg",
            "Price": 15.3, "StoreName": "测试供货商", "MinOrderQuantity": 1, "ServiceScore": 4}
VARIANTS = [{"SkuId": "4913429996853", "SkuName": "双层黑色", "Price": 13.0, "Stock": 100,
             "Width": 27, "Height": 30, "Length": 23, "Weight": 0.8, "PkgSizeSource": "商家自填"}]


def setup_run(monkeypatch, tmp_path):
    calls = []
    def fake_call(self, endpoint, domain, params):
        calls.append((endpoint, domain, params))
        self.receipts.append({"endpoint": endpoint, "domain": domain, "request_consumed": 2, "request_left": 40})
        if domain == 1:
            return [deepcopy(AMAZON)]
        if endpoint.startswith("ProductSearch"):
            return [deepcopy(SUPPLIER)]
        if endpoint == "ProductRequest":
            return deepcopy(SUPPLIER)
        return deepcopy(VARIANTS)
    monkeypatch.setattr(sorftime_client.SorftimeClient, "call", fake_call)
    config = normalize_product_research_config({})
    config["search_providers"][0]["config_json"]["api_key"] = "test-key"
    get_context().config.save_app_config({"product_research": config})
    run = create_hot_product_run(tmp_path, {"keywords": ["kitchen organizer"], "markets": {"target_markets": ["amazon-us"]}}, config)
    assert run["status"] == "completed"
    return run, calls


def test_research_to_supplier_import_persists_and_is_idempotent(monkeypatch, tmp_path):
    run, calls = setup_run(monkeypatch, tmp_path)
    candidate = run["items"][0]
    assert candidate["price"] == {"amount": 22.99, "currency": "USD"}
    assert candidate["review_count"] == 6627
    assert candidate["monthly_sales"] == 12182
    assert "hot_score" not in candidate
    body = {"run_id": run["run_id"], "candidate_id": candidate["id"], "keyword": "水槽下收纳架"}
    result, status = product_research_facade.search_suppliers_payload(body)
    assert status == 200 and result["sourcing"]["items"][0]["price_cny"] == 15.3
    assert sourcing.search_suppliers(body)["cached"]
    assert len(calls) == 2
    # 从数据库恢复后仍可复用找货结果。
    restored = ProductResearchRunRegistry(get_context().db).get(run["run_id"])
    assert restored["items"][0]["sourcing"]["items"][0]["product_id"] == SUPPLIER["ProductId"]
    import_body = {**body, "supplier_id": SUPPLIER["ProductId"], "confirmed": True}
    result, status = product_research_facade.import_supplier_payload(import_body)
    assert status == 200
    saved = get_context().products.collection_product("https://detail.1688.com/offer/692505958039.html")
    assert saved["product_id"] == result["product_id"]
    assert saved["source"]["currency"] == "CNY"
    assert saved["sku_items"][0]["cost_cny"] == "13.0"
    assert saved["sku_items"][0]["source_sku_id"] == VARIANTS[0]["SkuId"]
    assert saved["sku_items"][0]["package_dimensions"]["weight_kg"] == ""
    assert saved["attributes"]["research_evidence"]["amazon_asin"] == AMAZON["Asin"]
    assert saved["attributes"]["research_evidence"]["supplier_variants"][0]["Weight"] == .8
    saved["sku_items"][0]["cost_cny"] = "12.50"
    get_context().products.save_product(saved)
    assert sourcing.import_supplier(import_body)["already_imported"]
    assert len(calls) == 4
    assert get_context().products.collection_product(saved["source"]["source_url"])["sku_items"][0]["cost_cny"] == "12.50"
    assert len(get_context().products.load_products_index()) == 1


def test_import_requires_confirmation_and_stored_supplier(monkeypatch, tmp_path):
    run, calls = setup_run(monkeypatch, tmp_path)
    body = {"run_id": run["run_id"], "candidate_id": run["items"][0]["id"], "supplier_id": "123456"}
    with pytest.raises(ValueError, match="确认"):
        sourcing.import_supplier(body)
    with pytest.raises(ValueError, match="已查询"):
        sourcing.import_supplier({**body, "confirmed": True})
    assert len(calls) == 1
    assert not get_context().products.load_products_index()


def test_missing_skus_stops_import(monkeypatch, tmp_path):
    run, _ = setup_run(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="SKU"):
        sourcing.supplier_product(SUPPLIER, [], run["items"][0], run["run_id"])
    assert not get_context().products.load_products_index()


def test_missing_metrics_and_invalid_asins_are_not_fabricated():
    result = amazon_candidates([{**AMAZON, "Price": -1, "Ratings": None, "RatingsCount": -1,
                                 "ListingSalesVolumeOfMonth": -1}, {**AMAZON, "Asin": "not-an-asin"}, AMAZON], "organizer", 10)
    assert len(result) == 1
    assert "price" not in result[0]
    assert result[0]["rating"] is None and result[0]["review_count"] is None
    assert result[0]["monthly_sales"] is None
    assert result[0]["source_url"] == "https://www.amazon.com/dp/B0DNTL7T64"


@pytest.mark.parametrize("compressed", [False, True])
def test_client_auth_receipts_and_decoding(monkeypatch, compressed):
    captured = []
    def send(request, **kwargs):
        captured.append((request, kwargs))
        payload = json.dumps({"Code": 0, "Data": [AMAZON], "RequestConsumed": 2, "RequestLeft": 48}).encode()
        return BytesIO(base64.b64encode(gzip.compress(payload)) if compressed else payload)
    monkeypatch.setattr(sorftime_client, "managed_urlopen", send)
    client = sorftime_client.SorftimeClient("private-test-secret")
    assert client.call("ProductSearchFromName", 1, {"Name": "organizer"}) == [AMAZON]
    req, kw = captured[0]
    assert req.get_header("Authorization") == "BasicAuth private-test-secret"
    assert "private-test-secret" not in req.full_url
    assert kw["request_context"].max_attempts == 1
    assert client.receipts[0]["request_consumed"] == 2


def test_client_failure_does_not_retry_or_leak_secret(monkeypatch):
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("secret-value private data")
    monkeypatch.setattr(sorftime_client, "managed_urlopen", send)
    with pytest.raises(ValueError, match="扣费情况未知") as exc:
        sorftime_client.SorftimeClient("secret-value").call("ProductRequest", 601, {"ProductId": "123456"})
    assert "secret-value" not in str(exc.value) and len(calls) == 1


@pytest.mark.parametrize("code", [402, 694, 10])
def test_client_business_error_is_not_empty_success(monkeypatch, code):
    monkeypatch.setattr(sorftime_client, "managed_urlopen", lambda *a, **k: BytesIO(json.dumps({"code": code, "message": "secret-value"}).encode()))
    with pytest.raises(ValueError, match=str(code)) as exc:
        sorftime_client.SorftimeClient("secret-value").call("CoinQuery", 1, {})
    assert "secret-value" not in str(exc.value)


def test_search_failure_marks_run_failed(monkeypatch, tmp_path):
    def fail(*args, **kwargs): raise ValueError("Sorftime：接口未开通。")
    monkeypatch.setattr(sorftime_client.SorftimeClient, "call", fail)
    config = normalize_product_research_config({})
    config["search_providers"][0]["config_json"]["api_key"] = "test-key"
    run = create_hot_product_run(tmp_path, {"keywords": ["kitchen organizer"]}, config)
    assert run["status"] == "failed" and not run["items"]
    assert "接口未开通" in run["source_status"][0]["error_message"]


def test_migrates_ai_provider_and_keeps_markets_and_history():
    config = normalize_product_research_config({"search_providers": [{"id": "old", "source_type": "ai_search", "config_json": {"provider_strategy": "ai_web_search"}}],
        "target_markets": [{"id": "my-us", "platform": "amazon", "site": "amazon.com", "display_name": "我的美国站", "search_methods": [{"method_id": "old", "enabled": True}]}]})
    assert config["target_markets"][0]["display_name"] == "我的美国站"
    assert config["target_markets"][0]["search_methods"][0]["method_id"] == "sorftime"
    assert [p["id"] for p in config["search_providers"]] == ["sorftime"]
    assert config["search_providers"][0]["config_json"]["api_key"] == ""


def test_candidate_operations_reject_concurrent_spend_without_holding_lock():
    registry = get_context().research
    with registry.candidate_operation("run", "candidate"):
        with pytest.raises(ValueError, match="正在处理"):
            with registry.candidate_operation("run", "candidate"):
                pytest.fail("不应允许重复调用")
    with registry.candidate_operation("run", "candidate"):
        pass


def test_empty_result_is_completed_without_fabricating_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(sorftime_client.SorftimeClient, "call", lambda *a, **k: [])
    config = normalize_product_research_config({})
    config["search_providers"][0]["config_json"]["api_key"] = "test-key"
    run = create_hot_product_run(tmp_path, {"keywords": ["unusual organizer"]}, config)
    assert run["status"] == "completed" and run["items"] == []
    assert run["source_status"][0]["status"] == "empty"
    assert "没有返回" in run["description"]


def test_duplicate_binding_does_not_charge_twice(monkeypatch, tmp_path):
    run, calls = setup_run(monkeypatch, tmp_path)
    config = normalize_product_research_config({})
    config["search_providers"][0]["config_json"]["api_key"] = "test-key"
    config["target_markets"][0]["search_methods"] *= 2
    create_hot_product_run(tmp_path, {"keywords": ["organizer"]}, config)
    assert len(calls) == 2


def test_persistence_failure_is_not_reported_as_success(monkeypatch):
    context = get_context()
    registry = context.research
    def fail(*args, **kwargs): raise OSError("磁盘无法写入")
    monkeypatch.setattr(context.db, "save_research_run", fail)
    with pytest.raises(OSError, match="磁盘"):
        registry.store({"run_id": "not-saved", "status": "completed", "items": []})
    assert registry.get("not-saved") is None
