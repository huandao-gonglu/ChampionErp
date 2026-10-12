"""热搜词查询扣量边界、持久缓存与失败回执的离线回归。"""
from io import BytesIO
import json
import sqlite3

import pytest

from erp_web.context import get_context
from erp_web.facades.product_research_facade import search_keywords_payload
from erp_web.product_research_config import normalize_product_research_config
from erp_web.schemas.requests import validate_request_payload, RequestValidationError
from erp_web.services import product_research_keywords as service, sorftime_client
from erp_web.stores.research_keyword_store import ResearchKeywordStore

BODY = {"market_id": "amazon-us", "keyword": "Kitchen organizer", "refresh": False}
ROW = {"Keyword": "kitchen organizer", "KeywordCNName": "厨房收纳", "Rank": 123,
       "SearchVolume": 4567, "RankChangeOfWeekly": -12, "Update": "2026-10-04"}
PATH = "/api/v1/product-research/keywords/search"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    config = normalize_product_research_config({})
    config["search_providers"][0]["config_json"]["api_key"] = "test-key"
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return BytesIO(json.dumps({"Code": 0, "Data": [ROW], "RequestConsumed": 5, "RequestLeft": 95}).encode())
    monkeypatch.setattr(sorftime_client, "managed_urlopen", send)
    store = ResearchKeywordStore(tmp_path / "keywords.sqlite3")
    return config, store, calls


def test_one_page_receipts_persistent_cache_and_explicit_refresh(setup, monkeypatch):
    config, store, calls = setup
    monkeypatch.setattr(service.time, "time", lambda: 1000)
    result = service.search_keywords(BODY, config, store)
    assert result["ok"] and not result["cached"]
    assert result["items"][0]["rank_change"] == -12
    assert result["quota_receipts"][0]["request_consumed"] == 5
    request, kw = calls[0]
    assert json.loads(request.data) == {"Pattern": {"Keyword": "Kitchen organizer"}, "PageIndex": 1, "PageSize": 100}
    assert kw["request_context"].max_attempts == 1
    cached = service.search_keywords({**BODY, "keyword": " kitchen  ORGANIZER "}, config, ResearchKeywordStore(store.path))
    assert cached["cached"] and cached["quota_receipts"] == [] and len(calls) == 1
    assert cached["fetched_at"] == result["fetched_at"]
    service.search_keywords({**BODY, "refresh": True}, config, store)
    assert len(calls) == 2
    monkeypatch.setattr(service.time, "time", lambda: 1000 + service.CACHE_SECONDS)
    assert not service.search_keywords(BODY, config, store)["cached"]
    assert len(calls) == 3
    config["search_providers"][0]["config_json"]["api_key"] = "other-key"
    assert not service.search_keywords(BODY, config, store)["cached"]
    assert len(calls) == 4


def test_concurrent_queries_are_rejected_before_network(setup, monkeypatch):
    config, store, calls = setup
    original = sorftime_client.managed_urlopen
    def send(*args, **kwargs):
        with pytest.raises(ValueError, match="正在查询"):
            service.search_keywords(BODY, config, ResearchKeywordStore(store.path))
        return original(*args, **kwargs)
    monkeypatch.setattr(sorftime_client, "managed_urlopen", send)
    assert service.search_keywords(BODY, config, store)["ok"]
    assert len(calls) == 1


def test_failed_query_keeps_receipt_does_not_retry_or_cache(setup, monkeypatch):
    config, store, calls = setup
    def send(*args, **kwargs):
        calls.append(1)
        return BytesIO(json.dumps({"code": 694, "RequestConsumed": 0, "RequestLeft": 3}).encode())
    monkeypatch.setattr(sorftime_client, "managed_urlopen", send)
    result = service.search_keywords(BODY, config, store)
    assert not result["ok"] and "694" in result["error"]
    assert result["quota_receipts"][0]["request_left"] == 3 and len(calls) == 1
    service.search_keywords(BODY, config, store)
    assert len(calls) == 2


def test_metrics_and_empty_results_are_not_fabricated(setup, monkeypatch):
    items = service.keyword_items([ROW, {**ROW, "Keyword": "another", "Rank": -1, "SearchVolume": None,
                                        "RankChangeOfWeekly": None}, ROW, {}])
    assert len(items) == 2 and items[1]["rank"] is None and items[1]["search_volume"] is None
    assert items[1]["rank_change"] is None
    config, store, calls = setup
    monkeypatch.setattr(sorftime_client, "managed_urlopen", lambda *a, **k: BytesIO(b'{"Code":11,"RequestConsumed":0,"RequestLeft":95}'))
    assert service.search_keywords(BODY, config, store)["items"] == []
    assert service.search_keywords(BODY, config, store)["cached"]


def test_invalid_input_never_sends_and_facade_uses_contract(setup):
    config, store, calls = setup
    for body in [{**BODY, "keyword": " "}, {**BODY, "keyword": "a" * 201}, {**BODY, "market_id": "amazon-uk"}, {**BODY, "refresh": "true"}]:
        with pytest.raises(ValueError):
            service.search_keywords(body, config, store)
    with pytest.raises(RequestValidationError):
        validate_request_payload({**BODY, "refresh": []}, endpoint=PATH)
    assert not calls
    get_context().config.save_app_config({"product_research": config})
    response, status = search_keywords_payload(validate_request_payload(BODY, endpoint=PATH))
    assert status == 200 and response["ok"]


def test_save_failure_reports_paid_receipt(setup, monkeypatch):
    config, store, _ = setup
    def fail(*args):
        raise sqlite3.OperationalError("磁盘无法写入")
    monkeypatch.setattr(store, "save", fail)
    response = service.search_keywords(BODY, config, store)
    assert not response["ok"] and "保存失败" in response["error"]
    assert response["quota_receipts"][0]["request_consumed"] == 5


def test_keyword_route_is_explicit_ui_only():
    from erp_web.http_route_units.product_research_routes import POST_HANDLERS, GET_HANDLERS
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
    assert PATH in POST_HANDLERS and PATH not in GET_HANDLERS
    assert [e.disposition for e in AI_CAPABILITY_COVERAGE_MANIFEST if e.path == PATH] == ["internal_only"]


def test_expired_lease_can_be_reclaimed_and_old_owner_cannot_release_new_one(setup):
    _, store, _ = setup
    first, _ = store.reserve("test-query", refresh=False, now=100)
    with pytest.raises(ValueError, match="正在查询"):
        store.reserve("test-query", refresh=True, now=101)
    second, _ = ResearchKeywordStore(store.path).reserve("test-query", refresh=False, now=281)
    assert first != second
    store.release("test-query", first)
    with pytest.raises(ValueError, match="正在查询"):
        store.reserve("test-query", refresh=False, now=282)
    store.release("test-query", second)


def test_release_failure_does_not_hide_quota_receipt(setup, monkeypatch):
    config, store, _ = setup
    def fail(*args):
        raise sqlite3.OperationalError("磁盘无法写入")
    monkeypatch.setattr(store, "release", fail)
    result = service.search_keywords(BODY, config, store)
    assert not result["ok"] and "占位释放失败" in result["error"]
    assert result["quota_receipts"][0]["request_consumed"] == 5
