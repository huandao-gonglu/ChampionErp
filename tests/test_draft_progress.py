"""草稿 6 步准备进度、类目禁售落库与动态名单核对回归。"""

from __future__ import annotations

import pytest

from erp_web.context import get_context
from erp_web.facades import category_facade
from erp_web.runtime_units.category_capabilities import match_category
from erp_web.schemas.draft_capabilities import DraftQueryRequest
from erp_web.schemas.market_prepare_capabilities import CategoryMatchRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.draft_query_service import query_drafts


def _save_forbidden_list(*pairs: tuple[str, str]) -> None:
    get_context().config.shared_config.save_forbidden_categories(
        {
            "platform": "yandex",
            "filename": "forbidden-test.xlsx",
            "imported_at": "2026-11-28T00:00:00Z",
            "categories": [
                {"category_id": cid, "name": name}
                for cid, name in pairs
            ],
        }
    )


def _seed_product_and_draft(
    product_id: str,
    draft_id: str,
    *,
    platform: str = "yandex",
    site: str = "global",
    target_sites: list[dict] | None = None,
    draft_extra: dict | None = None,
    image_pool: list[dict] | None = None,
) -> dict:
    app = get_context()
    targets = target_sites or [
        {"platform": platform, "site": site, "language": "ru-RU"}
    ]
    draft_payload = {
        "draft_id": draft_id,
        "enabled": True,
        "platform": platform,
        "site": site,
        "language": "ru-RU",
        "target_sites": targets,
        "sku_items": [{"sku_id": "sku-1", "selected": True}],
        **(draft_extra or {}),
    }
    app.products.save_product(
        {
            "product_id": product_id,
            "name": f"测试商品 {product_id}",
            "source": {
                "source_platform": "1688",
                "title": f"测试商品 {product_id}",
                "image_pool": image_pool or [],
            },
            "sku_items": [
                {
                    "id": "sku-1",
                    "name": "默认规格",
                    "cost_cny": "15",
                    "active": True,
                }
            ],
            "drafts": {platform: draft_payload},
        }
    )
    return app.db.load_draft_model(draft_id)


def _row_by_id(rows: list[dict], draft_id: str) -> dict:
    return next(row for row in rows if row["draft_id"] == draft_id)


def test_multimarket_draft_shows_category_forbidden_when_any_market_is_forbidden(monkeypatch) -> None:
    app = get_context()
    _save_forbidden_list(("13793701", "Удобрения и уход за растениями"))
    monkeypatch.setattr(
        app.category_catalog.provider_for("yandex"),
        "cached_category_ancestor_ids",
        lambda category_id, **kw: ("90401", "13793701") if str(category_id) == "60491375" else (),
    )
    _seed_product_and_draft(
        "prod-multi-ban",
        "draft-multi-ban",
        platform="ozon",
        site="global",
        target_sites=[
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "category_id": "17028922",
                "description_category_id": "17028922",
                "attributes": {"85": "BrandX"},
            },
            {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
                "category_id": "60491375",
            },
        ],
        draft_extra={
            "title": "Локализованный заголовок",
            "description": "Подробное описание товара",
            "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
            "image_preparation": {"mode": "generated", "fingerprint": "fp-1"},
        },
    )

    list_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-multi-ban")
    full_row = _row_by_id(app.products.load_drafts_index("all"), "draft-multi-ban")
    for row in (list_row, full_row):
        assert row["status"] == "category_forbidden"
        assert row["publish_state"] == "unpublished"
        progress = row["progress"]
        assert progress["forbidden"] is True
        assert progress["status_code"] == "category_forbidden"
        assert progress["status_label"] == "禁售类目"
        assert progress["prep_stage"] == "category_forbidden"
        assert "13793701" in progress["alert"]
        steps_by_key = {step["key"]: step for step in progress["steps"]}
        assert steps_by_key["category"]["state"] == "blocked"
        assert steps_by_key["copy"]["state"] == "done"
        assert steps_by_key["images"]["state"] == "done"
        markets_by_platform = {m["platform"]: m for m in progress["markets"]}
        assert markets_by_platform["yandex"]["forbidden"] is True
        assert markets_by_platform["yandex"]["status_label"] == "禁售类目"
        assert markets_by_platform["ozon"]["forbidden"] is False

    # 移除禁售的 Yandex 市场后，草稿立刻退出「禁售类目」并展示 Ozon 真实准备进度
    current = app.db.load_draft_model("draft-multi-ban")
    _, error, status = app.products.save_draft_content(
        {
            "draft_id": "draft-multi-ban",
            "updated_at": current["updated_at"],
            "target_sites": [current["target_sites"][0]],
        }
    )
    assert error is None and status == 200
    after_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-multi-ban")
    assert after_row["progress"]["forbidden"] is False
    assert after_row["progress"]["prep_stage"] == "preparing"
    assert after_row["progress"]["completed_steps"] == 4


