"""复制后继承 SKU 报价，仍按当前成本和店铺币种进行预检。"""

from copy import deepcopy

from erp_web.context import get_context
from erp_web.product_model.sku_model import effective_sku
from erp_web.runtime_units.publish_context import PreparedPublishContext
from erp_web.runtime_units.publish_validation import _selected_price_errors
from erp_web.runtime_units.sku_publish_projection import (
    grouping_contract,
    sku_context,
    sku_quote_errors,
)
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from tests.runtime_test_utils import seed_store_currency
from tests.test_sku_workflow import product_fixture


def priced_draft():
    app = get_context()
    fingerprint = seed_store_currency("ozon", "RUB")
    product = product_fixture()
    draft = product["drafts"]["ozon"]
    for row in draft["sku_items"]:
        quote = row["pricing"]["targets"]["ozon:global"]
        quote["currency_fingerprint"] = fingerprint
        quote["calculation_basis"].update({
            "listing_currency": "RUB", "currency_fingerprint": fingerprint,
        })
        quote["calculation_fingerprint"] = pricing_calculation_fingerprint(
            quote["calculation_basis"]
        )
        row["publications"] = {"ozon:global": {"status": "published"}}
    app.products.save_product(product)
    return app, app.db.load_draft_model("sku-draft")


def price_errors(app, draft, row):
    product = app.db.load_product_model(draft["product_id"])
    product["drafts"] = {"ozon": deepcopy(draft)}
    fact = effective_sku(
        next(s for s in product["sku_items"] if s["id"] == row["sku_id"]), row
    )
    context = PreparedPublishContext(
        product=product,
        draft=product["drafts"]["ozon"],
        target=draft["target_sites"][0],
        platform="ozon",
    )
    projection = sku_context(context, fact, row, grouping_contract(context))
    return (
        sku_quote_errors(fact, row, draft, "ozon:global"),
        _selected_price_errors(projection.product, projection.draft),
    )


def test_copy_and_deselect_preserve_valid_per_sku_prices_and_currency():
    app, original = priced_draft()
    for index in range(2):
        result, error, status = app.products.duplicate_draft_from_index(original["draft_id"])
        assert error is None and status == 200
        copy_id = result["draft"]["draft_id"]
        app.products.update_draft_sku_selection(copy_id, [f"fact-{index}"])
        copied = app.db.load_draft_model(copy_id)
        for row, source in zip(copied["sku_items"], original["sku_items"]):
            assert row["pricing"] == source["pricing"]
            assert row["sku"] != source["sku"]
            assert row["publications"] == {}
        row = copied["sku_items"][index]
        assert row["selected"] is True
        assert price_errors(app, copied, row) == ([], [])
    assert app.db.load_draft_model(original["draft_id"]) == original


def test_missing_sku_quote_does_not_claim_store_currency_changed():
    app, draft = priced_draft()
    row = deepcopy(draft["sku_items"][0])
    row["pricing"] = {}
    sku_errors, currency_errors = price_errors(app, draft, row)
    assert sku_errors == ["请重新核价并应用此 SKU 的售价"]
    assert [error["code"] for error in currency_errors] == ["PRICING_STALE"]
    assert "缺少核价币种快照" in currency_errors[0]["message"]
    assert "店铺发布币种已变化" not in str(currency_errors)


def test_inherited_quote_still_rejects_changed_cost_and_store_currency():
    app, original = priced_draft()
    result, _, _ = app.products.duplicate_draft_from_index(original["draft_id"])
    copied = result["draft"]
    row = deepcopy(copied["sku_items"][0])
    row["overrides"]["cost_cny"] = "999"
    sku_errors, _ = price_errors(app, copied, row)
    assert "cost_cny" in sku_errors[0]
    seed_store_currency("ozon", "CNY")
    _, currency_errors = price_errors(app, copied, copied["sku_items"][0])
    assert [error["code"] for error in currency_errors] == ["STORE_CURRENCY_CHANGED"]
