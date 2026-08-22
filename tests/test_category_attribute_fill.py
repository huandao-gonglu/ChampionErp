from __future__ import annotations

from erp_web.product_model import (
    build_ai_attribute_fill,
    default_product_model,
    validate_category_precheck,
)
from erp_web.runtime_units import category_attribute_ai_fill
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeFillAgentRun,
)


def test_ai_attribute_fill_treats_attribute_id_value_as_missing() -> None:
    product = default_product_model()
    product["drafts"]["mercadolibre"]["model"] = "T-3A"
    product["drafts"]["mercadolibre"]["attributes"] = {
        "BRAND": "Generic",
        "MODEL": "T-3A",
        "AIR_CONDITIONER_TYPE": "AIR_CONDITIONER_TYPE",
    }
    category = {
        "category_id": "MLM459570",
        "attributes": {
            "required": [
                {"id": "BRAND", "name": "Marca", "required": True},
                {"id": "MODEL", "name": "Modelo", "required": True},
                {"id": "AIR_CONDITIONER_TYPE", "name": "Tipo de aire acondicionado", "required": True, "options": ["Split", "Window"]},
            ],
            "optional": [],
        },
    }

    result = build_ai_attribute_fill(product, "mercadolibre", category)

    assert result["attributes"]["BRAND"] == "Generic"
    assert result["attributes"]["MODEL"] == "T-3A"
    assert "AIR_CONDITIONER_TYPE" not in result["attributes"]
    assert "AIR_CONDITIONER_TYPE" in result["need_review"]


def test_ai_model_attribute_fill_uses_product_context_and_validates_options(monkeypatch) -> None:
    product = default_product_model()
    product["name"] = "Portable air conditioner"
    product["source"]["title"] = "Cooling appliance with configurable installation"
    product["attributes"] = {"form_factor": "Portable"}
    product["drafts"]["mercadolibre"]["brand"] = "Generic"
    product["drafts"]["mercadolibre"]["model"] = "T-3A"
    product["drafts"]["mercadolibre"]["attributes"] = {
        "BRAND": "Generic",
        "MODEL": "T-3A",
        "AIR_CONDITIONER_TYPE": "AIR_CONDITIONER_TYPE",
        "POWER_SUPPLY_TYPE": "POWER_SUPPLY_TYPE",
    }
    category = {
        "category_id": "MLM459570",
        "attributes": {
            "required": [
                {"id": "BRAND", "name": "Marca", "required": True},
                {"id": "MODEL", "name": "Modelo", "required": True},
                {
                    "id": "AIR_CONDITIONER_TYPE",
                    "name": "Tipo de aire acondicionado",
                    "required": True,
                    "options": ["Portable", "Split"],
                    "description": "Select the physical air conditioner type.",
                },
                {"id": "POWER_SUPPLY_TYPE", "name": "Tipo de alimentación", "required": True, "options": ["Electric", "Gas"]},
            ],
            "optional": [],
        },
    }
    captured = {}

    def fake_agent(payload, toolset, ledger):
        del toolset, ledger
        captured["title"] = payload["product_context"]["source"]["title"]
        captured["schema_ids"] = [item["id"] for item in payload["attributes"]]
        captured["descriptions"] = {
            item["id"]: item["description"] for item in payload["attributes"]
        }
        return CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "AIR_CONDITIONER_TYPE",
                        "value": "Portable",
                        "dictionary_value_id": "",
                    },
                    {
                        "attribute_id": "POWER_SUPPLY_TYPE",
                        "value": "electric",
                        "dictionary_value_id": "",
                    },
                ],
                "need_review": [],
            }
        )

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        fake_agent,
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "mercadolibre", category)
    attrs = updated["drafts"]["mercadolibre"]["attributes"]

    assert captured["title"] == "Cooling appliance with configurable installation"
    assert "AIR_CONDITIONER_TYPE" in captured["schema_ids"]
    assert captured["descriptions"]["AIR_CONDITIONER_TYPE"] == (
        "Select the physical air conditioner type."
    )
    assert meta["source"] == "ai_model"
    assert attrs["BRAND"] == "Generic"
    assert attrs["MODEL"] == "T-3A"
    assert attrs["AIR_CONDITIONER_TYPE"] == "Portable"
    assert "POWER_SUPPLY_TYPE" not in attrs
    assert updated["drafts"]["mercadolibre"]["validation_errors"] == [
        "POWER_SUPPLY_TYPE"
    ]
    assert meta["evidence_rejected"] == ["POWER_SUPPLY_TYPE"]


