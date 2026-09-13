"""模型输入历史投影的架构与行为测试（修复计划第 15 节）。

核心安全不变量：
- 只要当前模型请求暴露任意工具，全部历史 ``ThinkingPart`` 必须原样保留；
- 投影是纯函数，绝不原地修改规范历史对象；
- 未闭合 Deferred/tool 尾部与当前 run 的 thinking 完整保护；
- 工具不可见时才移除旧完成轮次可省略 thinking，且移除后空响应整体删除。
"""

from __future__ import annotations

import dataclasses
import asyncio
import pytest

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from erp_web.services.ai_model_context_projection import (
    project_model_context_for_model,
    strip_stale_thinking,
    project_model_request,
)


class _Ctx:
    def __init__(self, tools: set[str], run_id: str = "r2") -> None:
        self.available_tool_names = tools
        self.run_id = run_id


def _turn1_with_thinking(run_id: str = "r1") -> ModelResponse:
    return ModelResponse(
        parts=[ThinkingPart(content="old thinking"), TextPart(content="answer 1")],
        run_id=run_id,
    )


def test_tools_visible_preserves_all_thinking() -> None:
    msgs = [
        _turn1_with_thinking(),
        ModelRequest(parts=[UserPromptPart("q2")], run_id="r2"),
    ]
    out = project_model_context_for_model(_Ctx({"product_publish_request"}), msgs)
    assert [p.part_kind for p in out[0].parts] == ["thinking", "text"]


def test_tools_not_visible_strips_old_thinking_keeps_text() -> None:
    msgs = [
        _turn1_with_thinking(),
        ModelRequest(parts=[UserPromptPart("q2")], run_id="r2"),
    ]
    out = project_model_context_for_model(_Ctx(set()), msgs)
    assert [p.part_kind for p in out[0].parts] == ["text"]


def test_projection_is_pure_and_does_not_mutate_input() -> None:
    turn1 = _turn1_with_thinking()
    msgs = [turn1, ModelRequest(parts=[UserPromptPart("q2")], run_id="r2")]
    _ = strip_stale_thinking(msgs, current_run_id="r2")
    # 原对象保持 thinking + text，未被原地修改。
    assert [p.part_kind for p in turn1.parts] == ["thinking", "text"]


def test_unclosed_tool_call_tail_protected() -> None:
    resp = ModelResponse(
        parts=[
            ThinkingPart(content="tool thinking"),
            ToolCallPart(tool_name="t", args={}, tool_call_id="c1"),
        ],
        run_id="r1",
    )
    out = strip_stale_thinking([resp], current_run_id="r9")
    assert [p.part_kind for p in out[0].parts] == ["thinking", "tool-call"]


def test_closed_tool_call_response_can_drop_thinking_when_complete() -> None:
    resp = dataclasses.replace(
        ModelResponse(
            parts=[
                ThinkingPart(content="tool thinking"),
                ToolCallPart(tool_name="t", args={}, tool_call_id="c1"),
            ],
            run_id="r1",
        ),
        state="complete",
    )
    toolret = ModelRequest(
        parts=[ToolReturnPart(tool_name="t", content="ok", tool_call_id="c1")],
        run_id="r1",
    )
    out = strip_stale_thinking([resp, toolret], current_run_id="r9")
    assert [p.part_kind for p in out[0].parts] == ["tool-call"]


def test_current_run_messages_fully_preserved() -> None:
    current = ModelResponse(
        parts=[
            ThinkingPart(content="current thinking"),
            TextPart(content="still running"),
        ],
        run_id="r2",
    )
    out = strip_stale_thinking([current], current_run_id="r2")
    assert [p.part_kind for p in out[0].parts] == ["thinking", "text"]


def test_only_thinking_response_is_dropped_entirely() -> None:
    only_thinking = dataclasses.replace(
        ModelResponse(parts=[ThinkingPart(content="just thinking")], run_id="r1"),
        state="complete",
    )
    keep = ModelRequest(parts=[UserPromptPart("q2")], run_id="r2")
    out = strip_stale_thinking([only_thinking, keep], current_run_id="r2")
    # 空 assistant 响应被整体删除，只保留用户消息。
    assert len(out) == 1
    assert out[0].kind == "request"


