"""统一草稿核价：真实 Store、确定性引擎和页面/AI 入口回归。"""
from copy import deepcopy
from functools import partial

import pytest
from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.facades.draft_pricing_facade import price_draft_payload
from erp_web.product_model import default_product_model
from erp_web.runtime_units.draft_pricing import price_draft
from erp_web.runtime_units.draft_pricing_capabilities import DraftPricingCapabilityScope, draft_pricing_preview, draft_pricing_apply
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.runtime_units.sku_publish_projection import sku_quote_errors
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_pricing import DraftPricingRequest, DraftPricingHttpRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.runtime_test_utils import seed_store_currency
from tests.pricing_test_utils import official_commission
from tests.yandex_logistics_support import PLAN, install_logistics
from tests.test_sku_pricing_batch import batch_setup, rules  # noqa: F401


@pytest.fixture
def pricing_draft(monkeypatch):
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.PricingCommissions", lambda _config, **_kwargs: official_commission)
    app = get_context()
    install_logistics(monkeypatch)
    from erp_web.services.pricing_shipping import PricingShipping
    class Shipping(PricingShipping):
        def __call__(self, common, target):
            return {'amount': '4', 'currency': 'CNY', 'minimum_price_cny': '0', 'billable_g': str(float(common['weight_kg']) * 1000),
                    'evidence': {'fulfillment_plan': deepcopy(PLAN)}, 'candidates': []}
    monkeypatch.setattr('erp_web.runtime_units.pricing_runtime.PricingShipping', Shipping)
    seed_store_currency("ozon", "CNY", identity={"client_id": "test", "api_key": "test"})
    seed_store_currency("yandex", "RUB", mode="manual")
    product = default_product_model()
    # 每个规格独立持有成本；历史商品级采购成本只留作待确认参考。
    product["source"]["legacy_product_cost_cny"] = "6.00"
    costs = ["8.80"] * 58 + ["15.00"] * 66 + ["9.50"] * 7 + ["12.00"] * 65 + ["6.00", "11.00"]
    product.update(product_id="pricing-product", name="核价回归商品", sku_items=[{
        "id": f"sku-{index}", "name": f"规格 {index}", "active": True, "cost_cny": cost,
        "package_dimensions": {"length_cm": "20", "width_cm": "15", "height_cm": "10", "weight_kg": "0.3"},
    } for index, cost in enumerate(costs)])
    product["drafts"] = {"ozon": {
        "draft_id": "pricing-draft", "title": "核价商品", "description": "回归描述",
        "target_sites": [{"platform": platform, "site": "global", "language": "ru-RU"} for platform in ("ozon", "yandex")],
        "sku_items": [{"sku_id": sku["id"], "sku": f"SELL-{index}", "selected": True, "stock": "5"} for index, sku in enumerate(product["sku_items"])],
        "pricing": {"common": {"domestic_freight_cny": 1, "packaging_cost_cny": 2, "other_cost_cny": 3,
            "exchange_rate_mode": "manual", "usd_cny_rate": 7, "mxn_usd_rate": 20, "rub_cny_rate": 12},
            "targets": {f"{platform}:global": {"shipping_quote_mode": "auto" if platform == "yandex" else "manual", "shipping_currency": "CNY", "shipping_amount": 4,
                "target_margin_percent": 30} for platform in ("ozon", "yandex")}},
    }}
    app.products.save_product(product)
    return app.products, app.db.load_draft_model("pricing-draft"), costs


def request(**values):
    return DraftPricingRequest(draft_id="pricing-draft", **values)


def test_template_selection_survives_plain_draft_save_before_pricing(pricing_draft):
    store, draft, _ = pricing_draft
    source = {"id": "selected-template", "name": "日常费用"}
    draft["pricing"]["template_source"] = source
    draft["pricing"]["common"]["domestic_freight_cny"] = 9
    draft["pricing"]["targets"]["ozon:global"]["target_margin_percent"] = 35
    _, error, status = store.save_draft_content(draft)
    assert error is None
    assert status == 200
    saved = get_context().db.load_draft_model("pricing-draft")
    assert saved["pricing"]["template_source"] == source
    assert saved["pricing"]["common"]["domestic_freight_cny"] == 9
    assert saved["pricing"]["targets"]["ozon:global"]["target_margin_percent"] == 35
    assert not any(row["pricing"].get("applied") for row in saved["sku_items"])


