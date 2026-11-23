from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from erp_web.facades import translation_facade
from erp_web.http_route_units import manual_ai, translation_routes
from erp_web.runtime_units import ai_use_case, text_translation
from erp_web.schemas.requests import validate_request_payload
from erp_web.stores.category_catalog_store import CategoryCatalogStore


@pytest.fixture(autouse=True)
def isolated_glossary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CategoryCatalogStore:
    store = CategoryCatalogStore(tmp_path / "glossary.sqlite3")
    monkeypatch.setattr(text_translation, "_catalog_store", lambda: store)
    return store


def test_text_translation_uses_one_use_case_and_caches_in_l2_glossary() -> None:
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
        "category.2.dup": "Hogar / Ventiladores",
    }
    with patch.object(ai_use_case.ai_gateway, "chat_json", side_effect=translate):
        first = text_translation.translate_texts_with_meta("zh-CN", content, domain="mercadolibre")
        second = text_translation.translate_texts_with_meta("zh-CN", content, domain="mercadolibre")

    expected = {
        "category.0.path": "电脑 / 笔记本电脑配件",
        "category.1.path": "家居 / 风扇",
        "category.2.dup": "家居 / 风扇",
    }
    assert first["translations"] == expected
    assert first["source"] == "ai"
    assert second["translations"] == expected
    assert second["source"] == "cache"
    assert second["cache_hits"] == 3
    assert second["ai_translated"] == 0
    # 首次去重仅发 2 条给模型，二次全部 0ms 命中 L2 SQLite 词典！
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[2] == "text.translate"
    messages = args[3]
    input_payload = json.loads(str(messages[1]["content"]).split("Input:\n", 1)[1])
    assert input_payload == {
        "target_language": "zh-CN",
        "content": {
            "category.0.path": "Computación / Accesorios para Laptops",
            "category.1.path": "Hogar / Ventiladores",
        },
    }
    assert kwargs["temperature"] == 0.1


def test_text_translation_bypass_cache_and_cache_only() -> None:
    with patch.object(ai_use_case.ai_gateway, "chat_json", return_value={"a": "品牌"}) as chat:
        text_translation.translate_texts("zh-CN", {"a": "Marca"}, domain="mercadolibre")
        cached = text_translation.translate_texts_with_meta(
            "zh-CN", {"a": "Marca", "b": "Color"}, domain="mercadolibre", cache_only=True
        )
        assert cached["translations"] == {"a": "品牌"}
        assert cached["missing_keys"] == ["b"]
        assert chat.call_count == 1

        text_translation.translate_texts("zh-CN", {"a": "Marca"}, domain="mercadolibre", bypass_cache=True)
        assert chat.call_count == 2


def test_text_translation_rejects_model_key_drift() -> None:
    with (
        patch.object(ai_use_case.ai_gateway, "chat_json", return_value={"renamed": "品牌"}),
        pytest.raises(text_translation.TranslationResponseError, match="key 与请求不一致"),
    ):
        text_translation.translate_texts("zh-CN", {"attribute.0.label": "Marca"})


def test_text_translation_protects_explicit_brand_and_model_terms() -> None:
    def translate(*args: object, **kwargs: object) -> dict[str, str]:
        del kwargs
        messages = args[3]
        input_payload = json.loads(str(messages[1]["content"]).split("Input:\n", 1)[1])
        protected = input_payload["content"]["title"]
        assert "Generic" not in protected
        assert "MODEL-1" not in protected
        assert "__ERP_PRESERVE_0__" in protected
        assert "__ERP_PRESERVE_1__" in protected
        return {"title": f"Ventilador {protected}"}

    with patch.object(ai_use_case.ai_gateway, "chat_json", side_effect=translate):
        result = text_translation.translate_texts(
            "es",
            {"title": "Generic Portable fan MODEL-1"},
            preserve_terms=("Generic", "MODEL-1"),
        )

    assert "Generic" in result["title"]
    assert "MODEL-1" in result["title"]
    assert "__ERP_PRESERVE_" not in result["title"]


def test_text_translation_facade_cache_hit_and_silent_degradation(isolated_glossary_store: CategoryCatalogStore) -> None:
    isolated_glossary_store.put_glossary("mercadolibre", "zh-CN", {"Marca": "品牌"})
    # 100% 命中 L2 词典时，即使未配置翻译模型也能 0ms 返回！
    with patch.object(translation_facade, "require_model_platform", side_effect=RuntimeError("未配置翻译模型")):
        result, status = translation_facade.text_translate_payload(
            {
                "domain": "mercadolibre",
                "target_language": "zh-CN",
                "content": {"attribute.0.label": "Marca"},
            }
        )
        assert status == 200
        assert result["ok"] is True
        assert result["translations"] == {"attribute.0.label": "品牌"}
        assert result["source"] == "cache"

        # 含生词且 silent=True 时，优雅降级返回已命中项 + degraded=True，不阻断页面。
        degraded_result, degraded_status = translation_facade.text_translate_payload(
            {
                "domain": "mercadolibre",
                "target_language": "zh-CN",
                "silent": True,
                "content": {"a": "Marca", "b": "Unseen Term"},
            }
        )
        assert degraded_status == 200
        assert degraded_result["ok"] is True
        assert degraded_result["degraded"] is True
        assert degraded_result["translations"] == {"a": "品牌"}


def test_text_translation_request_contract_requires_language_and_content() -> None:
    assert validate_request_payload(
        {
            "target_language": "zh-CN",
            "domain": "ozon",
            "silent": True,
            "content": {"category.0.path": "Hogar / Ventiladores"},
        },
        endpoint="/api/text-translate",
    ) == {
        "target_language": "zh-CN",
        "domain": "ozon",
        "silent": True,
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
        manual_ai,
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
