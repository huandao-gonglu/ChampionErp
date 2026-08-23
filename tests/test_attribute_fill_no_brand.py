from __future__ import annotations

from copy import deepcopy

from erp_web.product_model.defaults import default_product_model
from erp_web.runtime_units import (
    attribute_fill_capabilities,
    category_attribute_ai_fill,
    category_store,
)
from erp_web.runtime_units.category_brand_values import (
    apply_no_brand_attribute,
    definition_is_brand,
    resolve_no_brand_option,
)
from erp_web.schemas.category_brand import is_no_brand_fact
from erp_web.schemas.category_definition import (
    CategoryAttributeValue,
    CategoryAttributeValuePage,
)


def _brand_definition() -> dict[str, object]:
    return {
        "id": "85",
        "name": "Бренд",
        "required": True,
        "value_mode": "strict_enum",
        "dictionary_id": "28732849",
        "is_dictionary": True,
        "options": [],
    }


def _category_record() -> dict[str, object]:
    return {
        "category_id": "94953",
        "site": "global",
        "attributes": {
            "required": [_brand_definition()],
            "optional": [],
        },
    }


def _value_page(
    *,
    values: list[tuple[str, str]],
    cursor: str = "",
    limit: int = 50,
    next_cursor: str = "",
    has_more: bool = False,
) -> CategoryAttributeValuePage:
    return CategoryAttributeValuePage(
        platform="ozon",
        site="global",
        category_id="94953",
        attribute_id="85",
        cursor=cursor,
        limit=limit,
        values=tuple(
            CategoryAttributeValue(value=value, dictionary_value_id=value_id)
            for value_id, value in values
        ),
        next_cursor=next_cursor,
        has_more=has_more,
    )


def test_brand_attribute_id_is_platform_scoped() -> None:
    yandex_color = {
        "id": "85",
        "name": "Цвет",
        "dictionary_id": "1494",
        "is_dictionary": True,
    }

    assert definition_is_brand(yandex_color, platform="yandex") is False
    assert definition_is_brand(_brand_definition(), platform="ozon") is True


def test_brand_attribute_name_requires_a_complete_brand_word() -> None:
    marker_type = {
        "id": "MARKER_TYPE",
        "name": "Tipo de marcador",
        "value_mode": "strict_enum",
        "dictionary_id": "marker-types",
        "is_dictionary": True,
    }
    spanish_brand = {
        "id": "SELLER_BRAND",
        "name": "Marca del producto",
        "value_mode": "strict_enum",
        "dictionary_id": "brands",
        "is_dictionary": True,
    }

    assert definition_is_brand(marker_type, platform="mercadolibre") is False
    assert definition_is_brand(spanish_brand, platform="mercadolibre") is True


def test_no_brand_aliases_include_other_but_not_concrete_oem() -> None:
    assert is_no_brand_fact("Generic") is True
    assert is_no_brand_fact("无品牌") is True
    assert is_no_brand_fact("其他") is True
    assert is_no_brand_fact("其它") is True
    assert is_no_brand_fact("Other") is True
    assert is_no_brand_fact("OTHER") is True
    assert is_no_brand_fact("OEM") is False
    assert is_no_brand_fact("") is False


def test_no_brand_resolution_uses_current_platform_candidate_without_id_fallback() -> None:
    calls: list[str] = []

    def loader(*args, query="", **kwargs):
        del args, kwargs
        calls.append(query)
        return {"values": [{"id": "current-category-id", "value": "Нет бренда"}]}

    assert resolve_no_brand_option(
        loader,
        platform="ozon",
        category_id="94953",
        attribute_id="85",
        site="global",
    ) == {
        "dictionary_value_id": "current-category-id",
        "value": "Нет бренда",
    }
    assert calls == ["нет бренда"]

    def failed_loader(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("平台查询失败")

    assert resolve_no_brand_option(
        failed_loader,
        platform="ozon",
        category_id="another-category",
        attribute_id="85",
        site="global",
    ) is None


def test_no_brand_rule_does_not_override_a_concrete_product_brand() -> None:
    draft = {"brand": "Generic", "attributes": {}}
    product = {"brand": "Bosch", "source": {"brand": "Bosch"}}
    calls = 0

    def loader(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return {"values": [{"id": "1", "value": "Нет бренда"}]}

    assert apply_no_brand_attribute(
        draft,
        product=product,
        platform="ozon",
        record=_category_record(),
        category_id="94953",
        site="global",
        loader=loader,
    ) == ""
    assert draft["attributes"] == {}
    assert calls == 0


def test_direct_ai_fill_resolves_explicit_no_brand_before_running_agent(
    monkeypatch,
) -> None:
    product = default_product_model()
    product["brand"] = "其他"
    product["source"]["brand"] = "其他"
    product["drafts"]["ozon"]["brand"] = "其他"

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "fetch_category_attribute_values",
        lambda *args, **kwargs: {
            "values": [{"id": "category-scoped-id", "value": "Нет бренда"}]
        },
    )
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("确定性无品牌事实不应再交给 Agent 猜测")
        ),
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        _category_record(),
    )

    assert updated["drafts"]["ozon"]["attributes"]["85"] == {
        "values": [
            {
                "dictionary_value_id": "category-scoped-id",
                "value": "Нет бренда",
            }
        ]
    }
    assert updated["drafts"]["ozon"]["validation_errors"] == []
    assert meta == {
        "source": "rules",
        "ai_filled": [],
        "rule_filled": ["85"],
    }


