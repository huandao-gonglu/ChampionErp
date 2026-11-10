"""核价模板持久化、薄 HTTP 边界和独立汇率参考。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from erp_web.app_config import normalize_app_config
from erp_web.context import get_context
from erp_web.facades.pricing_workspace_facade import (
    mutate_pricing_template_payload,
    pricing_exchange_rates_payload,
)
from erp_web.facades.business_data_facade import business_query_payload
from erp_web.http_route_units import pricing_workspace_routes
from erp_web.schemas.requests import REQUEST_CONTRACTS, validate_request_payload


def parameters():
    return {"common": {"domestic_freight_cny": 2, "packaging_cost_cny": 0.8, "other_cost_cny": 0},
            "targets": {"ozon:global": {"payment_fee_percent": 1, "target_margin_percent": 30,
                        "pricing_mode": "margin", "shipping_quote_mode": "auto", "shipping_currency": "CNY"}}}


def save(name="日常直发", **kwargs):
    return mutate_pricing_template_payload({"name": name, "parameters": parameters(), **kwargs}, "save")


def test_templates_persist_without_product_facts_and_default_can_be_removed():
    first, status = save(set_default=True)
    assert status == 200 and first["default_template_id"] == first["saved_id"]
    identity = first["saved_id"]
    saved = first["items"][0]
    assert saved["name"] == "日常直发"
    assert saved["parameters"]["common"]["exchange_rate_overrides"] == {}
    assert "commission_percent" not in saved["parameters"]["targets"]["ozon:global"]
    listed, _ = business_query_payload('pricing.templates')
    assert listed["items"] == first["items"]
    assert get_context().config.load_app_config()["pricing_templates"]["items"] == first["items"]
    updated, status = save("日常直发 · 调整", id=identity, set_default=False)
    assert status == 200 and updated["default_template_id"] == ""
    assert len(updated["items"]) == 1
    assert updated["items"][0]["name"] == "日常直发 · 调整"
    mutate_pricing_template_payload({"id": identity}, "default")
    removed, status = mutate_pricing_template_payload({"id": identity}, "delete")
    assert status == 200 and removed["items"] == [] and removed["default_template_id"] == ""


@pytest.mark.parametrize("section,field,value", [
    ("common", "purchase_cost_cny", 10), ("common", "battery", True),
    ("common", "usd_cny_rate", 7), ("targets", "commission_percent", 20),
    ("targets", "category_id", "1"), ("targets", "shipping_evidence", {}),
])
def test_templates_reject_facts_official_commissions_and_automatic_receipts(section, field, value):
    values = parameters()
    destination = values["common"] if section == "common" else values["targets"]["ozon:global"]
    destination[field] = value
    result, status = mutate_pricing_template_payload({"name": "不能保存的数据", "parameters": values}, "save")
    assert status == 400 and result["error_code"] == "PRICING_TEMPLATE_INVALID"
    assert get_context().config.load_app_config()["pricing_templates"]["items"] == []


def test_only_explicit_manual_rates_are_persisted_and_invalid_rates_rejected():
    values = parameters()
    values["common"]["exchange_rate_overrides"] = {"usd_cny_rate": 7.05}
    result, status = mutate_pricing_template_payload({"name": "固定美元汇率", "parameters": values}, "save")
    assert status == 200
    assert result["items"][0]["parameters"]["common"]["exchange_rate_overrides"] == {"usd_cny_rate": 7.05}
    values["common"]["exchange_rate_overrides"]["usd_cny_rate"] = 0
    assert mutate_pricing_template_payload({"name": "无效", "parameters": values}, "save")[1] == 400


def test_concurrent_template_saves_keep_each_change_and_other_config():
    store = get_context().config
    original = store.load_app_config()["pricing_defaults"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: store.mutate_pricing_template("save", {"name": f"模板 {index}", "parameters": parameters()}), range(8)))
    config = store.load_app_config()
    assert {item["name"] for item in config["pricing_templates"]["items"]} == {f"模板 {index}" for index in range(8)}
    assert config["pricing_defaults"] == original
    assert normalize_app_config({})["pricing_templates"] == {"items": [], "default_template_id": ""}


def test_generic_settings_snapshot_cannot_overwrite_new_templates():
    store = get_context().config
    old_config = store.load_app_config()
    save(set_default=True)
    current = store.load_app_config()
    merged = store.merge_app_config_fields(current, old_config)
    assert merged["pricing_templates"] == current["pricing_templates"]


def test_template_http_round_trip_and_invalid_payload(backend_server):
    import requests

    url = backend_server + "/api/pricing/templates"
    before = backend_server.client.get(url, timeout=5)
    assert before.status_code == 200 and before.json()["ok"]
    created = backend_server.client.post(url + "/save", json={"name": "HTTP 核价模板", "parameters": parameters(), "set_default": True}, timeout=5)
    assert created.status_code == 200
    identity = created.json()["saved_id"]
    listed = backend_server.client.get(url, timeout=5).json()
    assert listed["default_template_id"] == identity
    assert any(item["id"] == identity for item in listed["items"])
    invalid = backend_server.client.post(url + "/save", json={"name": "", "parameters": parameters()}, timeout=5)
    assert invalid.status_code == 400 and invalid.json()["error_code"] == "MISSING_REQUIRED_FIELD"
    invalid_fees = parameters()
    invalid_fees["common"]["domestic_freight_cny"] = -1
    invalid = backend_server.client.post(url + "/save", json={"name": "无效费用", "parameters": invalid_fees}, timeout=5)
    assert invalid.status_code == 400 and invalid.json()["error_code"] == "PRICING_TEMPLATE_INVALID"
    removed = backend_server.client.post(url + "/delete", json={"id": identity}, timeout=5)
    assert removed.status_code == 200
    assert removed.json()["default_template_id"] == ""


def test_rates_reference_uses_existing_service_without_exposing_raw_response(monkeypatch):
    calls = []
    def fetch(*, force_refresh):
        calls.append(force_refresh)
        return {"ok": True, "rates": {"usd_cny_rate": 7.12}, "source": "API", "cached": False, "raw": {"private": "不会下发"}}
    monkeypatch.setattr("erp_web.facades.pricing_workspace_facade.fetch_pricing_exchange_rates", fetch)
    response, status = pricing_exchange_rates_payload(True)
    assert status == 200 and calls == [True] and "raw" not in response
    assert response["rates"]["usd_cny_rate"] == 7.12


def test_pricing_workspace_routes_are_explicit_and_have_request_contracts():
    assert pricing_workspace_routes.HANDLED_PATHS == frozenset(pricing_workspace_routes.POST_HANDLERS)
    assert pricing_workspace_routes.GET_API_ROUTES == frozenset(pricing_workspace_routes.GET_HANDLERS)
    assert pricing_workspace_routes.HANDLED_PATHS <= REQUEST_CONTRACTS.keys()
    payload = {"name": "日常直发", "parameters": parameters(), "set_default": True}
    assert validate_request_payload(payload, endpoint="/api/pricing/templates/save") == payload
