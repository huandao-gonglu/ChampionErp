"""原生工具重试必须闭合消息，实际执行额度不得被纠正预留位置放大。"""

import pytest
import asyncio
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
)
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from tests.test_category_match_agent_service import factory_for


def test_nested_agent_failure_also_persists_parent_native_history(monkeypatch):
    factory = factory_for(FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("建议值")])))
    saved = []
    original_save = factory.message_store.save

    def save(conversation_id, messages, **kwargs):
        saved.append(conversation_id)
        return original_save(conversation_id, messages, **kwargs)

    monkeypatch.setattr(factory.message_store, "save", save)

    def review(ctx, output):
        raise AiAgentExecutionError("REVIEW_FAILED", "子复核失败。", conversation_id="review-child")

    with pytest.raises(AiAgentExecutionError) as captured:
        factory.run_sync(
            profile=AiAgentExecutionProfile(
                use_case_id="test.budget", output_type=str, toolset_id="test.history",
                permissions=frozenset(), budget_profile="test.budget", timeout_seconds=10,
                max_model_requests=2, max_tool_calls=1, max_tool_output_bytes=4096,
            ),
            instructions="返回建议。", user_prompt="保存父调用上下文。",
            toolset=AiToolSet("test.history", {}), output_validator=review,
        )
    assert captured.value.code == "REVIEW_FAILED"
    assert len(saved) == 1 and saved[0] != "review-child"
    history = factory.message_store.get(saved[0]).model_messages()
    assert "保存父调用上下文。" in str(history)
    assert "建议值" in str(history)


def test_total_deadline_closes_a_model_stream_that_keeps_producing_chunks():
    chunks = []
    closed = []

    async def stream(messages, info):
        try:
            for index in range(50):
                chunks.append(index)
                yield "继续生成"
                await asyncio.sleep(0.02)
        finally:
            closed.append(True)

    factory = factory_for(FunctionModel(stream_function=stream))
    with pytest.raises(AiAgentExecutionError) as captured:
        factory.run_sync(
            profile=AiAgentExecutionProfile(
                use_case_id="test.budget", output_type=str, toolset_id="test.deadline",
                permissions=frozenset(), budget_profile="test.budget", timeout_seconds=0.12,
                max_model_requests=2, max_tool_calls=1, max_tool_output_bytes=4096,
            ),
            instructions="持续返回文本。", user_prompt="测试总时限。",
            toolset=AiToolSet("test.deadline", {}),
        )
    assert captured.value.code == "TASK_DEADLINE_EXCEEDED"
    assert 0 < len(chunks) < 50
    assert closed == [True]


def run_model(model, executions):
    definition = AiToolDefinition(
        name="lookup",
        version="1",
        description="只读查询",
        required_permission="test.read",
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    def execute(arguments, context):
        context.bounded_timeout_seconds()
        executions.append(arguments["value"])
        return arguments

    toolset = AiToolSet.bind(
        "test.lookup", (definition,), {"lookup": deadline_aware_tool_executor(execute)}
    )
    return factory_for(FunctionModel(model)).run_sync(
        profile=AiAgentExecutionProfile(
            use_case_id="test.budget",
            output_type=str,
            toolset_id=toolset.toolset_id,
            permissions=frozenset({"test.read"}),
            budget_profile="test.budget",
            timeout_seconds=10,
            max_model_requests=6,
            max_tool_calls=4,
            max_tool_output_bytes=4096,
        ),
        instructions="查询后回答。",
        user_prompt="测试通用工具预算。",
        toolset=toolset,
    )


def assert_closed_tool_messages(messages):
    pending = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            assert not pending, f"下一模型响应前仍有未闭合工具：{pending}"
            pending.update(
                part.tool_call_id
                for part in message.parts
                if isinstance(part, ToolCallPart)
            )
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if (
                    isinstance(part, (ToolReturnPart, RetryPromptPart))
                    and part.tool_name
                ):
                    pending.discard(part.tool_call_id)
    assert not pending


def test_depleted_budget_rejects_hidden_extra_call():
    executions = []
    turns = 0

    def model(messages, info):
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}")
                    for i in range(4)
                ]
            )
        assert not info.function_tools
        if turns == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup", {"value": ["格式错误"]}, tool_call_id="fifth"
                    )
                ]
            )
        assert any(
            isinstance(part, RetryPromptPart) and part.tool_call_id == "fifth"
            for message in messages
            for part in message.parts
        )
        return ModelResponse(parts=[TextPart("已根据已有结果完成。")])

    with pytest.raises(AiAgentExecutionError) as captured:
        run_model(model, executions)
    assert captured.value.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
    assert sorted(executions) == ["0", "1", "2", "3"]


def test_parallel_batch_over_budget_does_not_partially_execute():
    executions = []
    turns = 0

    def model(messages, info):
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}")
                    for i in range(5)
                ]
            )
        assert not info.function_tools
        assert "本次工具尚未执行" in str(messages)
        return ModelResponse(parts=[TextPart("已有四次结果，可以完成。")])

    with pytest.raises(AiAgentExecutionError) as captured:
        run_model(model, executions)
    assert captured.value.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
    assert executions == []


def test_oversized_batch_is_stopped_by_native_hard_limit_before_any_execution():
    executions = []

    def model(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}")
                for i in range(6)
            ]
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_model(model, executions)
    assert executions == []
    assert captured.value.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
    assert captured.value.details["origin"] == "local"
