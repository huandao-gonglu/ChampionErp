from __future__ import annotations

from datetime import datetime, timedelta, timezone

from erp_web.runtime_units import category_attribute_tools
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger


def execution_context() -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task_attribute_values",
        attempt_id="attempt_attribute_values",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        budget_profile="category.attribute_fill.default",
        permissions=frozenset({"category.read"}),
    )


def test_attribute_value_tool_queries_strict_dictionary_and_records_ids(
    monkeypatch,
) -> None:
    schema = [
        {
            "id": "8229",
            "name": "Тип",
            "strict_enum": True,
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

    output = toolset.get("search_attribute_values").executor(
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
            "strict_enum": True,
            "allows_custom_value": False,
            "values": [
                {
                    "dictionary_value_id": "91443",
                    "value": "Вентилятор",
                }
            ],
            "error_code": "",
        }
    ]
    assert captured["platform"] == "ozon"
    assert captured["category_id"] == "91443"
    assert captured["site"] == "global"
    assert ledger.get("8229", "91443") == {
        "dictionary_value_id": "91443",
        "value": "Вентилятор",
    }


def test_suggested_enum_reports_that_custom_text_is_allowed() -> None:
    ledger = CategoryAttributeValueLedger.from_schema(
        [
            {
                "id": "STYLE",
                "name": "Style",
                "strict_enum": False,
                "options": ["Desk", "Floor"],
            }
        ]
    )
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="mercadolibre",
        category_record={"category_id": "MLM123", "site": "MLM"},
        ledger=ledger,
    )

    output = toolset.get("search_attribute_values").executor(
        {"requests": [{"attribute_id": "STYLE", "query": "wall"}]},
        execution_context(),
    )

    assert output["results"][0]["values"] == []
    assert output["results"][0]["strict_enum"] is False
    assert output["results"][0]["allows_custom_value"] is True