def test_ai_model_attribute_fill_resolves_ozon_dictionary_values(monkeypatch) -> None:
    product = default_product_model()
    product["name"] = "共田 F30 手持风扇"
    product["source"]["title"] = "F30 手持充电风扇"
    category = {
        "category_id": "91443",
        "site": "global",
        "attributes": {
            "required": [
                {
                    "id": "8229",
                    "name": "Тип",
                    "required": True,
                    "dictionary_id": "1960",
                    "is_dictionary": True,
                },
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": "28732849",
                    "is_dictionary": True,
                },
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "dictionary_id": "0",
                    "is_dictionary": True,
                },
            ],
            "optional": [],
        },
    }

    def fake_agent(payload, toolset, ledger):
        del toolset
        assert {item["id"]: item["value_mode"] for item in payload["attributes"]} == {
            "8229": "strict_enum",
            "85": "strict_enum",
            "9048": "open_enum",
        }
        ledger.add_values(
            "8229",
            [{"id": "91443", "value": "Вентилятор"}],
        )
        ledger.add_values(
            "85",
            [{"id": "126745801", "value": "Нет бренда"}],
        )
        return CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "8229",
                        "value": "Вентилятор",
                        "dictionary_value_id": "91443",
                    },
                    {
                        "attribute_id": "85",
                        "value": "Нет бренда",
                        "dictionary_value_id": "126745801",
                    },
                    {
                        "attribute_id": "9048",
                        "value": "F30",
                        "dictionary_value_id": "",
                    },
                ],
                "need_review": [],
            }
        )

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        fake_agent,
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        category,
    )
    draft = updated["drafts"]["ozon"]

    assert "8229" not in draft["attributes"]
    assert "85" not in draft["attributes"]
    assert draft["attributes"]["9048"] == "F30"
    assert draft["validation_errors"] == ["8229", "85"]
    assert meta["ai_filled"] == ["9048"]
    assert meta["evidence_rejected"] == ["8229", "85"]


def test_ai_model_attribute_fill_allows_custom_value_for_open_enum(
    monkeypatch,
) -> None:
    product = default_product_model()
    category = {
        "category_id": "MLM123",
        "site": "MLM",
        "attributes": {
            "required": [
                {
                    "id": "MOUNT_TYPE",
                    "name": "Mount type",
                    "required": True,
                    "options": ["Desk", "Floor"],
                }
            ],
            "optional": [],
        },
    }
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        lambda *args: CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "MOUNT_TYPE",
                        "value": "Wall mounted",
                        "dictionary_value_id": "",
                    }
                ],
                "need_review": [],
            }
        ),
    )

    updated, _ = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "mercadolibre",
        category,
    )

    assert "MOUNT_TYPE" not in updated["drafts"]["mercadolibre"]["attributes"]
    assert updated["drafts"]["mercadolibre"]["validation_errors"] == [
        "MOUNT_TYPE"
    ]


def test_unresolved_optional_dictionary_attribute_is_not_a_blocking_error(
    monkeypatch,
) -> None:
    product = default_product_model()
    product["source"]["title"] = "F30 handheld fan"
    category = {
        "category_id": "91443",
        "site": "global",
        "attributes": {
            "required": [
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "dictionary_id": "0",
                }
            ],
            "optional": [
                {
                    "id": "20210",
                    "name": "Вид вентилятора",
                    "required": False,
                    "dictionary_id": "1234",
                }
            ],
        },
    }
    captured = {}

    def fake_agent(payload, *args):
        captured["schema_ids"] = [item["id"] for item in payload["attributes"]]
        return CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "9048",
                        "value": "F30",
                        "dictionary_value_id": "",
                    }
                ],
                "need_review": [],
            }
        )

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        fake_agent,
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        category,
    )
    draft = updated["drafts"]["ozon"]

    assert draft["attributes"]["9048"] == "F30"
    assert "20210" not in draft["attributes"]
    # 可选属性现在也会纳入 AI 填充范围；未填出时静默跳过，不阻断。
    assert captured["schema_ids"] == ["9048", "20210"]
    assert draft["validation_errors"] == []
    assert meta["ai_filled"] == ["9048"]


def test_zero_required_category_fills_optional_attributes_best_effort(
    monkeypatch,
) -> None:
    """零必填参数类目（如部分 Yandex 类目）：AI 也要填可选属性。

    发布接口仍要求至少一个参数值；能确定的可选属性被填充，
    且整个过程不产生阻断错误。
    """

    product = default_product_model()
    product["source"]["title"] = "Шлейка для собак Y-образная"
    category = {
        "category_id": "16088928",
        "site": "global",
        "attributes": {
            "required": [],
            "optional": [
                {
                    "id": "21194330",
                    "name": "Тип",
                    "required": False,
                    "dictionary_id": "955",
                    "is_dictionary": True,
                },
                {
                    "id": "17352854",
                    "name": "Материал",
                    "required": False,
                    "dictionary_id": "1494",
                    "is_dictionary": True,
                },
            ],
        },
    }
    captured = {}

    def fake_agent(payload, toolset, ledger):
        del toolset
        captured["schema_ids"] = [item["id"] for item in payload["attributes"]]
        ledger.add_values(
            "21194330",
            [{"id": "971224534", "value": "Шлейка"}],
        )
        # Материал 找不到合适候选：不填，验证 best-effort 跳过不阻断。
        return CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "21194330",
                        "value": "Шлейка",
                        "dictionary_value_id": "971224534",
                    }
                ],
                "need_review": [],
            }
        )

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        fake_agent,
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "yandex",
        category,
    )
    draft = updated["drafts"]["yandex"]

    assert captured["schema_ids"] == ["21194330", "17352854"]
    assert draft["attributes"]["21194330"] == {
        "values": [{"dictionary_value_id": 971224534, "value": "Шлейка"}]
    }
    assert "17352854" not in draft["attributes"]
    assert draft["validation_errors"] == []
    assert meta["ai_filled"] == ["21194330"]