def test_template_source_persists_with_parameters_without_reading_latest_template(pricing_draft):
    store, _, _ = pricing_draft
    source = {"id": "used-template", "name": "原核价模板"}
    body = request(template_source=source, common={"domestic_freight_cny": 9})
    preview = price_draft(body, product_store=store)
    assert not get_context().db.load_draft_model("pricing-draft")["pricing"].get("template_source")
    result = price_draft(body.model_copy(update={"preview_id": preview["preview_id"]}), product_store=store,
                         apply=True, calculator=lambda _: pytest.fail("应用不得重算或套用最新模板"))
    assert result["applied"]
    saved = get_context().db.load_draft_model("pricing-draft")
    assert saved["pricing"]["template_source"] == source
    assert saved["pricing"]["common"]["domestic_freight_cny"] == 9
    # 普通草稿保存保留已应用的参数和来源，不要求来源模板仍存在。
    saved["title"] = "修改后保存草稿"
    _, error, _ = store.save_draft_content(saved)
    assert error is None
    again = get_context().db.load_draft_model("pricing-draft")
    assert again["pricing"] == saved["pricing"]
    assert again["sku_items"][0]["pricing"] == saved["sku_items"][0]["pricing"]
    retained = preview_and_apply(request(), product_store=store)
    assert retained["draft"]["pricing"]["template_source"] == source
    cleared = preview_and_apply(request(template_source=None), product_store=store)
    assert cleared["draft"]["pricing"]["template_source"] is None


def preview_and_apply(body, *, product_store, apply=True, **kwargs):
    preview = price_draft(body, product_store=product_store, **kwargs)
    if preview["errors"]:
        assert not preview.get("preview_id")
        return preview
    return price_draft(body.model_copy(update={"preview_id": preview["preview_id"]}),
                       product_store=product_store, apply=True, **kwargs)


def test_shipping_selection_is_saved_reused_and_explicit_null_restores_comparison(pricing_draft):
    store, _, _ = pricing_draft
    selection = {'warehouse_id': 31, 'route_id': 'requested-route'}
    applied = preview_and_apply(request(target_keys=['yandex:global'], targets={
        'yandex:global': {'shipping_selection': selection},
    }), product_store=store, apply=True)
    assert applied['applied']
    saved = get_context().db.load_draft_model('pricing-draft')
    assert saved['pricing']['targets']['yandex:global']['shipping_selection'] == selection
    preview = price_draft(request(target_keys=['yandex:global']), product_store=store, apply=False)
    assert preview['items'][0]['result']['results'][0]['calculation_basis']['shipping_selection'] == selection
    cleared = preview_and_apply(request(target_keys=['yandex:global'], targets={
        'yandex:global': {'shipping_selection': None},
    }), product_store=store, apply=True)
    assert cleared['applied']
    assert get_context().db.load_draft_model('pricing-draft')['pricing']['targets']['yandex:global']['shipping_selection'] is None


@pytest.mark.parametrize("changed", [
    None,
    {"warehouse_id": 32, "route_id": PLAN["route_id"]},
    {"warehouse_id": PLAN["publish_warehouse_id"], "route_id": "another-route"},
])
def test_changed_shipping_choice_blocks_publishing_old_applied_quote(pricing_draft, changed):
    store, _, _ = pricing_draft
    selection = {"warehouse_id": PLAN["publish_warehouse_id"], "route_id": PLAN["route_id"]}
    result = preview_and_apply(request(target_keys=["yandex:global"], targets={
        "yandex:global": {"shipping_selection": selection},
    }), product_store=store, apply=True)
    saved = result["draft"]
    fact = get_context().db.load_product_model("pricing-product")["sku_items"][0]
    row = saved["sku_items"][0]
    assert sku_quote_errors(fact, row, saved, "yandex:global") == []
    saved["pricing"]["targets"]["yandex:global"]["shipping_selection"] = changed
    assert sku_quote_errors(fact, row, saved, "yandex:global") == ["物流选择已变化，请重新核价并应用此 SKU 的售价"]


