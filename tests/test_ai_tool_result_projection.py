"""重复结果去重必须可完整还原，且不能改变业务/审计对象。"""

import asyncio
from copy import deepcopy
from datetime import datetime
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from erp_web.services.ai_model_context_projection import project_model_request
from erp_web.services.ai_tool_result_projection import share_repeated_json_values
from erp_web.context import get_context


def expand_shared(result):
    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                return expand(result["shared_values"][value["$ref"].rsplit("/", 1)[1]])
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value
    return expand(result["value"])


def validation_result():
    skus = [{"sku_id": f"id-{i}", "sku": f"SKU-{i}", "name": f"粉色 / 规格{i}"} for i in range(41)]
    return {"passed": False, "validation_digest": "", "errors": [
        {"code": "IMAGE_NOT_PREPARED", "field": f"images.{i}", "severity": "error",
         "next_action": "准备图片", "affected_skus": deepcopy(skus)} for i in range(16)
    ], "warnings": [{"code": "REVIEW", "affected_skus": deepcopy(skus[:9])}],
        "summary": {"sku_count": 41, "stock": 0, "price": None}}


def test_repeated_collections_preserve_all_records_and_issue_associations():
    full = validation_result()
    original = deepcopy(full)
    projected = share_repeated_json_values(full)
    assert projected["encoding"] == "shared_json_values.v1"
    assert expand_shared(projected) == original == full
    assert len(json.dumps(projected)) < len(json.dumps(full)) / 4


def test_nested_shared_values_can_be_expanded_without_cycles():
    rows = [{"name": "属性" * 150}] * 12
    full = {"groups": [{"rows": deepcopy(rows), "flags": [False, None, 0]} for _ in range(6)]}
    projected = share_repeated_json_values(full)
    assert len(projected["shared_values"]) >= 2
    assert expand_shared(projected) == full


@pytest.mark.parametrize("content", [
    {"ok": True}, "长字符串" * 3000,
    {"data": [{"unique": str(i) * 200} for i in range(100)]},
    {"$ref": "业务引用", "data": ["x" * 1000] * 20},
    {"timestamp": datetime(2026, 1, 1)},
])
def test_small_unprofitable_or_non_json_results_remain_unchanged(content):
    assert share_repeated_json_values(content) is content


@pytest.mark.parametrize("streaming", [False, True])
def test_current_tool_result_is_compact_only_on_model_input(streaming):
    full = validation_result()
    history = [ModelRequest(parts=[UserPromptPart("检查草稿")]),
               ModelResponse(parts=[ToolCallPart("validate", {}, tool_call_id="check")]),
               ModelRequest(parts=[ToolReturnPart("validate", full, tool_call_id="check")])]
    seen = []

    def inspect(messages):
        part = next(p for m in messages for p in m.parts if isinstance(p, ToolReturnPart))
        assert expand_shared(part.content) == full
        assert part.tool_call_id == "check"
        seen.append(True)

    def respond(messages, info):
        inspect(messages)
        return ModelResponse(parts=[TextPart("未通过")])

    async def stream(messages, info):
        inspect(messages)
        yield "未通过"

    agent = Agent(FunctionModel(respond, stream_function=stream), capabilities=[Hooks(model_request=project_model_request)])
    if streaming:
        async def run():
            async with agent.run_stream(message_history=history) as result:
                await result.get_output()
                return result.all_messages()
        saved = asyncio.run(run())
    else:
        saved = agent.run_sync(message_history=history).all_messages()
    assert seen == [True]
    assert next(p.content for m in saved for p in m.parts if isinstance(p, ToolReturnPart)) == full
    persisted = get_context().pydantic_messages.save("shared-result-audit", saved).model_messages()
    assert next(p.content for m in persisted for p in m.parts if isinstance(p, ToolReturnPart)) == full
    assert history[-1].parts[0].content == full
