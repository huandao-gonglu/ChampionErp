from __future__ import annotations

from erp_web.runtime_units import category_product_identify


def test_identify_product_for_category_returns_localized_query_for_every_target(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        captured["use_case_id"] = use_case_id
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {
            "product_name": "手持风扇",
            "product_type": "handheld_fan",
            "confidence": 0.94,
            "reason": ["标题包含手持风扇", "属性表明 USB 供电"],
            "target_queries": [
                {"platform": "mercadolibre", "site": "MLM", "query": "ventilador portátil de mano"},
                {"platform": "mercadolibre", "site": "CBT", "query": "portable handheld fan"},
            ],
        }

    monkeypatch.setattr(category_product_identify.ai_gateway, "chat_json", fake_chat_json)
    result = category_product_identify.identify_product_for_category(
        {
            "name": "2026新品 Ultra2E 手持小风扇便携式迷你无叶 USB 高速节能电风扇",
            "source": {
                "title": "2026新品 Ultra2E 手持小风扇便携式迷你无叶 USB 高速节能电风扇",
                "description": "可充电便携风扇",
                "attributes": {"供电方式": "USB", "产品类型": "手持风扇"},
            },
        },
        {"title": "Portable fan", "description": ""},
        [
            {"platform": "mercadolibre", "site": "MLM", "language": "es-MX", "currency": "MXN"},
            {"platform": "mercadolibre", "site": "CBT", "language": "en-US", "currency": "USD"},
        ],
    )

    assert captured["use_case_id"] == "category.product_identify"
    assert captured["kwargs"] == {"temperature": 0, "max_tokens": 700}
    user_message = captured["messages"][1]["content"]
    assert "手持小风扇" in user_message
    assert result["identity"] == {
        "name": "手持风扇",
        "product_type": "handheld_fan",
        "confidence": 0.94,
        "reason": ["标题包含手持风扇", "属性表明 USB 供电"],
    }
    assert result["targets"] == [
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "language": "es-MX",
            "currency": "MXN",
            "query": "ventilador portátil de mano",
        },
        {
            "platform": "mercadolibre",
            "site": "CBT",
            "language": "en-US",
            "currency": "USD",
            "query": "portable handheld fan",
        },
    ]


def test_identify_product_for_category_deduplicates_targets_and_keeps_missing_query_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        category_product_identify.ai_gateway,
        "chat_json",
        lambda *args, **kwargs: {"product_name": "折叠伞", "target_queries": []},
    )

    result = category_product_identify.identify_product_for_category(
        {"source": {"title": "便携三折雨伞"}},
        {},
        [
            {"platform": "mercadolibre", "site": "MLM", "language": "es-MX", "currency": "MXN"},
            {"platform": "mercadolibre", "site": "MLM", "language": "es-MX", "currency": "MXN"},
        ],
    )

    assert result["targets"] == [
        {
            "platform": "mercadolibre",
            "site": "MLM",
            "language": "es-MX",
            "currency": "MXN",
            "query": "",
        }
    ]
