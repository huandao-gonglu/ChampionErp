"""原生工具重试必须闭合消息，实际执行额度不得被纠正预留位置放大。"""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.services.ai_agent_factory import AiAgentExecutionError, AiAgentExecutionProfile
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from tests.test_category_match_agent_service import factory_for


def run_model(model, executions):
    definition = AiToolDefinition(
        name="lookup", version="1", description="只读查询", required_permission="test.read",
        input_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}, "additionalProperties": False},
        output_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}, "additionalProperties": False},
    )

    def execute(arguments, context):
        context.bounded_timeout_seconds()
        executions.append(arguments["value"])
        return arguments

    toolset = AiToolSet.bind("test.lookup", (definition,), {"lookup": deadline_aware_tool_executor(execute)})
    return factory_for(FunctionModel(model)).run_sync(
        profile=AiAgentExecutionProfile(
            use_case_id="test.budget", output_type=str, toolset_id=toolset.toolset_id,
            permissions=frozenset({"test.read"}), budget_profile="test.budget",
            timeout_seconds=10, max_model_requests=6, max_tool_calls=4, max_tool_output_bytes=4096,
        ),
        instructions="查询后回答。", user_prompt="测试通用工具预算。", toolset=toolset,
    )


def assert_closed_tool_messages(messages):
    pending = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            assert not pending, f"下一模型响应前仍有未闭合工具：{pending}"
            pending.update(part.tool_call_id for part in message.parts if isinstance(part, ToolCallPart))
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_name:
                    pending.discard(part.tool_call_id)
    assert not pending


def test_fifth_hidden_malformed_call_uses_native_retry_and_closes_messages():
    executions = []
    turns = 0

    def model(messages, info):
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}") for i in range(4)])
        assert not info.function_tools
        if turns == 2:
            return ModelResponse(parts=[ToolCallPart("lookup", {"value": ["格式错误"]}, tool_call_id="fifth")])
        assert any(isinstance(part, RetryPromptPart) and part.tool_call_id == "fifth" for message in messages for part in message.parts)
        return ModelResponse(parts=[TextPart("已根据已有结果完成。")])

    result = run_model(model, executions)
    assert executions == ["0", "1", "2", "3"]
    assert result.usage["tool_calls"] == 4
    assert_closed_tool_messages(result.messages)


def test_parallel_fifth_call_cannot_execute_in_correction_slot():
    executions = []
    turns = 0

    def model(messages, info):
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}") for i in range(5)])
        assert not info.function_tools
        assert "本次工具尚未执行" in str(messages)
        return ModelResponse(parts=[TextPart("已有四次结果，可以完成。")])

    result = run_model(model, executions)
    assert executions == ["0", "1", "2", "3"]
    assert result.usage["tool_calls"] == 4
    assert_closed_tool_messages(result.messages)


def test_oversized_batch_is_stopped_by_native_hard_limit_before_any_execution():
    executions = []

    def model(messages, info):
        return ModelResponse(parts=[ToolCallPart("lookup", {"value": str(i)}, tool_call_id=f"read-{i}") for i in range(6)])

    with pytest.raises(AiAgentExecutionError) as captured:
        run_model(model, executions)
    assert executions == []
    assert captured.value.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
    assert captured.value.details["origin"] == "local"