def test_198_skus_http_ai_and_apply_share_costs_and_parameters(pricing_draft, monkeypatch):
    store, before, costs = pricing_draft
    captured = []
    def calculate(body):
        captured.append(deepcopy(body))
        return calculate_sku_prices(body)
    shared = partial(price_draft, calculator=calculate)
    monkeypatch.setattr("erp_web.facades.draft_pricing_facade.price_draft", shared)
    monkeypatch.setattr("erp_web.runtime_units.draft_pricing_capabilities.price_draft", shared)
    body = {"draft_id": "pricing-draft", "common": {"domestic_freight_cny": 20}}
    preview, status = price_draft_payload(body, apply=False)
    ai = draft_pricing_preview(DraftPricingRequest(**body), DraftPricingCapabilityScope(store))
    assert status == 200 and preview["errors"] == [] and ai.errors == []
    assert captured[0] == captured[1]
    assert ai.sku_count == 198 and ai.target_count == 2 and not ai.applied
    assert get_context().db.load_draft_model("pricing-draft") == before
    assert len(preview["items"]) == 198
    for item, cost in zip(preview["items"], costs, strict=True):
        for quote in item["result"]["results"]:
            basis = quote["calculation_basis"]
            assert float(basis["cost_cny"]) == float(cost)
            assert float(basis["domestic_freight_cny"]) == 20
            assert float(basis["packaging_cost_cny"]) == 2
            assert float(basis["other_cost_cny"]) == 3
            assert quote["shipping_quote_mode"] == ("auto" if quote["platform"] == "yandex" else "manual")
            assert float(quote["shipping_amount"]) == 4
    applied = draft_pricing_apply(DraftPricingRequest(**body, preview_id=ai.preview_id), DraftPricingCapabilityScope(store), AiExecutionContext.create(timeout_seconds=30, budget_profile="test"))
    assert applied.applied and applied.sku_count == 198
    saved = get_context().db.load_draft_model("pricing-draft")
    product = get_context().db.load_product_model("pricing-product")
    for fact, row, preview_item in zip(product["sku_items"], saved["sku_items"], preview["items"], strict=True):
        assert row["pricing"]["applied"] is True
        for quote in preview_item["result"]["results"]:
            key = f"{quote['platform']}:{quote['site']}".lower()
            assert row["pricing"]["targets"][key]["applied_price"] == quote["applied_price"]
            assert sku_quote_errors(fact, row, saved, key) == []


def test_unsaved_selection_and_fee_overrides_preview_and_atomic_apply(pricing_draft):
    store, before, _ = pricing_draft
    edits = [{"sku_id": row["sku_id"], "selected": index < 2} for index, row in enumerate(before["sku_items"])]
    edits[0].update(pricing_overrides={"common": {"domestic_freight_cny": 8}, "targets": {"ozon:global": {"shipping_amount": 12}}})
    body = DraftPricingHttpRequest(draft_id="pricing-draft", expected_updated_at=before["updated_at"], sku_updates=edits,
        common={"domestic_freight_cny": 20})
    preview = price_draft(body, product_store=store)
    first = preview["items"][0]["result"]["results"][0]
    assert preview["errors"] == [] and preview["sku_count"] == 2
    assert float(first["calculation_basis"]["cost_cny"]) == 8.8
    assert float(first["calculation_basis"]["domestic_freight_cny"]) == 8
    assert float(first["calculation_basis"]["weight_kg"]) == 0.3
    assert first["shipping_amount"] == 12
    assert get_context().db.load_draft_model("pricing-draft") == before
    applied = price_draft(body.model_copy(update={"preview_id": preview["preview_id"]}), product_store=store, apply=True)
    assert applied["applied"]
    rows = applied["draft"]["sku_items"]
    assert sum(row["selected"] for row in rows) == 2
    assert rows[0]["overrides"] == {}
    assert rows[0]["pricing_overrides"]["targets"]["ozon:global"]["shipping_amount"] == 12


