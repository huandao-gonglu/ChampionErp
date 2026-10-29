"""默认模板在统一核价入口生效，预览不写库，既有参数不被替换。"""
from copy import deepcopy
from functools import partial

import pytest

from erp_web.context import get_context
from erp_web.facades.draft_pricing_facade import price_draft_payload
from erp_web.product_model.defaults import default_pricing
from erp_web.runtime_units.draft_pricing import price_draft
from erp_web.runtime_units.draft_pricing_capabilities import (
    DraftPricingCapabilityScope, draft_pricing_apply, draft_pricing_preview,
)
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_pricing import DraftPricingRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_draft_pricing import pricing_draft, preview_and_apply  # noqa: F401


@pytest.fixture
def fresh_pricing(pricing_draft, monkeypatch):
    store, draft, _ = pricing_draft
    draft["pricing"] = default_pricing("yandex")
    draft["target_sites"] = [target for target in draft["target_sites"] if target["platform"] == "yandex"]
    for index, row in enumerate(draft["sku_items"]):
        row["selected"] = index < 2
    _, error, _ = store.save_draft_content(draft)
    assert error is None
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.fetch_pricing_exchange_rates",
        lambda *_args, **_kwargs: {"ok": True, "rates": {"usd_cny_rate": 7, "mxn_usd_rate": 20, "rub_cny_rate": 12}})
    settings = get_context().config.mutate_pricing_template("save", {
        "name": "默认费用", "set_default": True,
        "parameters": {
            "common": {"domestic_freight_cny": 5, "packaging_cost_cny": 2.5},
            "targets": {"yandex:global": {"payment_fee_percent": 1, "return_rate_percent": 10}},
        },
    })
    return store, settings["items"][0]


def body(**kwargs):
    return DraftPricingRequest(draft_id="pricing-draft", **kwargs)


def test_http_and_ai_first_preview_share_default_and_apply_persists_source(fresh_pricing, monkeypatch):
    store, template = fresh_pricing
    before = get_context().db.load_draft_model("pricing-draft")
    captured = []

    def calculate(payload):
        captured.append(deepcopy(payload))
        return calculate_sku_prices(payload)

    shared = partial(price_draft, calculator=calculate)
    monkeypatch.setattr("erp_web.facades.draft_pricing_facade.price_draft", shared)
    monkeypatch.setattr("erp_web.runtime_units.draft_pricing_capabilities.price_draft", shared)
    http, status = price_draft_payload({"draft_id": "pricing-draft"}, apply=False)
    ai = draft_pricing_preview(body(), DraftPricingCapabilityScope(store))
    assert status == 200 and not http["errors"] and not ai.errors
    assert captured[0] == captured[1]
    for item in captured[0]["items"]:
        assert item["input"]["common"]["domestic_freight_cny"] == 5
        assert item["input"]["common"]["packaging_cost_cny"] == 2.5
        target = item["input"]["targets"][0]
        assert target["payment_fee_percent"] == 1
        assert target["return_rate_percent"] == 10
    assert get_context().db.load_draft_model("pricing-draft") == before
    restored, status = price_draft_payload({"draft_id": "pricing-draft"}, apply=False, restore=True)
    assert status == 200 and restored["preview_id"] == ai.preview_id
    applied = draft_pricing_apply(body(preview_id=ai.preview_id), DraftPricingCapabilityScope(store),
        AiExecutionContext.create(timeout_seconds=30, budget_profile="test"))
    assert applied.applied
    assert len(captured) == 2
    saved = get_context().db.load_draft_model("pricing-draft")
    assert saved["pricing"]["template_source"] == {"id": template["id"], "name": template["name"]}
    assert saved["pricing"]["common"]["domestic_freight_cny"] == 5
    assert saved["pricing"]["targets"]["yandex:global"]["return_rate_percent"] == 10
    # 默认模板被删除后仍沿用草稿快照，重新核价不会丢失费用及来源。
    get_context().config.mutate_pricing_template("delete", {"id": template["id"]})
    again = preview_and_apply(body(), product_store=store)
    assert again["draft"]["pricing"]["template_source"] == saved["pricing"]["template_source"]
    assert again["draft"]["pricing"]["common"] == saved["pricing"]["common"]


