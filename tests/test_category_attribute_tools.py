from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from erp_web.runtime_units import category_attribute_tools
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger


def execution_context() -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task_attribute_values",
        attempt_id="attempt_attribute_values",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        budget_profile="category.attribute_fill.default",
        permissions=frozenset({"category.attribute.read"}),
    )


def test_attribute_value_tool_queries_strict_dictionary_and_records_ids(
    monkeypatch,
) -> None:
    schema = [
        {
            "id": "8229",
            "name": "Тип",
            "value_mode": "strict_enum",
            "options": [],
        }
    ]
    ledger = CategoryAttributeValueLedger.from_schema(schema)
    captured = {}

    def fake_values(platform, category_id, attribute_id, **kwargs):
        captured.update(
            {
                "platform": platform,
                "category_id": category_id,
                "attribute_id": attribute_id,
                **kwargs,
            }
        )
        return {"values": [{"id": 91443, "value": "Вентилятор"}]}

    monkeypatch.setattr(
        category_attribute_tools,
        "fetch_category_attribute_values",
        fake_values,
    )
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="ozon",
        category_record={"category_id": "91443", "site": "global"},
        ledger=ledger,
    )

    output = toolset.get("category_attribute_values_search").executor(
        {
            "requests": [
                {"attribute_id": "8229", "query": "вентилятор"}
            ]
        },
        execution_context(),
    )

    assert output["results"] == [
        {
            "attribute_id": "8229",
            "query": "вентилятор",
            "values": [
                {
                    "dictionary_value_id": "91443",
                    "value": "Вентилятор",
                }
            ],
            "error_code": "",
            "error_message": "",
        }
    ]
    assert captured["platform"] == "ozon"
    assert captured["category_id"] == "91443"
    assert captured["site"] == "global"
    assert ledger.get("8229", "91443") == {
        "dictionary_value_id": "91443",
        "value": "Вентилятор",
    }


def test_attribute_value_tool_rejects_open_enum() -> None:
    ledger = CategoryAttributeValueLedger.from_schema(
        [
            {
                "id": "STYLE",
                "name": "Style",
                "value_mode": "open_enum",
                "options": ["Desk", "Floor"],
            }
        ]
    )
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="mercadolibre",
        category_record={"category_id": "MLM123", "site": "MLM"},
        ledger=ledger,
    )

    output = toolset.get("category_attribute_values_search").executor(
        {"requests": [{"attribute_id": "STYLE", "query": "wall"}]},
        execution_context(),
    )
    assert output["results"][0]["error_code"] == "ATTRIBUTE_VALUES_NOT_QUERYABLE"
    assert "直接" in output["results"][0]["error_message"]
    assert ledger.attempts == []


def test_brand_value_tool_maps_no_brand_alias_to_platform_query(
    monkeypatch,
) -> None:
    ledger = CategoryAttributeValueLedger.from_schema(
        [
            {
                "id": "85",
                "name": "Бренд",
                "value_mode": "strict_enum",
                "dictionary_id": "28732849",
                "is_dictionary": True,
            }
        ]
    )
    captured: dict[str, object] = {}

    def fake_values(*args, **kwargs):
        del args
        captured.update(kwargs)
        return {"values": [{"id": "live-id", "value": "Нет бренда"}]}

    monkeypatch.setattr(
        category_attribute_tools,
        "fetch_category_attribute_values",
        fake_values,
    )
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="ozon",
        category_record={"category_id": "94953", "site": "global"},
        ledger=ledger,
    )

    output = toolset.get("category_attribute_values_search").executor(
        {"requests": [{"attribute_id": "85", "query": "无品牌"}]},
        execution_context(),
    )

    assert captured["query"] == "нет бренда"
    assert output["results"][0] == {
        "attribute_id": "85",
        "query": "无品牌",
        "values": [
            {
                "dictionary_value_id": "live-id",
                "value": "Нет бренда",
            }
        ],
        "error_code": "",
        "error_message": "",
    }
    assert ledger.get("85", "live-id") == {
        "dictionary_value_id": "live-id",
        "value": "Нет бренда",
    }


def test_invalid_batch_items_do_not_discard_valid_dictionary_results(monkeypatch):
    ledger = CategoryAttributeValueLedger.from_schema([
        {"id": "STYLE", "value_mode": "open_enum"},
        {"id": "GENDER", "value_mode": "strict_enum", "options": ["женский", "мужской"]},
    ])
    calls = []
    def values(*args, **kwargs):
        calls.append(kwargs["query"])
        return {"values": [{"id": "female", "value": "женский"}]}
    monkeypatch.setattr(category_attribute_tools, "fetch_category_attribute_values", values)
    tool = category_attribute_tools.build_category_attribute_value_toolset(
        platform="yandex", category_record={"category_id": "67831537"}, ledger=ledger,
    ).get("category_attribute_values_search")
    result = tool.executor({"requests": [
        {"attribute_id": "STYLE", "query": "冰丝"},
        {"attribute_id": "GENDER", "query": "女性"},
        {"attribute_id": "GENDER", "query": "женский"},
    ]}, execution_context())
    assert [item["error_code"] for item in result["results"]] == [
        "ATTRIBUTE_VALUES_NOT_QUERYABLE", "ATTRIBUTE_QUERY_LANGUAGE_MISMATCH", "",
    ]
    assert "женский" in result["results"][1]["error_message"]
    assert calls == ["женский"]
    assert ledger.get("GENDER", "female")["value"] == "женский"
