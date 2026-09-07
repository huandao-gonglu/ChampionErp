"""回放真实 Ozon 错误建议，验证从原生 Agent 到草稿保存的属性隔离。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from erp_web.context import get_context
from erp_web.runtime_units import category_attribute_ai_fill, category_attribute_tools
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.schemas.category_attribute_evidence import attribute_evidence_sources, evidence_reference_is_valid
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeAssignment, CategoryAttributeFillAgentOutput, CategoryAttributeFillOutputValidator,
    run_category_attribute_fill_agent,
)
from tests.test_category_attribute_fill_agent_service import factory_for, final_output


FIXTURE = json.loads((Path(__file__).parent / "fixtures/ozon_attribute_fill_mixed_invalid.json").read_text())


@pytest.mark.parametrize(("index", "expected_ids"), [
    (0, {"31", "8229"}),
    (1, {"31", "8229"}),
    (2, {"9163", "31", "8229", "4389"}),
])
def test_recorded_bad_attributes_do_not_erase_valid_saved_values(monkeypatch, index, expected_ids):
    """首轮空证据、二轮错路径、末轮错标签，都不能拖垮合法的品牌/类型。"""
    schema = deepcopy(FIXTURE["schema"])
    context = deepcopy(FIXTURE["product_context"])
    category = {
        "category_id": "970676618", "site": "global",
        "category_path": "Одежда / Аксессуары / Маска-повязка на лицо",
        "attributes": {
            "required": [item for item in schema if item["required"]],
            "optional": [item for item in schema if not item["required"]],
        },
    }
    product = {**context["product"], "source": context["source"], "drafts": {"ozon": {
        "brand": "Golovejoy", "category_id": category["category_id"],
        "category_path": category["category_path"],
    }}}
    monkeypatch.setattr(category_attribute_tools, "fetch_category_attribute_values",
                        lambda platform, category_id, attribute_id, **kwargs: {
                            "values": FIXTURE["lookups"][attribute_id],
                        })
    turns = 0

    def model(messages, info):
        nonlocal turns
        turns += 1
        if turns == 1:
            prompt = next(part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart))
            payload = json.loads(prompt.split("Input:\n")[1])
            assert {"source_path": ["source", "attributes", "适用性别"], "source_value": "女"} in payload["attribute_evidence_sources"]
            return ModelResponse(parts=[ToolCallPart("category_attribute_values_search", {
                "requests": [{"attribute_id": item["id"], "query": ""} for item in schema],
            }, tool_call_id="recorded-lookups")])
        assert turns == 2, "单个错误属性不应触发整批输出重试"
        return final_output(info, deepcopy(FIXTURE["outputs"][index]), "recorded-output")

    factory = factory_for(FunctionModel(model))
    monkeypatch.setattr(category_attribute_ai_fill, "run_category_attribute_fill_agent",
                        lambda payload, toolset, ledger: run_category_attribute_fill_agent(
                            payload, toolset, ledger, factory=factory, timeout_seconds=10))
    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "ozon", category)
    saved = get_context().products.save_product(updated)
    draft = saved["drafts"]["ozon"]
    assert turns == 2
    assert set(meta["ai_filled"]) == expected_ids
    assert set(draft["attributes"]) == expected_ids
    assert "4496" in meta["evidence_rejected"]
    assert "22232" in meta["evidence_rejected"]
    assert set(draft["validation_errors"]) == {"9163", "31", "8229", "10096"} - expected_ids
    assert draft["attributes"]["31"]["values"] == [{"dictionary_value_id": 970693417, "value": "Golovejoy"}]


def test_malformed_collection_member_rejects_whole_attribute_not_just_bad_value():
    ledger = CategoryAttributeValueLedger.from_schema([
        {"id": "SEASON", "required": True, "is_collection": True, "value_mode": "open_enum"},
        {"id": "MODEL", "required": True, "value_mode": "free_text"},
    ])
    output = CategoryAttributeFillAgentOutput.model_validate({"assignments": [
        {"attribute_id": "SEASON", "value": "лето"},
        {"attribute_id": "SEASON", "value": "весна", "evidence": {
            "source_path": ["source", "attributes", "适合季节"], "source_value": "", "reason": "春季",
        }},
        {"attribute_id": "MODEL", "value": "XKZ42"},
    ], "need_review": []})
    result = CategoryAttributeFillOutputValidator(ledger)(None, output)
    assert [item.attribute_id for item in result.assignments] == ["MODEL"]
    assert [item.id for item in result.need_review] == ["SEASON"]


def test_invalid_item_can_be_isolated_alongside_typed_assignments():
    output = CategoryAttributeFillAgentOutput(assignments=[
        CategoryAttributeAssignment(attribute_id="MODEL", value="XKZ42"),
        {"attribute_id": "BROKEN", "value": ""},
    ], need_review=[])
    assert [item.attribute_id for item in output.assignments] == ["MODEL"]
    assert set(output._rejected_attributes) == {"BROKEN"}


@pytest.mark.parametrize("payload", [
    {"assignments": "不是列表", "need_review": []},
    {"assignments": [], "need_review": [], "unknown": True},
    {"assignments": [{"attribute_id": "A", "value": "a"}] * 101, "need_review": []},
])
def test_invalid_response_envelope_still_requires_native_validation(payload):
    with pytest.raises(ValidationError):
        CategoryAttributeFillAgentOutput.model_validate(payload)


def test_copyable_evidence_only_contains_complete_allowed_string_leaves():
    context = {**deepcopy(FIXTURE["product_context"]), "draft": {"title": "不能作翻译证据"}}
    context["source"]["long_text"] = "长" * 1001
    references = attribute_evidence_sources(context)
    assert all(evidence_reference_is_valid(item, context) for item in references)
    assert {"source_path": ["source", "material"], "source_value": "冰丝"} in references
    assert all(item["source_path"] not in (["product", "materials"], ["source", "long_text"]) for item in references)