def test_ozon_commission_receives_actual_target_and_sku_brand(pricing_draft):
    store, draft, _ = pricing_draft
    apple = {"values": [{"dictionary_value_id": "1", "value": "Apple"}]}
    generic = {"values": [{"dictionary_value_id": "2", "value": "Generic"}]}
    draft["target_sites"][0]["attributes"] = {"85": apple}
    for index, row in enumerate(draft["sku_items"]):
        row["selected"] = index < 2
        if index == 1:
            row["attributes_by_target"] = {"ozon:global": {"85": generic}}
    store.save_draft_content(draft)
    captured = []
    def calculate(body):
        captured.extend(body["items"])
        return calculate_sku_prices(body)
    result = price_draft(request(), product_store=store, calculator=calculate)
    assert not result["errors"]
    assert captured[0]["input"]["targets"][0]["brand_attribute"] == apple
    assert captured[1]["input"]["targets"][0]["brand_attribute"] == generic
    assert "brand_attribute" not in captured[0]["input"]["targets"][1]


@pytest.mark.parametrize("field,value", [("cost_cny", ""), ("cost_cny", "nan"), ("weight_kg", "0"), ("width_cm", "")])
def test_only_real_missing_facts_reported_without_main_product_fallback(pricing_draft, field, value):
    store, _before, _ = pricing_draft
    product = get_context().db.load_product_model("pricing-product")
    target = product["sku_items"][0] if field == "cost_cny" else product["sku_items"][0]["package_dimensions"]
    target[field] = value
    get_context().db.upsert_product_model(product)
    with pytest.raises(BusinessCapabilityError) as caught:
        price_draft(request(), product_store=store, calculator=lambda _: pytest.fail("缺失事实不能请求外部报价"))
    assert caught.value.code == "PRICING_INPUT_INVALID"
    assert [(issue["sku_id"], issue["field"]) for issue in caught.value.details["errors"]] == [("sku-0", field)]


@pytest.mark.parametrize("change", ["draft", "product", "config", "logistics"])
def test_changes_during_calculation_prevent_save(pricing_draft, change):
    store, before, _ = pricing_draft
    config = {"ozon": {"currency_fingerprint": "before"}}
    def calculate(body):
        result = calculate_sku_prices(body)
        if change == "draft":
            edited = deepcopy(before)
            edited["title"] = "其他操作的新标题"
            store.save_draft_content(edited)
        elif change == "product":
            product = get_context().db.load_product_model("pricing-product")
            product["sku_items"][0]["cost_cny"] = "999"
            get_context().db.upsert_product_model(product)
        elif change == "logistics":
            get_context().yandex_logistics.current[0]["configuration_revision"] += 1
        else:
            config["ozon"]["currency_fingerprint"] = "after"
        return result
    with pytest.raises(BusinessCapabilityError, match="已改变"):
        preview_and_apply(request(), product_store=store, apply=True, calculator=calculate, store_config_loader=lambda: deepcopy(config))
    assert not any(row.get("pricing", {}).get("applied") for row in get_context().db.load_draft_model("pricing-draft")["sku_items"])


@pytest.mark.parametrize("invalid", ["missing_sku", "duplicate_sku", "missing_market", "loss", "invalid_money"])
def test_invalid_batch_never_partially_saves(pricing_draft, invalid):
    store, before, _ = pricing_draft
    def calculate(body):
        result = calculate_sku_prices(body)
        if invalid == "missing_sku": result["items"].pop()
        elif invalid == "duplicate_sku": result["items"][-1] = result["items"][0]
        elif invalid == "missing_market": result["items"][-1]["result"]["results"].pop()
        elif invalid == "loss": result["items"][-1]["result"]["results"][0]["is_loss"] = True
        else: result["items"][-1]["result"]["results"][0]["applied_price"]["amount"] = "0"
        return result
    if invalid in {"missing_sku", "duplicate_sku"}:
        with pytest.raises(BusinessCapabilityError):
            preview_and_apply(request(), product_store=store, apply=True, calculator=calculate)
    else:
        result = preview_and_apply(request(), product_store=store, apply=True, calculator=calculate)
        assert result["errors"] and not result["applied"]
    assert get_context().db.load_draft_model("pricing-draft") == before


def test_ai_schema_rejects_raw_cost_and_ambiguous_freight():
    for common in ({"cost_cny": 6}, {"purchase_cost": 6}, {"shipping_amount": 20}, {"domestic_freight": 20}):
        with pytest.raises(ValidationError): request(common=common)