def test_category_match_persists_forbidden_block_and_unbans_when_rule_removed() -> None:
    app = get_context()
    _save_forbidden_list(("13793701", "Удобрения и уход за растениями"))
    _seed_product_and_draft("prod-ai-ban", "draft-ai-ban", platform="yandex", site="global")

    def fake_matcher(_product, _draft, _target):
        return {
            "ok": True,
            "status": "unresolved",
            "failure": {
                "code": "CATEGORY_FORBIDDEN_NO_ALTERNATIVE",
                "message": "最匹配的类目属于禁售范围，且无合规替代类目：60491375（上级类目 13793701 在禁售名单中）",
                "details": {
                    "matched_category_ids": ["60491375"],
                    "matched_forbidden_ids": ["13793701"],
                },
            },
            "candidates": [],
        }

    with pytest.raises(BusinessCapabilityError) as exc_info:
        match_category(
            CategoryMatchRequest(draft_id="draft-ai-ban", target_platform="yandex", site="global"),
            product_store=app.products,
            matcher=fake_matcher,
        )
    assert exc_info.value.code == "CATEGORY_FORBIDDEN_NO_ALTERNATIVE"

    # 即使未写入 category_id，草稿箱与 AI 查询也能直接识别禁售类目
    row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-ai-ban")
    assert row["status"] == "category_forbidden"
    assert row["progress"]["forbidden"] is True
    assert row["progress"]["status_label"] == "禁售类目"
    assert "13793701" in row["progress"]["alert"]

    query_res = query_drafts(
        DraftQueryRequest(scope="active", view="summary"),
        product_store=app.products,
        snapshot_repository=app.draft_query_snapshots,
    )
    queried = next(item for item in query_res.items if item.draft_id == "draft-ai-ban")
    assert queried.readiness.forbidden is True
    assert queried.readiness.prep_stage == "category_forbidden"

    # 管理员从禁售名单移除 13793701 后，不再误报禁售
    _save_forbidden_list(("999999", "其他禁售类目"))
    unbanned = _row_by_id(app.products.load_draft_list_rows("all"), "draft-ai-ban")
    assert unbanned["progress"]["forbidden"] is False
    assert unbanned["progress"]["prep_stage"] == "pending"


def test_http_category_match_facade_persists_forbidden_block(monkeypatch) -> None:
    app = get_context()
    _save_forbidden_list(("13793701", "Удобрения и уход за растениями"))
    _seed_product_and_draft("prod-http-ban", "draft-http-ban", platform="yandex", site="global")
    monkeypatch.setattr(category_facade, "require_model_platform", lambda *a, **k: None)
    monkeypatch.setattr(
        category_facade,
        "match_category",
        lambda *a, **k: {
            "ok": True,
            "status": "unresolved",
            "failure": {
                "code": "CATEGORY_FORBIDDEN_NO_ALTERNATIVE",
                "message": "最匹配的类目属于禁售范围：13793701",
                "details": {"matched_forbidden_ids": ["13793701"]},
            },
            "candidates": [],
        },
    )
    payload, status = category_facade.category_match_payload(
        {"draft_id": "draft-http-ban", "platform": "yandex", "site": "global"}
    )
    assert status == 200 and payload["status"] == "unresolved"
    row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-http-ban")
    assert row["progress"]["forbidden"] is True
    assert row["status"] == "category_forbidden"


