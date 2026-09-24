from __future__ import annotations

from copy import deepcopy

import pytest

from erp_web.context import get_context
from erp_web.facades.collect_facade import claim_products_payload
from erp_web.schemas.requests import validate_request_payload


def market(platform: str, site: str, language: str) -> dict[str, str]:
    return {"platform": platform, "site": site, "language": language}


@pytest.fixture
def library(monkeypatch):
    """使用隔离 SQLite 和授权配置，所有草稿走真实创建流程。"""
    context = get_context()
    for product_id in ("product-1", "product-2"):
        context.products.save_product({
            "product_id": product_id,
            "source": {
                "title": f"测试商品 {product_id}",
                "source_url": f"https://example.com/{product_id}",
                "source_platform": "1688",
            },
        })
    config = {"mercadolibre": {
        "listing_model": "traditional_global_items",
        "marketplace_bindings": [
            {"site_id": site, "logistic_type": logistic, "pricing_model": "price"}
            for site, logistic in (("MLM", "fulfillment"), ("MLM", "remote"), ("MLC", "remote"), ("MLB", "remote"))
        ],
    }}
    monkeypatch.setattr(context.config, "load_store_config", lambda: config)
    return context


def test_each_product_has_one_draft_per_language_and_only_selected_markets(library):
    targets = [
        market("mercadolibre", "MLM", "es"),
        market("mercadolibre", "MLC", "es"),
        market("yandex", "global", "ru-RU"),
        market("ozon", "global", "ru-RU"),
    ]
    result, status = claim_products_payload({"product_ids": ["product-1", "product-2"], "targets": targets})

    assert status == 200
    assert result["claimed_count"] == 2
    assert result["draft_count"] == 4
    assert len(result["draftsIndex"]) == 4
    for item in result["items"]:
        assert len(item["draft_ids"]) == 2
        drafts = [library.db.load_draft_model(draft_id) for draft_id in item["draft_ids"]]
        by_language = {draft["language"]: draft for draft in drafts}
        assert set(by_language) == {"es", "ru-RU"}
        assert all(draft["source_product_id"] == item["product_id"] for draft in drafts)
        assert all(draft["title"] == f"测试商品 {item['product_id']}" for draft in drafts)
        spanish = by_language["es"]
        assert spanish["platforms"] == ["mercadolibre"]
        assert len(spanish["target_sites"]) == 1
        assert spanish["target_sites"][0]["site"] == "CBT"
        assert spanish["target_sites"][0]["sites_to_sell"] == [
            {"site_id": "MLC", "logistic_type": "remote"},
            {"site_id": "MLM", "logistic_type": "remote"},
        ]
        russian = by_language["ru-RU"]
        assert russian["platforms"] == ["yandex", "ozon"]
        assert {(t["platform"], t["site"], t["language"]) for t in russian["target_sites"]} == {
            ("yandex", "global", "ru-RU"), ("ozon", "global", "ru-RU"),
        }


def test_agent_all_markets_uses_same_language_groups_and_sales_bindings(library):
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from erp_web.schemas.ai_trace import AiExecutionContext

    tool = build_global_chat_toolset(library).bindings["claim_products"]
    result = tool.executor({"product_ids": ["product-1", "product-2"], "all_markets": True},
                          AiExecutionContext.create(timeout_seconds=30, budget_profile="test"))
    assert result["claimed_count"] == 2
    assert len(result["drafts_index"]) == 6
    for item in result["items"]:
        drafts = [library.db.load_draft_model(draft_id) for draft_id in item["draft_ids"]]
        assert {draft["language"] for draft in drafts} == {"es", "pt-BR", "ru-RU"}
        assert sum(len(draft["target_sites"]) for draft in drafts) == 4
        brazil = next(draft for draft in drafts if draft["language"] == "pt-BR")
        assert brazil["target_sites"][0]["sites_to_sell"] == [{"site_id": "MLB", "logistic_type": "remote"}]


def test_single_row_multiple_languages_create_fresh_drafts_without_modifying_existing(library):
    targets = [market("mercadolibre", "MLM", "es"), market("mercadolibre", "MLB", "pt-BR")]
    body = {"product_ids": ["product-1", "product-1"], "targets": targets + targets}
    first, _ = claim_products_payload(body)
    assert first["claimed_count"] == 1
    assert first["draft_count"] == 2
    previous = {draft_id: deepcopy(library.db.load_draft_model(draft_id)) for draft_id in first["items"][0]["draft_ids"]}
    second, _ = claim_products_payload(body)
    assert second["draft_count"] == 2
    assert set(previous).isdisjoint(second["items"][0]["draft_ids"])
    for draft_id, draft in previous.items():
        assert library.db.load_draft_model(draft_id) == draft
        expected_site = "MLM" if draft["language"] == "es" else "MLB"
        assert draft["target_sites"][0]["sites_to_sell"] == [{"site_id": expected_site, "logistic_type": "remote"}]
    assert all(row["product_id"] == "product-1" for row in second["draftsIndex"])


@pytest.mark.parametrize("targets", [
    [], None, ["MLM"],
    [market("unknown", "global", "es")],
    [market("mercadolibre", "CBT", "es")],
    [market("mercadolibre", "invalid", "es")],
    [market("mercadolibre", "MLM", "ru-RU")],
    [market("ozon", "global", "ru-RU"), market("mercadolibre", "MLU", "es")],
])
def test_invalid_selection_is_rejected_before_creating_any_drafts(library, targets):
    result, status = claim_products_payload({"product_ids": ["product-1"], "targets": targets})
    assert status == 400
    assert result["ok"] is False
    assert library.products.load_drafts_index() == []


def test_http_request_requires_explicit_products_and_market_selection():
    for body in ({"product_ids": ["product-1"], "platform": "ozon"}, {"targets": [market("ozon", "global", "ru-RU")]}, {"product_ids": [], "targets": []}):
        with pytest.raises(ValueError):
            validate_request_payload(body, endpoint="/api/claim-products")


def test_missing_product_is_reported_with_actual_created_count(library):
    result, status = claim_products_payload({
        "product_ids": ["product-1", "missing"],
        "targets": [market("ozon", "global", "ru-RU")],
    })
    assert status == 200
    assert result["claimed_count"] == result["draft_count"] == 1
    assert result["items"][1] == {"product_id": "missing", "ok": False, "error": "商品不存在"}