def test_partial_target_apply_preserves_other_market(pricing_draft):
    store, _, _ = pricing_draft
    first = preview_and_apply(request(), product_store=store, apply=True)
    original = first["draft"]["sku_items"][0]["pricing"]["targets"]["yandex:global"]
    second = preview_and_apply(request(target_keys=["ozon:global"], targets={"ozon:global": {"target_margin_percent": 40}}), product_store=store, apply=True)
    assert second["applied"]
    assert second["draft"]["sku_items"][0]["pricing"]["targets"]["yandex:global"] == original


def test_http_errors_and_revision_conflict(pricing_draft):
    result, status = price_draft_payload({"draft_id": "pricing-draft", "expected_updated_at": "obsolete"}, apply=True)
    assert status == 409 and result["error_code"] == "DRAFT_CHANGED"
    result, status = price_draft_payload({"draft_id": "pricing-draft", "common": {"cost_cny": 6}}, apply=False)
    assert status == 400 and result["error_code"] == "PRICING_INPUT_INVALID"


def test_unready_store_currency_blocks_application(pricing_draft):
    store, before, _ = pricing_draft
    seed_store_currency("ozon", "", status="unresolved")
    result = preview_and_apply(request(), product_store=store, apply=True)
    assert not result["applied"] and result["errors"]
    assert get_context().db.load_draft_model("pricing-draft") == before


def test_explicit_sku_read_exposes_cost_and_source(pricing_draft):
    from erp_web.runtime_units.product_capabilities import ProductCapabilityScope, draft_attributes_read
    from erp_web.schemas.product_capabilities import DraftAttributesReadRequest
    store, before, costs = pricing_draft
    before["sku_items"][0]["overrides"]["cost_cny"] = "25"
    _, error, status = store.save_draft_content(before)
    assert status == 400 and error["error_code"] == "PRODUCT_FACTS_READ_ONLY"
    result = draft_attributes_read(DraftAttributesReadRequest(draft_id="pricing-draft", platform="ozon", site="global", scope="sku"), ProductCapabilityScope(store))
    assert result.sku_count == 198
    assert result.skus[0]["cost_cny"] == costs[0] and result.skus[0]["cost_source"] == "product_sku"
    assert result.skus[1]["cost_cny"] == costs[1] and result.skus[1]["cost_source"] == "product_sku"


def test_manual_price_uses_store_currency_when_page_currency_not_yet_resolved(pricing_draft):
    store, _, _ = pricing_draft
    result = preview_and_apply(request(target_keys=["ozon:global"], targets={"ozon:global": {
        "pricing_mode": "manual", "manual_price": {"amount": 150, "currency": ""},
    }}), product_store=store, apply=True)
    assert result["applied"] and not result["errors"]
    assert result["items"][0]["result"]["results"][0]["applied_price"] == {"amount": "150.00", "currency": "CNY"}
    product = get_context().db.load_product_model("pricing-product")
    assert sku_quote_errors(product["sku_items"][0], result["draft"]["sku_items"][0], result["draft"], "ozon:global") == []


@pytest.fixture
def mercado_pricing(pricing_draft, monkeypatch):
    store, source, _ = pricing_draft
    app = get_context()
    seed_store_currency("mercadolibre", "USD", identity={"user_id": "pricing-account", "access_token": "test-token"})
    config = app.config.load_store_config()
    config["mercadolibre"].update(listing_model="traditional_global_items", marketplace_bindings=[
        {"site_id": site, "logistic_type": "remote", "pricing_model": "price", "user_product": True}
        for site in ("MLM", "MLC")
    ])
    monkeypatch.setattr(app.config, "load_store_config", lambda: deepcopy(config))
    product = default_product_model()
    facts = app.db.load_product_model("pricing-product")["sku_items"]
    product.update(product_id="mercado-product", name="Mercado 核价样本", sku_items=[facts[0], facts[-1]])
    product["drafts"] = {"mercadolibre": {
        "draft_id": "mercado-draft", "language": "es", "title": "商品", "description": "描述",
        "target_sites": [{"platform": "mercadolibre", "site": "CBT", "language": "es", "sites_to_sell": []}],
        "sku_items": [source["sku_items"][0], source["sku_items"][-1]],
        "pricing": {"common": source["pricing"]["common"], "targets": {"mercadolibre:cbt": {
            "shipping_quote_mode": "manual", "shipping_currency": "USD", "shipping_amount": 2,
        }}},
    }}
    app.products.save_product(product)
    return store, app.db.load_draft_model("mercado-draft"), config