def test_draft_progress_tracks_all_six_steps_and_keeps_precheck_not_ready_in_unpublished() -> None:
    app = get_context()
    # 1. 待准备
    _seed_product_and_draft("prod-pending", "draft-pending", platform="ozon", site="global")
    pending_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-pending")
    assert pending_row["progress"]["prep_stage"] == "pending"
    assert pending_row["progress"]["completed_steps"] == 0

    # 2. 待确认（图片手工改动 + 属性待复核）
    _seed_product_and_draft(
        "prod-attn",
        "draft-attn",
        platform="ozon",
        site="global",
        target_sites=[
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "category_id": "17028922",
                "attributes": {"85": "BrandX"},
                "validation_errors": [
                    {"code": "NEED_REVIEW_ATTRIBUTES", "field": "attributes.85", "message": "请复核品牌"}
                ],
            }
        ],
        draft_extra={
            "title": "Заголовок",
            "description": "Описание",
            "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
            "images_manually_modified_at": "2026-11-28T08:00:00Z",
        },
    )
    attn_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-attn")
    assert attn_row["progress"]["prep_stage"] == "needs_confirmation"
    attn_steps = {s["key"]: s["state"] for s in attn_row["progress"]["steps"]}
    assert attn_steps["images"] == "attention"
    assert attn_steps["attributes"] == "attention"

    # 3. 预检未通过留在「待发布」，而非归入「发布失败」
    _seed_product_and_draft(
        "prod-not-ready",
        "draft-not-ready",
        platform="ozon",
        site="global",
        target_sites=[
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "category_id": "17028922",
                "attributes": {"85": "BrandX"},
                "status": "not_ready",
                "publish_status": "not_ready",
                "last_precheck": {
                    "ok": False,
                    "errors": [
                        {"code": "REQUIRED_ATTRIBUTE_MISSING", "field": "attributes.99", "message": "缺少必填属性"},
                    ],
                },
            }
        ],
        draft_extra={
            "status": "not_ready",
            "publish_status": "not_ready",
            "title": "Заголовок",
            "description": "Описание",
            "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
            "image_preparation": {"mode": "kept", "fingerprint": "fp-2"},
            "sku_items": [
                {
                    "sku_id": "sku-1",
                    "selected": True,
                    "pricing": {"applied": True, "targets": {"ozon:global": {"applied_price": {"amount": "999", "currency": "RUB"}}}},
                }
            ],
        },
    )
    nr_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-not-ready")
    assert nr_row["publish_state"] == "unpublished"
    assert nr_row["progress"]["prep_stage"] == "not_ready"
    assert nr_row["progress"]["status_label"] == "预检未通过 (1)"
    nr_steps = {s["key"]: s["state"] for s in nr_row["progress"]["steps"]}
    assert nr_steps["attributes"] == "blocked"
    assert nr_steps["pricing"] == "done"
    assert nr_steps["precheck"] == "blocked"

    # 4. 可发布（6/6 全绿）
    _seed_product_and_draft(
        "prod-ready",
        "draft-ready",
        platform="ozon",
        site="global",
        target_sites=[
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "category_id": "17028922",
                "attributes": {"85": "BrandX"},
                "status": "ready_to_publish",
                "publish_status": "ready",
                "last_precheck": {"ok": True, "errors": []},
            }
        ],
        draft_extra={
            "status": "ready_to_publish",
            "publish_status": "ready",
            "title": "Заголовок",
            "description": "Описание",
            "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
            "image_preparation": {"mode": "generated", "fingerprint": "fp-3"},
            "sku_items": [
                {
                    "sku_id": "sku-1",
                    "selected": True,
                    "pricing": {"applied": True, "targets": {"ozon:global": {"applied_price": {"amount": "999", "currency": "RUB"}}}},
                }
            ],
        },
    )
    ready_row = _row_by_id(app.products.load_draft_list_rows("all"), "draft-ready")
    assert ready_row["progress"]["prep_stage"] == "ready_to_publish"
    assert ready_row["progress"]["completed_steps"] == 6
    assert all(step["state"] == "done" for step in ready_row["progress"]["steps"])
