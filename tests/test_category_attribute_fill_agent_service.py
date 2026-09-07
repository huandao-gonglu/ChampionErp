from __future__ import annotations

from typing import Any
import json

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings, ToolOrOutput

from erp_web.context import get_context
from erp_web.runtime_units import category_attribute_tools
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.services.ai_agent_factory import AiAgentFactory
from erp_web.services.ai_model_config import AI_USE_CASES
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.category_attribute_fill_agent_service import (
    CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION,
    CategoryAttributeAssignment,
    CategoryAttributeFillAgentOutput,
    CategoryAttributeFillOutputValidator,
    run_category_attribute_fill_agent,
)
from tests.ai_function_model_streaming import streaming_function_model


SCHEMA = [
    {
        "id": "8229",
        "name": "Тип",
        "required": True,
        "value_mode": "strict_enum",
        "is_collection": False,
        "max_value_count": 0,
        "options": [],
    },
    {
        "id": "STYLE",
        "name": "Style",
        "required": True,
        "value_mode": "open_enum",
        "is_collection": False,
        "max_value_count": 0,
        "options": ["Desk", "Floor"],
    },
]

PAYLOAD = {
    "platform": "ozon",
    "site": "global",
    "category_id": "91443",
    "category_path": "Бытовая техника / Вентилятор",
    "product_context": {"source": {"title": "F30 手持风扇"}},
    "attributes": SCHEMA,
}

NUMBER_UNIT_SCHEMA = [
    {
        "id": "WEIGHT",
        "name": "Weight",
        "required": True,
        "value_type": "number_unit",
        "value_mode": "free_text",
        "unit_options": ["g", "kg", "lb"],
        "default_unit": "kg",
        "is_collection": False,
        "max_value_count": 0,
        "options": [],
    }
]

NUMBER_UNIT_PAYLOAD = {
    "platform": "mercadolibre",
    "site": "CBT",
    "category_id": "CBT455865",
    "category_path": "Portable fans",
    "product_context": {
        "source": {"title": "Portable fan, net weight 0.182 kg"}
    },
    "attributes": NUMBER_UNIT_SCHEMA,
}

OPEN_ENUM_COLLECTION_SCHEMA = [
    {
        "id": "700001",
        "name": "Supported labels",
        "required": True,
        "value_mode": "open_enum",
        "is_collection": True,
        "max_value_count": 2,
        "options": ["Alpha", "Beta"],
    }
]


def factory_for(model: FunctionModel) -> AiAgentFactory:
    context = get_context()
    streaming_model = streaming_function_model(model)

    def binding(*args, **kwargs):
        del args, kwargs
        return PydanticModelBinding(
            model=streaming_model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=context.pydantic_messages,
        model_binding_factory=binding,
    )


def final_output(
    agent_info: AgentInfo,
    payload: dict[str, Any],
    call_id: str,
) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                agent_info.output_tools[0].name,
                payload,
                tool_call_id=call_id,
            )
        ]
    )


def test_validator_isolates_missing_dictionary_ids_without_blocking_other_attributes():
    ledger = CategoryAttributeValueLedger.from_schema([
        {"id": "GENDER", "value_mode": "strict_enum", "required": True},
        {"id": "SEASON", "value_mode": "strict_enum", "required": False},
    ])
    validator = CategoryAttributeFillOutputValidator(ledger)
    output = CategoryAttributeFillAgentOutput(assignments=[
        CategoryAttributeAssignment(attribute_id="GENDER", value="женский"),
        CategoryAttributeAssignment(attribute_id="SEASON", value="лето"),
    ], need_review=[])
    validated = validator(None, output)
    assert validated.assignments == []
    assert [item.id for item in validated.need_review] == ["GENDER"]
    assert set(validated._rejected_attributes) == {"GENDER", "SEASON"}
    assert validator.error_code == "ATTRIBUTE_ENUM_ID_REQUIRED"