@pytest.mark.parametrize("mode", ["price", "net_proceeds"])
def test_mercado_selection_and_each_sku_amount_are_saved_atomically(mercado_pricing, mode):
    store, before, config = mercado_pricing
    for binding in config["mercadolibre"]["marketplace_bindings"]:
        binding["pricing_model"] = mode
    body = DraftPricingHttpRequest(draft_id="mercado-draft", target_selections={"mercadolibre:cbt": [
        {"site_id": "MLM", "logistic_type": "remote", "listing_type_id": "gold_special", "price": "999"},
        {"site_id": "MLC", "logistic_type": "remote"},
    ]})
    preview = price_draft(body, product_store=store)
    assert not preview["errors"]
    assert get_context().db.load_draft_model("mercado-draft") == before
    applied = price_draft(body.model_copy(update={"preview_id": preview["preview_id"]}), product_store=store, apply=True)
    assert applied["applied"]
    saved = get_context().db.load_draft_model("mercado-draft")
    assert all("price" not in target and "net_proceeds" not in target for target in saved["target_sites"][0]["sites_to_sell"])
    amounts = []
    for row in saved["sku_items"]:
        assert row["pricing"]["applied"] is True
        quote = row["pricing"]["targets"]["mercadolibre:cbt"]
        for destination in quote["sites_to_sell"]:
            assert mode in destination
            assert ("net_proceeds" if mode == "price" else "price") not in destination
        mexico = next(item for item in quote["sites_to_sell"] if item["site_id"] == "MLM")
        assert mexico["listing_type_id"] == "gold_special"
        amounts.append(mexico[mode])
    assert amounts[0] != amounts[1]


def test_unauthorized_mercado_destination_does_not_save_partial_selection(mercado_pricing):
    store, before, _ = mercado_pricing
    result = preview_and_apply(DraftPricingHttpRequest(draft_id="mercado-draft", target_selections={
        "mercadolibre:cbt": [{"site_id": "MLB", "logistic_type": "remote"}],
    }), product_store=store, apply=True)
    assert result["errors"] and not result["applied"]
    assert get_context().db.load_draft_model("mercado-draft") == before



def test_auto_shipping_remains_per_sku_and_passes_publish_price_validation(pricing_draft, batch_setup):
    store, _, _ = pricing_draft
    result = preview_and_apply(request(targets={"ozon:global": {"shipping_quote_mode": "auto"}, "yandex:global": {"shipping_quote_mode": "auto"}}), product_store=store, apply=True)
    assert result["applied"] and not result["errors"]
    saved = result["draft"]
    product = get_context().db.load_product_model("pricing-product")
    for fact, row in zip(product["sku_items"], saved["sku_items"], strict=True):
        for key in ("ozon:global", "yandex:global"):
            assert saved["pricing"]["targets"][key]["shipping_quote_mode"] == "auto"
            assert saved["pricing"]["targets"][key]["shipping_amount"] == 4
            assert sku_quote_errors(fact, row, saved, key) == []


def test_explicitly_cleared_manual_price_does_not_reuse_saved_amount(pricing_draft):
    store, _, _ = pricing_draft
    applied = preview_and_apply(request(target_keys=["ozon:global"], targets={"ozon:global": {
        "pricing_mode": "manual", "manual_price": {"amount": 150, "currency": "CNY"},
    }}), product_store=store, apply=True)
    assert applied["applied"]
    before = get_context().db.load_draft_model("pricing-draft")
    result = preview_and_apply(request(target_keys=["ozon:global"], targets={"ozon:global": {
        "pricing_mode": "manual", "manual_price": None,
    }}), product_store=store, apply=True)
    assert result["errors"] and not result["applied"]
    assert get_context().db.load_draft_model("pricing-draft") == before


