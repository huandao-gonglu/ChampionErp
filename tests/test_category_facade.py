from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.facades import category_facade
from erp_web.http_route_units import category_routes


def test_category_attrs_payload_preserves_validation_and_live_error_contract(
    monkeypatch,
) -> None:
    result, status = category_facade.category_attrs_payload({})
    assert status == 400
    assert result == {
        "ok": False,
        "error": "缺少 category_id",
        "error_code": "CATEGORY_ID_REQUIRED",
    }

    def fail_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("远端类目不可用")

    monkeypatch.setattr(category_facade, "fetch_category_attributes", fail_fetch)
    result, status = category_facade.category_attrs_payload(
        {"platform": "ozon", "category_id": "123"}
    )
    assert status == 400
    assert result["ok"] is False
    assert result["error"] == "远端类目不可用"
    assert result["error_code"] == "CATEGORY_LIVE_API_FAILED"


def test_category_search_payload_delegates_platform_dispatch_to_provider(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(
        platform: str,
        *,
        query: str,
        site: str,
        limit: int,
    ) -> list[dict[str, str]]:
        captured.update(
            platform=platform,
            query=query,
            site=site,
            limit=limit,
        )
        return [{"id": "94765"}]

    monkeypatch.setattr(category_facade, "search_categories_live", fake_search)
    result, status = category_facade.category_search_payload(
        {
            "platform": " OZON ",
            "country": "global",
            "keyword": "风扇",
            "limit": "3",
        }
    )

    assert status == 200
    assert captured == {
        "platform": "ozon",
        "query": "风扇",
        "site": "global",
        "limit": 3,
    }
    assert result == {
        "ok": True,
        "platform": "ozon",
        "site": "global",
        "query": "风扇",
        "source": "ozon_live",
        "results": [{"id": "94765"}],
    }


def test_category_ai_fill_payload_keeps_draft_response_shape(monkeypatch) -> None:
    context = {
        "product": {"product_id": "p-1"},
        "draft": {"draft_id": "d-1"},
        "platform": "mercadolibre",
        "site": "MLM",
    }
    updated_draft = {
        "attributes": {"BRAND": "Champion"},
        "validation_errors": ["MODEL"],
    }
    monkeypatch.setattr(
        category_facade,
        "load_required_draft_publish_context",
        lambda body: (context, None, 200),
    )
    monkeypatch.setattr(
        category_facade,
        "apply_ai_model_attribute_fill",
        lambda product, platform, record: (
            {"drafts": {"mercadolibre": updated_draft}},
            {"source": "ai_model", "ai_filled": ["BRAND"]},
        ),
    )
    monkeypatch.setattr(
        category_facade,
        "save_draft_target_listing_result",
        lambda supplied_context, draft: {
            "draft": {"draft_id": "d-1", "attributes": {}},
            "productContext": {"productId": "p-1"},
            "productsIndex": [{"productId": "p-1"}],
            "draftsIndex": [{"draftId": "d-1"}],
        },
    )

    result, status = category_facade.category_ai_fill_payload(
        {
            "draft_id": "d-1",
            "category_record": {"category_id": "MLM1"},
        }
    )

    assert status == 200
    assert result["draft"] == {"draft_id": "d-1", "attributes": {}}
    assert result["attributes"] == {"BRAND": "Champion"}
    assert result["need_review"] == ["MODEL"]
    assert result["fill_source"] == "ai_model"
    assert result["ai_filled"] == ["BRAND"]


def test_category_precheck_payload_preserves_category_contract(monkeypatch) -> None:
    product = {"product_id": "p-1"}
    record = {
        "category_id": "94765",
        "path_original": ["家电", "风扇"],
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        get_context().products,
        "load_required_product_from_body",
        lambda body: (product, None, 200),
    )

    def fake_precheck(
        supplied_product: dict[str, Any],
        platform: str,
        category_record: dict[str, Any],
    ) -> list[str]:
        captured.update(
            product=supplied_product,
            platform=platform,
            record=category_record,
        )
        return ["attributes.BRAND"]

    monkeypatch.setattr(
        category_facade,
        "validate_category_precheck",
        fake_precheck,
    )
    result, status = category_facade.category_precheck_payload(
        {
            "platform": "OZON",
            "site_id": "global",
            "category_id": "94765",
            "category_record": record,
        }
    )

    assert status == 200
    assert captured == {
        "product": product,
        "platform": "ozon",
        "record": record,
    }
    assert result == {
        "ok": True,
        "platform": "ozon",
        "site": "global",
        "category_id": "94765",
        "category_path": "家电 / 风扇",
        "category_record": record,
        "errors": ["attributes.BRAND"],
        "missing_fields": ["attributes.BRAND"],
    }


def test_category_route_only_validates_delegates_and_sends_status(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class Handler:
        path = "/api/category-search"

        @staticmethod
        def read_body() -> dict[str, Any]:
            return {"query": "风扇"}

        @staticmethod
        def send_json(payload: dict[str, Any], status: int = 200) -> None:
            calls.append({"payload": payload, "status": status})

    monkeypatch.setattr(
        category_routes.category_facade,
        "category_search_payload",
        lambda body: ({"ok": False, "error": body["query"]}, 409),
    )

    category_routes.handle_category_search(Handler())

    assert calls == [
        {
            "payload": {"ok": False, "error": "风扇"},
            "status": 409,
        }
    ]