def test_unresolvable_tool_visibility_falls_back_to_preserve() -> None:
    class _BrokenCtx:
        @property
        def available_tool_names(self):
            raise RuntimeError("cannot resolve")

        run_id = "r2"

    msgs = [
        _turn1_with_thinking(),
        ModelRequest(parts=[UserPromptPart("q2")], run_id="r2"),
    ]
    out = project_model_context_for_model(_BrokenCtx(), msgs)
    assert [p.part_kind for p in out[0].parts] == ["thinking", "text"]


@pytest.mark.parametrize("streaming", [False, True])
def test_native_request_projection_keeps_complete_audit_history(streaming):
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import Hooks
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.run import AgentRunResultEvent
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    from erp_web.context import get_context

    full = {"status": "partial", "items": [{"sku_id": str(i), "error": "真实超时", "attributes": {"color": "x" * 300}} for i in range(100)]}
    history = [ModelRequest(parts=[UserPromptPart("上一轮任务")], run_id="old"),
               ModelResponse(parts=[ThinkingPart("旧思考"), ToolCallPart("old_tool", {}, tool_call_id="old-call")], run_id="old"),
               ModelRequest(parts=[ToolReturnPart("old_tool", full, tool_call_id="old-call")], run_id="old"),
               ModelResponse(parts=[TextPart("有失败项，尚未完成。")], run_id="old")]
    original = ModelMessagesTypeAdapter.dump_json(history)
    seen = []

    def inspect(messages):
        result = next(p for m in messages for p in m.parts if isinstance(p, ToolReturnPart))
        assert result.tool_call_id == "old-call"
        assert result.content["history_preview"] is True
        assert result.content["status"] == "partial"
        assert result.content["item_count"] == result.content["items_with_error"] == 100
        assert len(result.model_response_str()) < 2500
        seen.append(True)

    def respond(messages, info):
        inspect(messages)
        return ModelResponse(parts=[TextPart("继续处理")])

    async def stream(messages, info):
        inspect(messages)
        yield "继续处理"

    agent = Agent(FunctionModel(respond, stream_function=stream), capabilities=[Hooks(model_request=project_model_request)])
    if streaming:
        async def run():
            async with agent.run_stream_events("新任务", message_history=history) as events:
                async for event in events:
                    if isinstance(event, AgentRunResultEvent):
                        return event.result
        result = asyncio.run(run())
    else:
        result = agent.run_sync("新任务", message_history=history)
    assert seen == [True]
    assert ModelMessagesTypeAdapter.dump_json(history) == original
    saved = get_context().pydantic_messages.save("projection-audit", result.all_messages()).model_messages()
    assert next(p.content for m in saved for p in m.parts if isinstance(p, ToolReturnPart)) == full
    assert any(isinstance(p, ThinkingPart) and p.content == "旧思考" for m in saved for p in m.parts)


def test_projection_preserves_native_request_metadata_and_current_results():
    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
    from pydantic_ai.models.function import FunctionModel
    context = ModelRequestContext(model=FunctionModel(lambda *_: ModelResponse(parts=[TextPart("完成")])),
        messages=[ModelRequest(parts=[UserPromptPart("当前任务")]),
                  ModelResponse(parts=[ToolCallPart("current", {}, tool_call_id="current-call")]),
                  ModelRequest(parts=[ToolReturnPart("current", "x" * 9000, tool_call_id="current-call")])],
        model_settings=None, model_request_parameters=ModelRequestParameters())
    context.streaming = True
    context.model_id = "current-model"

    async def handler(projected):
        assert projected is not context
        assert projected.streaming is True and projected.model_id == "current-model"
        assert projected.messages[-1].parts[0].content == "x" * 9000
        return ModelResponse(parts=[TextPart("完成")])

    asyncio.run(project_model_request(_Ctx({"current"}), request_context=context, handler=handler))
    assert context.messages[-1].parts[0].content == "x" * 9000