def test_live_rates_keep_partial_manual_override_and_official_reference(pricing_draft, monkeypatch):
    store, draft, _ = pricing_draft
    for index, row in enumerate(draft["sku_items"]):
        row["selected"] = index == 0
    store.save_draft_content(draft)
    calls = []
    def rates(*_args, **_kwargs):
        calls.append(True)
        return {"ok": True, "source": "test_api", "rates": {"usd_cny_rate": 7.12,
            "mxn_usd_rate": 18.9, "rub_cny_rate": 12.15, "rub_usd_rate": 86.508,
            "currency_usd_rates": {"CNY": 7.12, "MXN": 18.9, "RUB": 86.508}}}
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.fetch_pricing_exchange_rates", rates)
    result = price_draft(request(common={"exchange_rate_mode": "live", "exchange_rate_overrides": {"usd_cny_rate": 7}}), product_store=store)
    assert not result["errors"] and len(calls) == 1
    calculation = result["items"][0]["result"]
    assert calculation["input"]["common"]["usd_cny_rate"] == 7
    assert calculation["input"]["common"]["rub_cny_rate"] == 12.15
    assert calculation["input"]["common"]["currency_usd_rates"]["RUB"] == pytest.approx(85.05)
    assert calculation["exchange_rates"]["rates"]["usd_cny_rate"] == 7.12
    assert calculation["exchange_rates"]["manual_overrides"] == {"usd_cny_rate": 7}


def test_all_manual_pairs_do_not_require_live_api_and_clear_can_return_to_live(pricing_draft, monkeypatch):
    store, draft, _ = pricing_draft
    for index, row in enumerate(draft["sku_items"]):
        row["selected"] = index == 0
    store.save_draft_content(draft)
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.fetch_pricing_exchange_rates", lambda *_args, **_kwargs: pytest.fail("三个手动汇率齐全时不依赖 API"))
    result = preview_and_apply(request(common={"exchange_rate_mode": "live", "exchange_rate_overrides": {
        "usd_cny_rate": 7, "mxn_usd_rate": 18, "rub_cny_rate": 12}}), product_store=store, apply=True)
    assert result["applied"]
    saved = get_context().db.load_draft_model("pricing-draft")
    assert saved["pricing"]["common"]["exchange_rate_overrides"]["usd_cny_rate"] == 7
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.fetch_pricing_exchange_rates", lambda *_args, **_kwargs: {
        "ok": True, "source": "API", "rates": {"usd_cny_rate": 7.2, "mxn_usd_rate": 19, "rub_cny_rate": 12.5}})
    cleared = price_draft(request(common={"exchange_rate_mode": "live", "exchange_rate_overrides": {}}), product_store=store)
    assert not cleared["errors"]
    assert cleared["items"][0]["result"]["input"]["common"]["usd_cny_rate"] == 7.2


def test_yandex_without_warehouse_mapping_cannot_apply_or_query_commission(pricing_draft, monkeypatch):
    from erp_web.services.pricing_shipping import PricingShipping
    store, before, _ = pricing_draft
    get_context().yandex_logistics.current.clear()
    monkeypatch.setattr('erp_web.runtime_units.pricing_runtime.PricingShipping', PricingShipping)
    def commission(*_): pytest.fail('没有已配置物流时不得查询官方佣金')
    monkeypatch.setattr('erp_web.runtime_units.pricing_runtime.PricingCommissions', lambda *_a, **_k: commission)
    result = preview_and_apply(request(target_keys=['yandex:global']), product_store=store, apply=True)
    assert not result['applied'] and result['errors']
    assert '暂无可用物流方案' in str(result['errors'])
    assert get_context().db.load_draft_model('pricing-draft') == before


def test_apply_only_uses_verified_server_preview_without_calculating(pricing_draft):
    store, _, _ = pricing_draft
    body = request()
    preview = price_draft(body, product_store=store)
    original_items = deepcopy(preview['items'])
    preview['items'][0]['result']['results'][0]['applied_price']['amount'] = '1.00'
    result = price_draft(body.model_copy(update={'preview_id': preview['preview_id']}), product_store=store,
                         apply=True, calculator=lambda _: pytest.fail('应用不得重新请求或计算'))
    assert result['applied'] and result['items'] == original_items
    assert result['metrics']['preview_reused'] is True
    with pytest.raises(BusinessCapabilityError, match='请先计算核价'):
        price_draft(body.model_copy(update={'preview_id': preview['preview_id']}), product_store=store, apply=True)


