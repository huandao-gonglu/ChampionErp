"""模型输入历史投影的架构与行为测试（修复计划第 15 节）。

核心安全不变量：
- 只要当前模型请求暴露任意工具，全部历史 ``ThinkingPart`` 必须原样保留；
- 投影是纯函数，绝不原地修改规范历史对象；
- 未闭合 Deferred/tool 尾部与当前 run 的 thinking 完整保护；
- 工具不可见时才移除旧完成轮次可省略 thinking，且移除后空响应整体删除。
"""

from __future__ import annotations

import dataclasses

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
    msgs = [_turn1_with_thinking(), ModelRequest(parts=[UserPromptPart("q2")], run_id="r2")]
    out = project_model_context_for_model(_Ctx({"global_task_start"}), msgs)
    assert [p.part_kind for p in out[0].parts] == ["thinking", "text"]


def test_tools_not_visible_strips_old_thinking_keeps_text() -> None:
    msgs = [_turn1_with_thinking(), ModelRequest(parts=[UserPromptPart("q2")], run_id="r2")]
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
        parts=[ThinkingPart(content="current thinking"), TextPart(content="still running")],
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

    msgs = [_turn1_with_thinking(), ModelRequest(parts=[UserPromptPart("q2")], run_id="r2")]
    out = project_model_context_for_model(_BrokenCtx(), msgs)
    assert [p.part_kind for p in out[0].parts] == ["thinking", "text"]