def test_explicit_zero_and_sku_overrides_win_over_default(fresh_pricing):
    store, _ = fresh_pricing
    draft = get_context().db.load_draft_model("pricing-draft")
    draft["sku_items"][0]["pricing_overrides"] = {"common": {"packaging_cost_cny": 8}}
    store.save_draft_content(draft)
    result = preview_and_apply(body(common={"domestic_freight_cny": 0},
        targets={"yandex:global": {"return_rate_percent": 0}}), product_store=store)
    assert result["applied"]
    pricing = result["draft"]["pricing"]
    assert pricing["common"]["domestic_freight_cny"] == 0
    assert pricing["common"]["packaging_cost_cny"] == 2.5
    assert pricing["targets"]["yandex:global"]["return_rate_percent"] == 0
    rows = list(result["sku_pricing"].values())
    assert rows[0]["common"]["packaging_cost_cny"] == 8
    assert rows[1]["common"]["packaging_cost_cny"] == 2.5


@pytest.mark.parametrize("saved", [
    {"common": {"domestic_freight_cny": 0}},
    {"targets": {"yandex:global": {"payment_fee_percent": 0}}},
    {"template_source": {"id": "deleted", "name": "历史模板"}},
    {"updated_at": "2026-10-01T00:00:00Z"},
])
def test_saved_pricing_without_quotes_is_not_replaced(fresh_pricing, saved):
    store, _ = fresh_pricing
    draft = get_context().db.load_draft_model("pricing-draft")
    draft["pricing"].update(saved)
    store.save_draft_content(draft)
    result = preview_and_apply(body(), product_store=store)
    assert result["applied"]
    pricing = result["draft"]["pricing"]
    assert pricing["common"]["domestic_freight_cny"] == 0
    assert pricing["common"]["packaging_cost_cny"] == 0
    assert pricing["template_source"] == saved.get("template_source")


@pytest.mark.parametrize("source", [None, {"id": "chosen", "name": "手动选择"}])
def test_explicit_template_source_does_not_implicitly_apply_default(fresh_pricing, source):
    store, _ = fresh_pricing
    result = preview_and_apply(body(template_source=source, common={"domestic_freight_cny": 7}), product_store=store)
    assert result["applied"]
    assert result["draft"]["pricing"]["template_source"] == source
    assert result["draft"]["pricing"]["common"]["domestic_freight_cny"] == 7
    assert result["draft"]["pricing"]["common"]["packaging_cost_cny"] == 0


@pytest.mark.parametrize("change", ["edit", "delete", "unset"])
def test_default_change_requires_new_preview(fresh_pricing, change):
    store, template = fresh_pricing
    preview = price_draft(body(), product_store=store)
    assert not preview["errors"]
    if change == "edit":
        template["parameters"]["common"]["domestic_freight_cny"] = 99
        get_context().config.mutate_pricing_template("save", {
            "id": template["id"], "name": template["name"], "parameters": template["parameters"], "set_default": True,
        })
    else:
        get_context().config.mutate_pricing_template("delete" if change == "delete" else "default",
            {"id": template["id"] if change == "delete" else ""})
    with pytest.raises(BusinessCapabilityError) as exc:
        price_draft(body(preview_id=preview["preview_id"]), product_store=store, apply=True,
            calculator=lambda _: pytest.fail("应用不得重新计算"))
    assert exc.value.code == "PRICING_PREVIEW_CHANGED"
    assert not get_context().db.load_draft_model("pricing-draft")["pricing"]["template_source"]


def test_default_change_during_calculation_rejects_preview(fresh_pricing):
    store, template = fresh_pricing

    def calculate(payload):
        result = calculate_sku_prices(payload)
        get_context().config.mutate_pricing_template("delete", {"id": template["id"]})
        return result

    with pytest.raises(BusinessCapabilityError) as exc:
        price_draft(body(), product_store=store, calculator=calculate)
    assert exc.value.code == "DRAFT_CHANGED"


def test_without_default_retains_builtin_initial_values(fresh_pricing):
    store, _ = fresh_pricing
    get_context().config.mutate_pricing_template("default", {"id": ""})
    result = preview_and_apply(body(), product_store=store)
    assert result["applied"]
    assert result["draft"]["pricing"]["template_source"] is None
    assert result["draft"]["pricing"]["common"]["domestic_freight_cny"] == 0
