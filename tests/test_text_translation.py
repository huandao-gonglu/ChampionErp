from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from erp_web.facades import translation_facade
from erp_web.http_route_units import translation_routes
from erp_web.runtime_units import ai_use_case, text_translation
from erp_web.schemas.requests import validate_request_payload


def test_text_translation_uses_one_use_case_and_does_not_cache() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def translate(*args: object, **kwargs: object) -> dict[str, str]:
        calls.append((args, kwargs))
        return {
            "category.0.path": "电脑 / 笔记本电脑配件",
            "category.1.path": "家居 / 风扇",
        }

    content = {
        "category.0.path": "Computación / Accesorios para Laptops",
        "category.1.path": "Hogar / Ventiladores",
    }
    with patch.object(ai_use_case.ai_gateway, "chat_json", side_effect=translate):
        first = text_translation.translate_texts("zh-CN", content)
        second = text_translation.translate_texts("zh-CN", content)

    assert first == second == {
        "category.0.path": "电脑 / 笔记本电脑配件",
        "category.1.path": "家居 / 风扇",
    }
    assert len(calls) == 2
    for args, kwargs in calls:
        assert args[2] == "text.translate"
        messages = args[3]
        input_payload = json.loads(str(messages[1]["content"]).split("Input:\n", 1)[1])
        assert input_payload == {
            "target_language": "zh-CN",
            "content": content,
        }
        assert kwargs["temperature"] == 0.1


def test_text_translation_rejects_model_key_drift() -> None:
    with (
        patch.object(ai_use_case.ai_gateway, "chat_json", return_value={"renamed": "品牌"}),
        pytest.raises(text_translation.TranslationResponseError, match="key 与请求不一致"),
    ):
        text_translation.translate_texts("zh-CN", {"attribute.0.label": "Marca"})


def test_text_translation_facade_returns_flat_map() -> None:
    with patch.object(
        translation_facade,
        "translate_texts",
        return_value={"attribute.0.label": "品牌"},
    ) as translate:
        result, status = translation_facade.text_translate_payload(
            {
                "target_language": "zh-CN",
                "content": {"attribute.0.label": "Marca"},
            }
        )

    assert status == 200
    assert result == {
        "ok": True,
        "translations": {"attribute.0.label": "品牌"},
    }
    translate.assert_called_once_with("zh-CN", {"attribute.0.label": "Marca"})


def test_text_translation_request_contract_requires_language_and_content() -> None:
    assert validate_request_payload(
        {
            "target_language": "zh-CN",
            "content": {"category.0.path": "Hogar / Ventiladores"},
        },
        endpoint="/api/text-translate",
    ) == {
        "target_language": "zh-CN",
        "content": {"category.0.path": "Hogar / Ventiladores"},
    }


def test_text_translation_route_only_validates_delegates_and_sends(monkeypatch) -> None:
    sent: list[tuple[dict[str, object], int]] = []
    validated = {
        "target_language": "zh-CN",
        "content": {"category.0.path": "Hogar / Ventiladores"},
    }

    class Handler:
        path = "/api/text-translate"

        @staticmethod
        def read_body() -> dict[str, object]:
            return {"raw": True}

        @staticmethod
        def send_json(payload: dict[str, object], status: int = 200) -> None:
            sent.append((payload, status))

    monkeypatch.setattr(
        translation_routes,
        "validate_request_payload",
        lambda body, *, endpoint: validated,
    )
    monkeypatch.setattr(
        translation_routes.translation_facade,
        "text_translate_payload",
        lambda body: ({"ok": True, "translations": body["content"]}, 202),
    )

    translation_routes.handle_text_translate(Handler())

    assert sent == [
        (
            {
                "ok": True,
                "translations": {"category.0.path": "Hogar / Ventiladores"},
            },
            202,
        )
    ]
