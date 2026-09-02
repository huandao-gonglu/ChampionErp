from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

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

    with pytest.raises(ModelRetry, match="只能选择本次工具真实返回"):
        validator(None, output)  # type: ignore[arg-type]


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

    with pytest.raises(ModelRetry, match=expected_message):
        validator(None, output)  # type: ignore[arg-type]
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

    with pytest.raises(ModelRetry, match="不得重复填写"):
        validator(None, output)  # type: ignore[arg-type]
    assert validator.error_code == "ATTRIBUTE_VALUE_DUPLICATED"


def test_agent_receives_and_preserves_number_unit_contract() -> None:
    ledger = CategoryAttributeValueLedger.from_schema(NUMBER_UNIT_SCHEMA)
    toolset = category_attribute_tools.build_category_attribute_value_toolset(
        platform="mercadolibre",
        category_record={"category_id": "CBT455865", "site": "CBT"},
        ledger=ledger,
    )

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        assert "带单位属性" in str(agent_info.instructions or "")
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

    with pytest.raises(ModelRetry):
        validator(None, output)  # type: ignore[arg-type]
    assert validator.error_code == error_code
