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

    monkeypatch.setattr(category_facade, "fetch_category_attribute_page", fail_fetch)
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
        "category_precheck": {"ok": True},
        "last_precheck": {"ok": True},
        "last_precheck_target": {"site": "MLM"},
    }
    saved_draft: dict[str, Any] = {}
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
    def fake_save_draft_target(
        supplied_context: dict[str, Any], draft: dict[str, Any]
    ) -> dict[str, Any]:
        assert supplied_context is context
        saved_draft.update(draft)
        return {
            "draft": {"draft_id": "d-1", "attributes": {}},
            "productContext": {"productId": "p-1"},
            "productsIndex": [{"productId": "p-1"}],
            "draftsIndex": [{"draftId": "d-1"}],
        }

    monkeypatch.setattr(
        category_facade,
        "save_draft_target_listing_result",
        fake_save_draft_target,
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
    assert saved_draft["category_precheck"] == {}
    assert saved_draft["last_precheck"] == {}
    assert saved_draft["last_precheck_target"] == {}


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
        "missing_fields": ["attributes.BRAND"],
    }


def test_category_precheck_payload_persists_semantic_result_for_draft(
    monkeypatch,
) -> None:
    context = {
        "product": {"product_id": "p-1"},
        "draft": {"draft_id": "d-1"},
        "platform": "mercadolibre",
        "site": "CBT",
    }
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        category_facade,
        "load_required_draft_publish_context",
        lambda body: (context, None, 200),
    )
    monkeypatch.setattr(
        category_facade,
        "validate_category_precheck",
        lambda product, platform, record: ["attributes.VOLTAGE"],
    )

    def fake_save(
        supplied_context: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        assert supplied_context is context
        saved.update(updates)
        return {"draft": {"draft_id": "d-1"}}

    monkeypatch.setattr(
        category_facade,
        "save_draft_target_listing_result",
        fake_save,
    )

    result, status = category_facade.category_precheck_payload(
        {
            "draft_id": "d-1",
            "category_id": "CBT455865",
            "category_record": {
                "category_id": "CBT455865",
                "category_path": "Electronics / Fans",
            },
        }
    )

    assert status == 200
    assert result["ok"] is True
    assert saved == {
        "category_precheck": {
            "ok": False,
            "platform": "mercadolibre",
            "site": "CBT",
            "category_id": "CBT455865",
            "category_path": "Electronics / Fans",
            "missing_fields": ["attributes.VOLTAGE"],
        }
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


# -- 同步 focused 类目匹配入口（阶段5） -------------------------------------


def test_category_match_payload_returns_typed_result_200(monkeypatch) -> None:
    typed: dict[str, Any] = {
        "ok": True,
        "status": "completed",
        "target": {"platform": "mercadolibre", "site": "MLM"},
        "selected_category_id": "MLM-100",
        "query": "ventilador",
        "candidates": [],
        "decision": {},
        "failure": None,
        "trace": {"task_run_id": "t-1"},
    }

    monkeypatch.setattr(
        category_facade,
        "load_category_match_subject",
        lambda body: (
            {"product_id": "p-1"},
            {},
            {"platform": "mercadolibre", "site": "MLM", "language": ""},
            None,
        ),
    )
    seen: dict[str, Any] = {}

    def fake_match(product: Any, draft: Any, target: Any, **kwargs: Any):
        seen["product"] = product
        seen["draft"] = draft
        seen["target"] = target
        return typed

    monkeypatch.setattr(category_facade, "match_category", fake_match)

    result, status = category_facade.category_match_payload(
        {"draft_id": "d-1", "platform": "mercadolibre", "site": "MLM"}
    )

    assert status == 200
    assert result == typed
    assert seen["product"] == {"product_id": "p-1"}
    assert seen["target"]["platform"] == "mercadolibre"


def test_category_match_payload_propagates_subject_error(monkeypatch) -> None:
    error_payload = (
        {"ok": False, "error": "缺少商品", "error_code": "PRODUCT_NOT_FOUND"},
        404,
    )
    monkeypatch.setattr(
        category_facade,
        "load_category_match_subject",
        lambda body: ({}, {}, {}, error_payload),
    )

    def should_not_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("subject 校验失败时不得运行类目匹配")

    monkeypatch.setattr(category_facade, "match_category", should_not_run)

    result, status = category_facade.category_match_payload(
        {"draft_id": "missing", "platform": "mercadolibre", "site": "MLM"}
    )
    assert (result, status) == error_payload


def test_category_match_sync_route_dispatches_and_is_registered(
    monkeypatch,
) -> None:
    assert "/api/v1/category-match" in category_routes.HANDLED_PATHS
    assert "/api/v1/category-match" in category_routes.POST_HANDLERS
    # 旧 run 协议路由已删除：同步端点是类目匹配唯一 HTTP 入口。
    assert not any(path.endswith("/runs") for path in category_routes.HANDLED_PATHS)

    calls: list[dict[str, Any]] = []

    class Handler:
        path = "/api/v1/category-match"

        @staticmethod
        def read_body() -> dict[str, Any]:
            return {"platform": "mercadolibre", "site": "MLM", "draft_id": "d-1"}

        @staticmethod
        def send_json(payload: dict[str, Any], status: int = 200) -> None:
            calls.append({"payload": payload, "status": status})

    monkeypatch.setattr(
        category_routes.category_facade,
        "category_match_payload",
        lambda body: ({"ok": True, "status": "completed"}, 200),
    )

    category_routes.handle_category_match(Handler())

    assert calls == [{"payload": {"ok": True, "status": "completed"}, "status": 200}]