def test_native_agent_batches_gender_and_season_and_saves_translated_evidence(monkeypatch):
    from erp_web.product_model import default_product_model
    from erp_web.runtime_units import category_attribute_ai_fill

    product = default_product_model()
    product["source"]["attributes"] = {"适用性别": "女", "适合季节": "夏季,春季"}
    category = {"category_id": "67831537", "site": "global", "attributes": {
        "required": [{"id": "14805991", "name": "Пол", "required": True,
                      "value_mode": "strict_enum", "dictionary_id": "gender"}],
        "optional": [{"id": "27142893", "name": "Сезон", "value_mode": "strict_enum",
                      "dictionary_id": "season"}],
    }}
    candidates = {
        "14805991": {"id": "14805993", "value": "женский"},
        "27142893": {"id": "32034092", "value": "демисезон/лето"},
    }
    monkeypatch.setattr(category_attribute_tools, "fetch_category_attribute_values",
                        lambda platform, cat, attr, **kwargs: {"values": [candidates[attr]]})
    turns = 0
    def model(messages, agent_info):
        nonlocal turns
        turns += 1
        if turns == 1:
            prompt = next(part.content for msg in messages for part in msg.parts if isinstance(part, UserPromptPart))
            payload = json.loads(prompt.split('Input:\n')[1])
            assert payload["queryable_attribute_ids"] == ["14805991", "27142893"]
            assert "俄语" in payload["dictionary_language"]
            return ModelResponse(parts=[ToolCallPart("category_attribute_values_search", {"requests": [
                {"attribute_id": "14805991", "query": "женский"},
                {"attribute_id": "27142893", "query": "демисезон/лето"},
            ]}, tool_call_id="batch")])
        assert turns == 2
        return final_output(agent_info, {"assignments": [
            {"attribute_id": attr_id, "value": candidates[attr_id]["value"],
             "dictionary_value_id": candidates[attr_id]["id"],
             "evidence": {"source_path": ["source", "attributes", field],
                          "source_value": product["source"]["attributes"][field],
                          "reason": reason}}
            for attr_id, field, reason in [
                ("14805991", "适用性别", "女性对应平台女款选项"),
                ("27142893", "适合季节", "来源春夏季对应平台换季/夏季选项"),
            ]
        ], "need_review": []}, "final")

    factory = factory_for(FunctionModel(model))
    monkeypatch.setattr(category_attribute_ai_fill, "run_category_attribute_fill_agent",
                        lambda payload, toolset, ledger: run_category_attribute_fill_agent(
                            payload, toolset, ledger, factory=factory, timeout_seconds=10))
    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "yandex", category)
    assert turns == 2
    assert meta["ai_filled"] == ["14805991", "27142893"]
    assert "evidence_rejected" not in meta
    assert updated["drafts"]["yandex"]["validation_errors"] == []


def test_agent_queries_only_strict_enum_and_allows_custom_open_enum_value(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        category_attribute_tools,
        "fetch_category_attribute_values",
        lambda *args, **kwargs: {
            "values": [{"id": "91443", "value": "Вентилятор"}]
        },
    )
    ledger = CategoryAttributeValueLedger.from_schema(SCHEMA)
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="ozon",
        category_record={"category_id": "91443", "site": "global"},
        ledger=ledger,
    )
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "category_attribute_values_search",
                        {
                            "requests": [
                                {
                                    "attribute_id": "8229",
                                    "query": "вентилятор",
                                },
                            ]
                        },
                        tool_call_id="values-1",
                    )
                ]
            )
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert returns[-1].content["results"][0]["values"][0] == {
            "dictionary_value_id": "91443",
            "value": "Вентилятор",
        }
        return final_output(
            agent_info,
            {
                "assignments": [
                    {
                        "attribute_id": "8229",
                        "value": "Вентилятор",
                        "dictionary_value_id": "91443",
                    },
                    {
                        "attribute_id": "STYLE",
                        "value": "Wall mounted",
                        "dictionary_value_id": "",
                    },
                ],
                "need_review": [],
            },
            "final-1",
        )

    result = run_category_attribute_fill_agent(
        PAYLOAD,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=factory_for(FunctionModel(model)),
    )

    assert result.output["assignments"][0]["dictionary_value_id"] == "91443"
    assert result.output["assignments"][1]["value"] == "Wall mounted"
    assert result.outcome is not None
    assert result.outcome.usage["tool_calls"] == 1
    history = get_context().pydantic_messages.get(result.outcome.conversation_id)
    assert history is not None
    assert history.messages_json == ModelMessagesTypeAdapter.dump_json(
        result.outcome.messages
    )
    result.finish_business_result({"status": "completed"})


