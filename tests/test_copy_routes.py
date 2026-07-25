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
