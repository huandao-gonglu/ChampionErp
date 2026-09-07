"""来源复用必须按字段和当前市场保存，翻译去重且不覆盖人工决定。"""

from copy import deepcopy
from unittest.mock import Mock

from erp_web.facades import category_facade
from erp_web.runtime_units.sku_source_attributes import reuse_sku_source_attributes
from tests.test_sku_attribute_fill import subject
from tests.runtime_test_utils import temp_app_context


def definition(platform="ozon"):
    return {"category_id": "face-mask", "platform": platform, "site": "global", "attributes": {
        "required": [{"id": "10096", "name": "Цвет товара", "required": True, "value_mode": "strict_enum", "variation_role": "variant"}],
        "optional": [{"id": "10097" if platform == "ozon" else "14871214", "name": "Название цвета", "value_mode": "free_text", "variation_role": "variant"},
                     {"id": "9533", "name": "Размер производителя", "value_mode": "free_text", "variation_role": "variant"}],
    }}


def test_batch_deduplicates_text_preserves_designation_and_never_guesses_enum():
    product = subject()
    product["drafts"]["ozon"]["language"] = "ru-RU"
    source = deepcopy(product)
    translation = Mock(side_effect=lambda language, content: {key: "Перевод " + value for key, value in content.items()})
    updated, meta = reuse_sku_source_attributes(product, "ozon", definition(), translator=translation)
    assert product == source
    translation.assert_called_once()
    assert list(translation.call_args.args[1].values()) == ["暗夜黑", "均码", "樱花粉"]
    for row in updated["drafts"]["ozon"]["sku_items"]:
        assert set(row["attributes_by_target"]["ozon:global"]) == {"10097", "9533"}
        assert row["attributes_by_target"]["yandex:global"]
    assert len(meta["ai_filled"]) == 4
    again, meta = reuse_sku_source_attributes(updated, "ozon", definition(), translator=translation)
    assert again == updated and meta["ai_filled"] == []
    translation.assert_called_once()


def test_translation_cache_is_reused_across_markets_and_source_change_invalidates_it():
    product = subject()
    draft = product["drafts"]["ozon"]
    draft["language"] = "ru-RU"
    translation = Mock(side_effect=lambda language, content: {key: "Перевод " + value for key, value in content.items()})
    updated, _ = reuse_sku_source_attributes(product, "ozon", definition(), translator=translation)
    updated["drafts"]["yandex"] = updated["drafts"].pop("ozon")
    updated, meta = reuse_sku_source_attributes(updated, "yandex", definition("yandex"), translator=translation)
    translation.assert_called_once()
    assert len(meta["ai_filled"]) == 2
    # 人工值保持原样，清空后的新来源需要重新翻译。
    row = updated["drafts"]["yandex"]["sku_items"][0]
    row["attributes_by_target"]["yandex:global"]["14871214"] = ""
    updated["sku_items"][0]["options"]["颜色"] = "【护颈款】暗夜黑"
    final, _ = reuse_sku_source_attributes(updated, "yandex", definition("yandex"), translator=translation)
    assert translation.call_count == 2
    assert final["drafts"]["yandex"]["sku_items"][0]["attributes_by_target"]["yandex:global"]["14871214"] == "Перевод 【护颈款】暗夜黑"


def test_source_fill_http_saves_rows_and_translation_evidence(tmp_path, monkeypatch):
    with temp_app_context(tmp_path) as app:
        product = subject()
        product["drafts"]["ozon"]["language"] = "ru-RU"
        product["drafts"]["ozon"]["target_sites"][0]["language"] = "ru-RU"
        app.products.save_product(product)
        monkeypatch.setattr(category_facade, "fetch_category_record", lambda *args, **kwargs: definition())
        monkeypatch.setattr(category_facade, "translate_texts", lambda language, content: {key: "Перевод " + value for key, value in content.items()})
        response, status = category_facade.category_ai_fill_payload({
            "draft_id": "sku-draft", "platform": "ozon", "site": "global", "category_id": "face-mask", "reuse_sku_sources": True,
        })
        assert status == 200
        saved = app.db.load_draft_model("sku-draft")
        assert saved["sku_items"][0]["attributes_by_target"]["ozon:global"]["10097"] == "Перевод 暗夜黑"
        assert saved["sku_items"][0]["source_option_translations"]["ru-RU"]["均码"]
        assert len(response["ai_filled"]) == 4


def test_source_reuse_skips_inactive_skus_and_preserves_manual_attributes():
    product = subject()
    draft = product["drafts"]["ozon"]
    draft["language"] = "ru-RU"
    product["sku_items"][1]["active"] = False
    draft["sku_items"][0]["attributes_by_target"]["ozon:global"] = {"10097": "人工颜色"}
    translation = Mock(return_value={"0": "универсальный"})
    updated, meta = reuse_sku_source_attributes(product, "ozon", definition(), translator=translation)
    assert translation.call_args.args[1] == {"0": "均码"}
    assert updated["drafts"]["ozon"]["sku_items"][0]["attributes_by_target"]["ozon:global"]["10097"] == "人工颜色"
    assert updated["drafts"]["ozon"]["sku_items"][1] == draft["sku_items"][1]