@pytest.mark.parametrize("invalid_first_batch", [False, True])
def test_query_limit_keeps_final_output_available_and_saves_known_values(monkeypatch, invalid_first_batch):
    """查满四轮后停止暴露查询工具，已有候选仍通过原生最终输出与业务校验保存。"""
    from erp_web.product_model import default_product_model
    from erp_web.runtime_units import category_attribute_ai_fill

    monkeypatch.setattr(category_attribute_tools, "fetch_category_attribute_values",
                        lambda *args, **kwargs: {"values": [{"id": "91443", "value": "Вентилятор"}]})
    product = default_product_model()
    category = {"category_id": "91443", "site": "global", "category_path": "Бытовая техника / Вентилятор",
                "attributes": {"required": SCHEMA[:1], "optional": []}}
    product["drafts"]["ozon"]["target_sites"][0]["category_id"] = "91443"
    turns = 0

    def model(messages, agent_info):
        nonlocal turns
        turns += 1
        if invalid_first_batch and turns == 1:
            return ModelResponse(parts=[ToolCallPart("category_attribute_values_search", {
                "requests": [{"attribute_id": "8229", "query": "вентилятор"}] * 9,
            }, tool_call_id="oversized-query")])
        if invalid_first_batch and turns == 2:
            retries = [part for message in messages if isinstance(message, ModelRequest)
                       for part in message.parts if isinstance(part, RetryPromptPart)]
            assert "maxItems" in str(retries[-1].content)
        query_turn = turns - int(invalid_first_batch)
        if agent_info.function_tools:
            assert query_turn <= 4
            return ModelResponse(parts=[ToolCallPart("category_attribute_values_search", {
                "requests": [{"attribute_id": "8229", "query": f"вентилятор {turns}"}],
            }, tool_call_id=f"query-{turns}")])
        assert query_turn == 5
        assert agent_info.output_tools
        assert agent_info.model_settings["tool_choice"] == ToolOrOutput(function_tools=[])
        return final_output(agent_info, {"assignments": [{"attribute_id": "8229", "value": "Вентилятор",
                            "dictionary_value_id": "91443"}], "need_review": []}, "final")

    factory = factory_for(FunctionModel(model))
    monkeypatch.setattr(category_attribute_ai_fill, "run_category_attribute_fill_agent",
                        lambda payload, toolset, ledger: run_category_attribute_fill_agent(
                            payload, toolset, ledger, factory=factory, timeout_seconds=10))
    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "ozon", category)
    assert turns == 5 + int(invalid_first_batch)
    assert meta["ai_filled"] == ["8229"]
    assert "warning" not in meta
    assert updated["drafts"]["ozon"]["attributes"]["8229"]["values"][0]["dictionary_value_id"] == 91443


def test_validator_rejects_strict_enum_not_returned_by_tool() -> None:
    ledger = CategoryAttributeValueLedger.from_schema(SCHEMA)
    validator = CategoryAttributeFillOutputValidator(ledger)
    output = CategoryAttributeFillAgentOutput(
        assignments=[
            CategoryAttributeAssignment(
                attribute_id="8229",
                value="Ручной вентилятор",
                dictionary_value_id="invented",
            ),
            CategoryAttributeAssignment(
                attribute_id="STYLE",
                value="Wall mounted",
            ),
        ],
        need_review=[],
    )

    validated = validator(None, output)
    assert [item.attribute_id for item in validated.assignments] == ["STYLE"]
    assert "只能选择本次工具真实返回" in validated.need_review[0].reason


