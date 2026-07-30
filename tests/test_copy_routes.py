from __future__ import annotations

import pytest

from erp_web.context import get_context
from erp_web.facades import copy_facade
from erp_web.http_route_units import copy_routes


@pytest.mark.parametrize(
    ("handler_name", "facade_name"),
    [
        ("handle_generate_copy", "generate_copy_payload"),
        ("handle_generate_copy_batch", "generate_copy_batch_payload"),
        ("handle_generate_image_prompts", "generate_image_prompts_payload"),
        ("handle_test_ai_model", "test_ai_model_payload"),
    ],
)
def test_copy_routes_only_validate_delegate_and_send(
    monkeypatch,
    handler_name: str,
    facade_name: str,
) -> None:
    sent: list[tuple[dict, int]] = []
    received: list[dict] = []
    raw_body = {"raw": True}
    validated_body = {"validated": True}
    response = {"ok": True, "handler": handler_name}

    class Handler:
        path = {
            "handle_generate_copy": "/api/generate-copy",
            "handle_generate_copy_batch": "/api/generate-copy-batch",
            "handle_generate_image_prompts": "/api/generate-image-prompts",
            "handle_test_ai_model": "/api/test-ai-model",
        }[handler_name]

        @staticmethod
        def read_body() -> dict:
            return raw_body

        @staticmethod
        def send_json(payload: dict, status: int = 200) -> None:
            sent.append((payload, status))

    def fake_validate(body: dict, *, endpoint: str) -> dict:
        assert body is raw_body
        assert endpoint == Handler.path
        return validated_body

    def fake_facade(body: dict) -> tuple[dict, int]:
        received.append(body)
        return response, 207

    monkeypatch.setattr(copy_routes, "validate_request_payload", fake_validate)
    monkeypatch.setattr(copy_routes.copy_facade, facade_name, fake_facade)

    getattr(copy_routes, handler_name)(Handler())

    assert received == [validated_body]
    assert sent == [(response, 207)]


def test_failed_copy_generation_does_not_save_draft(monkeypatch) -> None:
    context = get_context()
    monkeypatch.setattr(
        context.products,
        "load_required_product_from_body",
        lambda body: ({"product_id": "product-1", "drafts": {}}, None, 200),
    )
    monkeypatch.setattr(
        context.config,
        "load_app_config",
        lambda: {},
    )
    monkeypatch.setattr(
        copy_facade,
        "generate_ai_copy_bundle",
        lambda *args, **kwargs: {
            "ok": False,
            "target_market": "ozon",
            "language": "ru-RU",
            "copy": {},
            "error": "本地化文案生成失败：AI 服务不可用",
        },
    )
    monkeypatch.setattr(
        copy_facade,
        "save_copy_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("失败结果不得写入草稿")
        ),
    )

    result = copy_facade.generate_copy_payload(
        {"product_id": "product-1", "platform": "ozon"}
    )

    assert result == (
        {
            "ok": False,
            "target_market": "ozon",
            "language": "ru-RU",
            "copy": {},
            "error": "本地化文案生成失败：AI 服务不可用",
        },
        400,
    )


def test_generate_copy_uses_requested_draft_context(monkeypatch) -> None:
    context = get_context()
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

    monkeypatch.setattr(
        context.products,
        "load_draft_from_index",
        lambda draft_id: product,
    )
    monkeypatch.setattr(
        context.products,
        "load_required_product_from_body",
        lambda body: (_ for _ in ()).throw(AssertionError("指定 draft_id 时不得按商品兜底加载")),
    )
    monkeypatch.setattr(
        context.config,
        "load_app_config",
        lambda: {},
    )

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

    monkeypatch.setattr(copy_facade, "generate_ai_copy_bundle", fake_generate)
    monkeypatch.setattr(copy_facade, "save_copy_result", lambda *args: product)
    monkeypatch.setattr(
        copy_facade,
        "build_plan_for_platform",
        lambda *args: {"platforms": {"ozon": {"listing": {}}}},
    )
    monkeypatch.setattr(
        copy_facade,
        "apply_product_drafts_to_plan",
        lambda product_arg, plan: plan,
    )
    monkeypatch.setattr(
        copy_facade,
        "platform_to_preset_key",
        lambda platform: platform,
    )
    monkeypatch.setattr(
        context.products,
        "load_draft_detail_from_index",
        lambda draft_id: ({"draft": {"draft_id": draft_id}, "productContext": {}}, None, 200),
    )
    monkeypatch.setattr(
        context.products,
        "load_products_index",
        lambda: [],
    )

    payload, status = copy_facade.generate_copy_payload(
        {
            "product_id": "product-1",
            "draft_id": "draft-1",
            "platform": "ozon",
            "language": "ru-RU",
            "mode": "rewrite",
        }
    )

    assert generated["product"] is product
    assert generated["language"] == "ru-RU"
    assert generated["mode"] == "rewrite"
    assert status == 200
    assert payload["draft"]["draft_id"] == "draft-1"


@pytest.mark.parametrize(
    ("domain_result", "expected_status"),
    [
        ({"ok": True, "items": []}, 200),
        ({"ok": False, "error": "不支持的平台"}, 400),
    ],
)
def test_generate_copy_batch_maps_domain_status(
    monkeypatch,
    domain_result: dict,
    expected_status: int,
) -> None:
    received: list[tuple[object, ...]] = []

    def fake_batch(*args):
        received.append(args)
        return domain_result

    monkeypatch.setattr(copy_facade, "batch_generate_copy_for_products", fake_batch)

    result = copy_facade.generate_copy_batch_payload(
        {
            "product_ids": ["product-1"],
            "platform": "ozon",
            "language": "ru-RU",
            "mode": "generate",
        }
    )

    assert received == [(["product-1"], "ozon", "ru-RU", "generate")]
    assert result == (domain_result, expected_status)


def test_generate_image_prompts_builds_compatible_response(monkeypatch) -> None:
    context = get_context()
    product = {"product_id": "product-1"}
    received: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        context.products,
        "load_required_product_from_body",
        lambda body: (product, None, 200),
    )

    def fake_build(*args):
        received.append(args)
        return "图片提示词"

    monkeypatch.setattr(copy_facade, "build_image_prompt_pack", fake_build)

    result = copy_facade.generate_image_prompts_payload(
        {
            "product_id": "product-1",
            "platform": "ozon",
            "selected_image_ids": ["image-1"],
            "include_bullets": False,
            "include_description": True,
            "target_language": "ru-RU",
        }
    )

    assert received == [
        (product, "ozon", ["image-1"], False, True, "ru-RU"),
    ]
    assert result == (
        {
            "ok": True,
            "prompt": "图片提示词",
            "selected_image_ids": ["image-1"],
        },
        200,
    )


def test_ai_model_test_preserves_success_and_exception_statuses(monkeypatch) -> None:
    model = {"id": "model-1", "provider": "openai"}
    monkeypatch.setattr(
        copy_facade,
        "test_ai_model_config",
        lambda config: {"ok": False, "error": "领域返回失败"},
    )
    assert copy_facade.test_ai_model_payload({"model": model}) == (
        {"ok": False, "error": "领域返回失败"},
        200,
    )

    def raise_error(config: dict) -> dict:
        raise RuntimeError("连接失败")

    monkeypatch.setattr(copy_facade, "test_ai_model_config", raise_error)
    assert copy_facade.test_ai_model_payload({"model": model}) == (
        {"ok": False, "error": "连接失败"},
        400,
    )
