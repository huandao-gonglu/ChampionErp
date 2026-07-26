from __future__ import annotations

from erp_web.http_route_units import copy_routes


def test_failed_copy_generation_does_not_save_draft(monkeypatch) -> None:
    sent: list[tuple[dict, int]] = []

    class Handler:
        @staticmethod
        def read_body() -> dict:
            return {"product_id": "product-1", "platform": "ozon"}

        @staticmethod
        def send_json(payload: dict, status: int = 200) -> None:
            sent.append((payload, status))

    monkeypatch.setattr(
        copy_routes,
        "load_required_product_from_body",
        lambda body: ({"product_id": "product-1", "drafts": {}}, None, 200),
    )
    monkeypatch.setattr(copy_routes, "load_app_config", lambda: {})
    monkeypatch.setattr(
        copy_routes,
        "generate_ai_copy_bundle",
        lambda *args, **kwargs: {
            "ok": False,
            "target_market": "ozon",
            "language": "ru-RU",
            "copy": {},
            "error": "本地化文案生成失败：AI 服务不可用",
        },
    )
    monkeypatch.setattr(copy_routes, "save_copy_result", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("失败结果不得写入草稿")))

    copy_routes.handle_generate_copy(Handler())

    assert sent == [
        (
            {
                "ok": False,
                "target_market": "ozon",
                "language": "ru-RU",
                "copy": {},
                "error": "本地化文案生成失败：AI 服务不可用",
            },
            400,
        )
    ]


def test_generate_copy_uses_requested_draft_context(monkeypatch) -> None:
    sent: list[tuple[dict, int]] = []
    generated: dict[str, object] = {}
    product = {
        "product_id": "product-1",
        "current_draft_id": "draft-1",
        "drafts": {
            "ozon": {
                "draft_id": "draft-1",
                "platform": "ozon",
                "language": "ru-RU",
            }
        },
    }

    class Handler:
        @staticmethod
        def read_body() -> dict:
            return {
                "product_id": "product-1",
                "draft_id": "draft-1",
                "platform": "ozon",
                "language": "ru-RU",
                "mode": "rewrite",
            }

        @staticmethod
        def send_json(payload: dict, status: int = 200) -> None:
            sent.append((payload, status))

    monkeypatch.setattr(copy_routes, "load_draft_from_index", lambda draft_id: product)
    monkeypatch.setattr(
        copy_routes,
        "load_required_product_from_body",
        lambda body: (_ for _ in ()).throw(AssertionError("指定 draft_id 时不得按商品兜底加载")),
    )
    monkeypatch.setattr(copy_routes, "load_app_config", lambda: {})

    def fake_generate(product_arg, source_platform, target_platform, language, mode, app_config):
        generated.update(
            {
                "product": product_arg,
                "source_platform": source_platform,
                "target_platform": target_platform,
                "language": language,
                "mode": mode,
            }
        )
        return {
            "ok": True,
            "target_market": "ozon",
            "source_platform": "ozon",
            "language": "ru-RU",
            "mode": "rewrite",
            "copy": {"title": "标题", "description": "描述", "bullets": []},
        }

    monkeypatch.setattr(copy_routes, "generate_ai_copy_bundle", fake_generate)
    monkeypatch.setattr(copy_routes, "save_copy_result", lambda *args: product)
    monkeypatch.setattr(copy_routes, "build_plan_for_platform", lambda *args: {"platforms": {"ozon": {"listing": {}}}})
    monkeypatch.setattr(copy_routes, "apply_product_drafts_to_plan", lambda product_arg, plan: plan)
    monkeypatch.setattr(copy_routes, "platform_to_preset_key", lambda platform: platform)
    monkeypatch.setattr(
        copy_routes,
        "load_draft_detail_from_index",
        lambda draft_id: ({"draft": {"draft_id": draft_id}, "productContext": {}}, None, 200),
    )
    monkeypatch.setattr(copy_routes, "load_products_index", lambda: [])

    copy_routes.handle_generate_copy(Handler())

    assert generated["product"] is product
    assert generated["language"] == "ru-RU"
    assert generated["mode"] == "rewrite"
    assert sent[0][1] == 200
    assert sent[0][0]["draft"]["draft_id"] == "draft-1"
