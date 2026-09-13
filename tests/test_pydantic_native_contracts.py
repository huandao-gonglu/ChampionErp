"""当前安装版本的原生契约；全部使用可控模型，不访问真实平台。"""

import threading

import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import ExternalToolset
from pydantic_ai.tools import ToolDefinition


def test_native_parallel_sync_tools_and_dynamic_next_step():
    barrier = threading.Barrier(2, timeout=3)
    completed = []

    def prepare(draft_id: str) -> dict:
        barrier.wait()
        completed.append(draft_id)
        return {"draft_id": draft_id, "missing": ["weight"] if draft_id == "a" else []}

    def lookup(draft_id: str) -> dict:
        return {"draft_id": draft_id, "weight": 1.5}

    def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not results:
            return ModelResponse(
                parts=[ToolCallPart("prepare", {"draft_id": x}, x) for x in ("a", "b")]
            )
        if len(results) == 2:
            assert results[0].content["missing"] == ["weight"]
            return ModelResponse(parts=[ToolCallPart("lookup", {"draft_id": "a"}, "c")])
        return ModelResponse(parts=[TextPart("已读取缺少的重量")])

    result = Agent(FunctionModel(model), tools=[prepare, lookup]).run_sync(
        "准备这两个草稿"
    )
    assert set(completed) == {"a", "b"}
    assert result.output == "已读取缺少的重量"


def test_native_multiple_deferred_approvals_user_prompt_and_more_tools():
    executed = []

    def publish(draft_id: str) -> str:
        executed.append(draft_id)
        return draft_id

    def read_latest() -> str:
        return "最新草稿"

    def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not results:
            return ModelResponse(
                parts=[
                    ToolCallPart("external", {"draft_id": "a"}, "job-a"),
                    ToolCallPart("external", {"draft_id": "b"}, "job-b"),
                    ToolCallPart("publish", {"draft_id": "a"}, "approval-a"),
                    ToolCallPart("publish", {"draft_id": "b"}, "approval-b"),
                ]
            )
        if not any(p.tool_name == "read_latest" for p in results):
            assert any(
                isinstance(p, UserPromptPart) and p.content == "使用最新草稿"
                for m in messages
                for p in m.parts
            )
            assert {p.tool_call_id for p in results} == {
                "job-a",
                "job-b",
                "approval-a",
                "approval-b",
            }
            return ModelResponse(parts=[ToolCallPart("read_latest", {}, "read")])
        return ModelResponse(parts=[TextPart("完成")])

    agent = Agent(
        FunctionModel(model),
        output_type=str | DeferredToolRequests,
        tools=[Tool(publish, requires_approval=True), read_latest],
        toolsets=[
            ExternalToolset(
                [
                    ToolDefinition(
                        name="external",
                        parameters_json_schema={
                            "type": "object",
                            "properties": {"draft_id": {"type": "string"}},
                            "required": ["draft_id"],
                        },
                    )
                ]
            )
        ],
    )
    first = agent.run_sync("准备并请求发布")
    assert isinstance(first.output, DeferredToolRequests)
    assert len(first.output.calls) == len(first.output.approvals) == 2
    assert executed == []
    with pytest.raises(Exception, match="(?i)(result|deferred|missing)"):
        agent.run_sync(
            message_history=first.all_messages(),
            deferred_tool_results=DeferredToolResults(calls={"job-a": "完成"}),
        )
    resumed = agent.run_sync(
        "使用最新草稿",
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            calls={
                "job-a": {"ok": True},
                "job-b": {"ok": False, "missing": ["weight"]},
            },
            approvals={"approval-a": True, "approval-b": False},
        ),
    )
    assert executed == ["a"]
    assert resumed.output == "完成"
    assert resumed.run_id != first.run_id
    assert len(resumed.all_messages()) == len(first.all_messages()) + len(
        resumed.new_messages()
    )