def test_validator_allows_multiple_values_for_open_enum_collection() -> None:
    validator = CategoryAttributeFillOutputValidator(
        CategoryAttributeValueLedger.from_schema(OPEN_ENUM_COLLECTION_SCHEMA)
    )
    output = CategoryAttributeFillAgentOutput(
        assignments=[
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="Alpha",
            ),
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="Beta",
            ),
        ],
        need_review=[],
    )

    assert validator(None, output) is output  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("schema", "expected_message"),
    [
        (
            [{**OPEN_ENUM_COLLECTION_SCHEMA[0], "is_collection": False}],
            "只能填写一个值",
        ),
        (
            [{**OPEN_ENUM_COLLECTION_SCHEMA[0], "max_value_count": 1}],
            "最多填写 1 个值",
        ),
    ],
)
def test_validator_rejects_values_beyond_attribute_cardinality(
    schema: list[dict[str, Any]],
    expected_message: str,
) -> None:
    validator = CategoryAttributeFillOutputValidator(
        CategoryAttributeValueLedger.from_schema(schema)
    )
    output = CategoryAttributeFillAgentOutput(
        assignments=[
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="Alpha",
            ),
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="Beta",
            ),
        ],
        need_review=[],
    )

    validated = validator(None, output)
    assert validated.assignments == []
    assert expected_message in validated.need_review[0].reason
    assert validator.error_code == "ATTRIBUTE_VALUE_COUNT_INVALID"


def test_validator_rejects_duplicate_collection_values() -> None:
    validator = CategoryAttributeFillOutputValidator(
        CategoryAttributeValueLedger.from_schema(OPEN_ENUM_COLLECTION_SCHEMA)
    )
    output = CategoryAttributeFillAgentOutput(
        assignments=[
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="Alpha",
            ),
            CategoryAttributeAssignment(
                attribute_id="700001",
                value="ALPHA",
            ),
        ],
        need_review=[],
    )

    validated = validator(None, output)
    assert validated.assignments == []
    assert "不得重复填写" in validated.need_review[0].reason
    assert validator.error_code == "ATTRIBUTE_VALUE_DUPLICATED"


def test_agent_receives_and_preserves_number_unit_contract() -> None:
    ledger = CategoryAttributeValueLedger.from_schema(NUMBER_UNIT_SCHEMA)
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="mercadolibre",
        category_record={"category_id": "CBT455865", "site": "CBT"},
        ledger=ledger,
    )

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        instructions = str(agent_info.instructions or "")
        assert "带单位属性" in instructions
        assert "YueShang" in instructions
        assert "DJI" in instructions
        assert "具体品牌时不得选择平台官方无品牌候选" in instructions
        user_prompts = [
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        rendered_prompt = "\n".join(user_prompts)
        assert '\"value_type\":\"number_unit\"' in rendered_prompt
        assert '\"unit_options\":[\"g\",\"kg\",\"lb\"]' in rendered_prompt
        assert '\"default_unit\":\"kg\"' in rendered_prompt
        return final_output(
            agent_info,
            {
                "assignments": [
                    {
                        "attribute_id": "WEIGHT",
                        "value": "0.182",
                        "dictionary_value_id": "",
                        "unit": "kg",
                    }
                ],
                "need_review": [],
            },
            "number-unit-final",
        )

    result = run_category_attribute_fill_agent(
        NUMBER_UNIT_PAYLOAD,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=factory_for(FunctionModel(model)),
    )

    assert result.output["assignments"] == [
        {
            "evidence": None,
            "attribute_id": "WEIGHT",
            "value": "0.182",
            "dictionary_value_id": "",
            "unit": "kg",
        }
    ]
    assert (
        AI_USE_CASES["category.attribute_fill"]["result_schema"]
        == CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION
    )
    result.finish_business_result({"status": "completed"})


@pytest.mark.parametrize(
    ("value", "unit", "error_code"),
    [
        ("0.182", "", "ATTRIBUTE_UNIT_REQUIRED"),
        ("0.182", "oz", "ATTRIBUTE_UNIT_INVALID"),
        ("NaN", "kg", "ATTRIBUTE_NUMBER_INVALID"),
    ],
)
def test_validator_rejects_invalid_number_unit_assignment(
    value: str,
    unit: str,
    error_code: str,
) -> None:
    ledger = CategoryAttributeValueLedger.from_schema(NUMBER_UNIT_SCHEMA)
    validator = CategoryAttributeFillOutputValidator(ledger)
    output = CategoryAttributeFillAgentOutput(
        assignments=[
            CategoryAttributeAssignment(
                attribute_id="WEIGHT",
                value=value,
                unit=unit,
            )
        ],
        need_review=[],
    )

    validated = validator(None, output)
    assert validated.assignments == []
    assert [item.id for item in validated.need_review] == ["WEIGHT"]
    assert validator.error_code == error_code