@pytest.mark.parametrize('change', ['missing', 'forged', 'expired', 'parameters', 'product', 'config', 'logistics', 'tariff'])
def test_apply_rejects_invalid_or_changed_preview_without_recalculating(pricing_draft, change):
    store, before, _ = pricing_draft
    body = request()
    preview = price_draft(body, product_store=store)
    token = preview['preview_id']
    if change == 'missing': token = ''
    elif change == 'forged': token = 'forged'
    elif change == 'expired':
        cache = get_context().pricing_cache.previews
        now = cache.clock()
        cache.clock = lambda: now + 601
    elif change == 'parameters':
        body = request(common={'domestic_freight_cny': 100})
    elif change == 'product':
        product = get_context().db.load_product_model('pricing-product')
        product['sku_items'][0]['cost_cny'] = '999'
        get_context().db.upsert_product_model(product)
    elif change == 'config':
        seed_store_currency('ozon', 'USD', identity={'client_id': 'other', 'api_key': 'test'})
    elif change == 'logistics':
        get_context().yandex_logistics.current[0]['configuration_revision'] += 1
    else:
        path = get_context().paths.data_dir / 'tariffs' / 'current.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"platforms": {"yandex": "new-version"}}')
    before = get_context().db.load_draft_model('pricing-draft')
    with pytest.raises(BusinessCapabilityError) as caught:
        price_draft(body.model_copy(update={'preview_id': token}), product_store=store, apply=True,
                    calculator=lambda _: pytest.fail('失效预览不得自动重算'))
    assert caught.value.code in {'PRICING_PREVIEW_REQUIRED', 'PRICING_PREVIEW_CHANGED'}
    assert get_context().db.load_draft_model('pricing-draft') == before


def test_return_loss_settings_persist_override_and_apply_without_recalculation(pricing_draft):
    store, before, _ = pricing_draft
    edits = [{"sku_id": row["sku_id"], "selected": index < 2} for index, row in enumerate(before["sku_items"])]
    edits[0]["pricing_overrides"] = {"targets": {"ozon:global": {
        "return_rate_percent": 20, "return_disposition": "resell", "return_handling_cost_cny": 40,
    }}}
    body = DraftPricingHttpRequest(draft_id="pricing-draft", sku_updates=edits, targets={
        "ozon:global": {"return_rate_percent": 10, "return_disposition": "discard", "return_handling_cost_cny": 0},
        "yandex:global": {"return_rate_percent": 5, "return_disposition": "resell", "return_handling_cost_cny": 20},
    })
    calls = []
    def calculator(payload):
        calls.append(payload)
        return calculate_sku_prices(payload)
    preview = price_draft(body, product_store=store, calculator=calculator)
    assert preview["errors"] == []
    changed = body.model_copy(deep=True)
    changed.targets["ozon:global"] = changed.targets["ozon:global"].model_copy(update={"return_rate_percent": 11})
    with pytest.raises(BusinessCapabilityError, match="参数|改变"):
        price_draft(changed.model_copy(update={"preview_id": preview["preview_id"]}), product_store=store, apply=True, calculator=calculator)
    applied = price_draft(body.model_copy(update={"preview_id": preview["preview_id"]}), product_store=store, apply=True, calculator=calculator)
    assert applied["applied"] and len(calls) == 1
    assert applied["items"] == preview["items"]
    saved = get_context().db.load_draft_model("pricing-draft")
    assert saved["pricing"]["targets"]["ozon:global"]["return_rate_percent"] == 10
    assert saved["sku_items"][0]["pricing_overrides"]["targets"]["ozon:global"]["return_rate_percent"] == 20
    again = price_draft(request(), product_store=store)
    quotes = again["sku_pricing"][saved["sku_items"][0]["sku_id"]]["targets"]
    assert quotes["ozon:global"]["return_loss"]["single_loss_cny"] == 47  # 国内 1 + 包装 2 + 国际 4 + 退回 40
    assert quotes["ozon:global"]["return_loss_cny"] == 11.75
    assert quotes["yandex:global"]["return_loss"]["return_rate_percent"] == 5
    fact = get_context().db.load_product_model("pricing-product")["sku_items"][0]
    row = saved["sku_items"][0]
    assert sku_quote_errors(fact, row, saved, "ozon:global") == []
    row["pricing_overrides"]["targets"]["ozon:global"]["return_handling_cost_cny"] = 41
    assert sku_quote_errors(fact, row, saved, "ozon:global") == ["货损预估已变化，请重新核价并应用此 SKU 的售价"]
