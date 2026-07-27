from __future__ import annotations

from unittest.mock import patch

from erp_web.runtime_units import ai_use_case, category_attribute_translation, category_result_translation


def test_category_result_translation_does_not_reuse_previous_response() -> None:
    calls: list[object] = []

    def translate(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((args, kwargs))
        return {"translations": {"MLM455865": "电脑 / 笔记本电脑配件 / 便携式风扇"}}

    categories = [{"id": "MLM455865", "name": "Ventiladores Portátiles", "path": "Computación / Ventiladores Portátiles"}]
    with patch.object(ai_use_case.ai_gateway, "chat_json", side_effect=translate):
        first = category_result_translation.translate_category_results("mercadolibre", categories)
        second = category_result_translation.translate_category_results("mercadolibre", categories)

    assert first["source"] == "ai"
    assert second["source"] == "ai"
    assert first["translations"] == second["translations"]
    assert len(calls) == 2


def test_category_attribute_translation_does_not_reuse_previous_response() -> None:
    calls: list[object] = []

    def translate(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((args, kwargs))
        return {
            "translations": {
                "BRAND": {
                    "label": "品牌",
                    "help": "填写商品品牌。",
                    "values": {"Generic": "通用品牌"},
                }
            }
        }

    attributes = [{"id": "BRAND", "name": "Marca", "required": True, "options": ["Generic"]}]
    with patch.object(ai_use_case.ai_gateway, "chat_json", side_effect=translate):
        first = category_attribute_translation.translate_category_attributes("mercadolibre", "MLM455865", "Electrónica", attributes)
        second = category_attribute_translation.translate_category_attributes("mercadolibre", "MLM455865", "Electrónica", attributes)

    assert first["source"] == "ai"
    assert second["source"] == "ai"
    assert first["translations"] == second["translations"]
    assert len(calls) == 2