def test_public_brand_value_page_pins_live_no_brand_and_preserves_cursor(
    monkeypatch,
) -> None:
    class Catalog:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def attribute_values(self, *args, **kwargs):
            del args
            self.calls.append(deepcopy(kwargs))
            if kwargs["query"] == "нет бренда":
                return _value_page(
                    values=[("no-brand-id", "Нет бренда")],
                    limit=int(kwargs["limit"]),
                )
            return _value_page(
                values=[("brand-1", "Alpha"), ("brand-2", "Beta")],
                limit=int(kwargs["limit"]),
                next_cursor="brand-2",
                has_more=True,
            )

    catalog = Catalog()
    monkeypatch.setattr(category_store, "get_category_catalog", lambda: catalog)

    payload = category_store.fetch_category_attribute_values(
        "ozon",
        "94953",
        "85",
        site="global",
        limit=2,
    )

    assert [(item["id"], item["value"]) for item in payload["values"]] == [
        ("no-brand-id", "Нет бренда"),
        ("brand-1", "Alpha"),
    ]
    assert payload["next_cursor"] == "brand-1"
    assert payload["has_more"] is True
    assert [call["query"] for call in catalog.calls] == ["", "нет бренда"]


def test_public_brand_value_page_with_limit_one_exposes_original_first_item(
    monkeypatch,
) -> None:
    class Catalog:
        def attribute_values(self, *args, **kwargs):
            del args
            if kwargs["query"] == "нет бренда":
                return _value_page(
                    values=[("no-brand-id", "Нет бренда")],
                    limit=1,
                )
            return _value_page(
                values=[("brand-1", "Alpha")],
                cursor=str(kwargs["cursor"]),
                limit=1,
            )

    monkeypatch.setattr(category_store, "get_category_catalog", Catalog)

    first = category_store.fetch_category_attribute_values(
        "ozon",
        "94953",
        "85",
        limit=1,
    )
    second = category_store.fetch_category_attribute_values(
        "ozon",
        "94953",
        "85",
        cursor=first["next_cursor"],
        limit=1,
    )

    assert [(item["id"], item["value"]) for item in first["values"]] == [
        ("no-brand-id", "Нет бренда"),
    ]
    assert first["next_cursor"] == "0"
    assert first["has_more"] is True
    assert [(item["id"], item["value"]) for item in second["values"]] == [
        ("brand-1", "Alpha"),
    ]
    assert second["has_more"] is False


def test_concrete_brand_input_does_not_run_no_brand_lookup() -> None:
    queries: list[str] = []

    def loader(*args, query="", **kwargs):
        del args, kwargs
        queries.append(query)
        return {"values": [{"id": "bosch-id", "value": "Bosch"}]}

    normalized = attribute_fill_capabilities._normalize_provided_dictionary_values(
        {"85": "Bosch"},
        platform="ozon",
        category_id="94953",
        site="global",
        record=_category_record(),
        loader=loader,
    )

    assert normalized == {
        "85": {
            "values": [
                {
                    "dictionary_value_id": "bosch-id",
                    "value": "Bosch",
                }
            ]
        }
    }
    assert queries == [""]


def test_public_brand_value_search_maps_chinese_alias_to_platform_query(
    monkeypatch,
) -> None:
    class Catalog:
        def __init__(self) -> None:
            self.query = ""

        def attribute_values(self, *args, **kwargs):
            del args
            self.query = str(kwargs["query"])
            return _value_page(
                values=[("no-brand-id", "Нет бренда")],
            )

    catalog = Catalog()
    monkeypatch.setattr(category_store, "get_category_catalog", lambda: catalog)

    payload = category_store.fetch_category_attribute_values(
        "ozon",
        "94953",
        "85",
        query="无品牌",
    )

    assert catalog.query == "нет бренда"
    assert payload["query"] == "无品牌"
    assert payload["values"][0]["value"] == "Нет бренда"