def test_ai_model_attribute_fill_rejects_market_default_without_product_evidence(
    monkeypatch,
) -> None:
    product = default_product_model()
    product["source"]["title"] = "Portable USB desk fan"
    product["source"]["description"] = (
        "Portable rechargeable USB desk fan for home and office."
    )
    category = {
        "category_id": "MLM457530",
        "site": "MLM",
        "attributes": {
            "required": [
                {
                    "id": "VOLTAGE",
                    "name": "Voltaje",
                    "required": True,
                    "options": ["127V", "220V"],
                }
            ],
            "optional": [],
        },
    }
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        lambda *args: CategoryAttributeFillAgentRun(
            {
                "assignments": [
                    {
                        "attribute_id": "VOLTAGE",
                        "value": "127V",
                        "dictionary_value_id": "",
                    }
                ],
                "need_review": [],
            }
        ),
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "mercadolibre",
        category,
    )

    draft = updated["drafts"]["mercadolibre"]
    assert "VOLTAGE" not in draft["attributes"]
    assert draft["validation_errors"] == ["VOLTAGE"]
    assert meta["ai_filled"] == []
    assert meta["evidence_rejected"] == ["VOLTAGE"]


def test_existing_required_open_enum_is_idempotent_and_clears_stale_error(
    monkeypatch,
) -> None:
    product = default_product_model()
    draft = product["drafts"]["ozon"]
    draft["attributes"] = {"9048": "F30"}
    draft["validation_errors"] = ["9048"]
    category = {
        "category_id": "91443",
        "site": "global",
        "attributes": {
            "required": [
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "dictionary_id": "0",
                    "is_dictionary": True,
                }
            ],
            "optional": [],
        },
    }
    calls = 0

    def unexpected_agent(*args):
        nonlocal calls
        calls += 1
        raise AssertionError("已有有效开放枚举值时不应调用 Agent")

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "run_category_attribute_fill_agent",
        unexpected_agent,
    )

    first, first_meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        category,
    )
    second, second_meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        first,
        "ozon",
        category,
    )

    assert calls == 0
    assert first["drafts"]["ozon"]["attributes"]["9048"] == "F30"
    assert first["drafts"]["ozon"]["validation_errors"] == []
    assert second["drafts"]["ozon"] == first["drafts"]["ozon"]
    assert first_meta == {"source": "rules", "ai_filled": []}
    assert second_meta == first_meta


def test_category_precheck_only_reports_missing_required_category_attributes() -> None:
    product = default_product_model()
    draft = product["drafts"]["mercadolibre"]
    draft["category_id"] = "MLM123"
    draft["brand"] = ""
    draft["model"] = ""
    draft["package_dimensions"] = {
        "length_cm": "21",
        "width_cm": "",
        "height_cm": "",
        "weight_kg": "",
    }
    draft["attributes"] = {"REQUIRED_VALUE": "filled"}
    category = {
        "category_id": "MLM123",
        "attributes": {
            "required": [
                {"id": "REQUIRED_VALUE", "required": True},
                {"id": "PACKAGE_LENGTH", "required": True},
                {"id": "MISSING_REQUIRED", "required": True},
            ],
            "optional": [
                {"id": "OPTIONAL_VALUE", "required": False},
            ],
        },
    }

    result = validate_category_precheck(product, "mercadolibre", category)

    assert result == ["attributes.MISSING_REQUIRED"]


def test_dictionary_attribute_requires_a_selected_platform_value() -> None:
    product = default_product_model()
    draft = product["drafts"]["ozon"]
    draft["category_id"] = "94765"
    draft["attributes"] = {"85": "中性"}
    category = {
        "category_id": "94765",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": "28732849",
                    "is_dictionary": True,
                }
            ],
            "optional": [],
        },
    }

    filled = build_ai_attribute_fill(product, "ozon", category)

    assert "85" not in filled["attributes"]
    assert filled["need_review"] == ["85"]
    assert validate_category_precheck(product, "ozon", category) == [
        "attributes.85"
    ]

    draft["attributes"]["85"] = {
        "values": [
            {
                "dictionary_value_id": 126745801,
                "value": "Нет бренда",
            }
        ]
    }
    assert validate_category_precheck(product, "ozon", category) == []
