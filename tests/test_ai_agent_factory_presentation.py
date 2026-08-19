"""AiAgentFactory 统一流式内核的 presentation 集成测试。

覆盖重构计划 §7 / §12.1 / §12.3 / §16：

- ``run_sync()`` 在 presentation scope 中通过 observer 发布官方 Vercel
  chunk，并推进展示生命周期；业务收尾（``finish_request``）标记终态。
- 无 presentation scope 时业务语义不变（等价性由既有黄金测试覆盖）。
- 流中期失败：官方 transform 输出 error/finish chunk，业务错误语义不变。
- 装配期（流前）失败：observer 补发官方 error chunk；不产生 had_agent_run。
- 运行内部再次进入 factory 派生 child：继承 presentation/observer，获得
  自己的 run_id/parent_run_id，不建立第二条 assistant stream。
- contextvar 穿过 Pydantic tool 执行线程/协程边界（§16 风险控制）。
- observer 故障只降级展示，不改写业务执行语义，也不让 Agent 重复执行。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.db import ErpDatabase
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_presentation_context import (
    AiPresentationContext,
    bind_presentation_context,
    current_presentation_context,
    root_presentation_context,
)
from erp_web.services.ai_presentation_registry import (
    COMPLETED,
    FAILED,
    FINALIZING,
    AiPresentationRegistry,
)
from erp_web.services.ai_presentation_service import (
    claim_presentation_scope,
    reserve_presentation,
)
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.stores.pydantic_message_store import PydanticMessageStore
from tests.ai_function_model_streaming import streaming_function_model


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


TEXT_PROFILE = AiAgentExecutionProfile(
    use_case_id="presentation.kernel.text",
    output_type=str,
    toolset_id="presentation.kernel.empty",
    budget_profile="presentation.kernel.v1",
    permissions=frozenset(),
    timeout_seconds=10,
    max_model_requests=2,
    max_tool_calls=1,
    max_tool_output_bytes=4096,
    retries=0,
)
EMPTY_TOOLSET = AiToolSet.bind("presentation.kernel.empty", [], {})

PARENT_PROFILE = AiAgentExecutionProfile(
    use_case_id="presentation.kernel.parent",
    output_type=Answer,
    toolset_id="presentation.kernel.parent",
    budget_profile="presentation.kernel.v1",
    permissions=frozenset({"presentation.run"}),
    timeout_seconds=10,
    max_model_requests=4,
    max_tool_calls=2,
    max_tool_output_bytes=4096,
    retries=0,
)

CHILD_PROFILE = AiAgentExecutionProfile(
    use_case_id="presentation.kernel.child",
    output_type=str,
    toolset_id="presentation.kernel.child.empty",
    budget_profile="presentation.kernel.v1",
    permissions=frozenset(),
    timeout_seconds=10,
    max_model_requests=2,
    max_tool_calls=1,
    max_tool_output_bytes=4096,
    retries=0,
)
CHILD_TOOLSET = AiToolSet.bind("presentation.kernel.child.empty", [], {})


def _factory(
    tmp_path: Path,
    models: dict[str, FunctionModel],
) -> AiAgentFactory:
    message_store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    streaming_models = {
        use_case_id: streaming_function_model(model)
        for use_case_id, model in models.items()
    }

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del kwargs
        use_case_id = args[2]
        return PydanticModelBinding(
            model=streaming_models[use_case_id],
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=tmp_path,
        app_config={},
        message_store=message_store,
        model_binding_factory=binding,
    )


def _reserve_and_claim(
    registry: AiPresentationRegistry,
    *,
    title: str,
) -> tuple[str, AiPresentationContext]:
    reserved = reserve_presentation(registry, display_title=title)
    presentation_id = str(reserved["presentation_id"])
    scope = claim_presentation_scope(registry, presentation_id=presentation_id)
    assert scope is not None
    return presentation_id, scope


def _drain_chunks(registry: AiPresentationRegistry, presentation_id: str) -> str:
    chunks, _cursor, closed = registry.read_chunks(
        presentation_id,
        0,
        wait_timeout=0.2,
    )
    assert closed is True
    return b"".join(chunks).decode("utf-8")


def _parse_frames(text: str) -> list[dict[str, Any]]:
    """解析官方 Vercel SDK v7 JSON SSE 帧（忽略 [DONE] 哨兵）。"""

    frames: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        frames.append(json.loads(line[len("data: ") :]))
    return frames


def parent_toolset(executor) -> AiToolSet:
    definition = AiToolDefinition(
        name="spawn_child",
        version="1",
        description="运行一个子 Agent",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["child_answer"],
            "properties": {"child_answer": {"type": "string"}},
            "additionalProperties": False,
        },
        required_permission="presentation.run",
        side_effect="none",
    )
    return AiToolSet.bind(
        "presentation.kernel.parent",
        [definition],
        {"spawn_child": deadline_aware_tool_executor(executor)},
    )


class RecordingObserver:
    """记录生命周期调用并透传 native events；用于断言 root/child 派生。"""

    def __init__(self) -> None:
        self.claim_root_run_calls: list[str] = []
        self.run_started_calls: list[tuple[str, str, str]] = []
        self.running_calls: list[str] = []
        self.finalizing_calls: list[str] = []
        self.completed_calls: list[str] = []
        self.failed_calls: list[tuple[str, str]] = []
        self.child_status_calls: list[tuple[str, str, str]] = []
        self.observe_calls = 0
        self._claimed_root_run_id = ""

    def claim_root_run(self, *, run_id: str) -> str:
        """模拟 registry：首个领取成功，之后返回已领取的 root run_id。"""

        normalized = str(run_id or "").strip()
        self.claim_root_run_calls.append(normalized)
        if not self._claimed_root_run_id:
            self._claimed_root_run_id = normalized
        return self._claimed_root_run_id

    def run_started(
        self,
        *,
        run_id: str,
        parent_run_id: str,
        use_case_id: str,
        label: str,
    ) -> None:
        del label
        self.run_started_calls.append((run_id, parent_run_id, use_case_id))

    def running(self, *, run_id: str) -> None:
        self.running_calls.append(run_id)

    def tool_activity(self, *, run_id: str, tool_name: str) -> None:
        return None

    def finalizing(self, *, run_id: str) -> None:
        self.finalizing_calls.append(run_id)

    def completed(self, *, run_id: str) -> None:
        self.completed_calls.append(run_id)

    def failed(self, *, run_id: str, code: str, message: str) -> None:
        del message
        self.failed_calls.append((run_id, code))

    def cancelled(self, *, run_id: str) -> None:
        return None

    def child_status(
        self,
        *,
        child_run_id: str,
        status: str,
        label: str,
    ) -> None:
        self.child_status_calls.append((child_run_id, status, label))

    def observe_native_events(self, events):
        self.observe_calls += 1

        async def passthrough():
            async for event in events:
                yield event

        return passthrough()


def _recording_scope(recorder: RecordingObserver) -> AiPresentationContext:
    return root_presentation_context(
        presentation_id="presentation_" + "ab" * 16,
        root_run_id="root_run_" + "cd" * 16,
        conversation_id="conversation_" + "ef" * 16,
        origin="business.ui",
        observer=recorder,
    )


def _parent_model(messages: list[Any], info: AgentInfo) -> ModelResponse:
    tool_returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if not tool_returns:
        return ModelResponse(
            parts=[ToolCallPart("spawn_child", {}, tool_call_id="child-1")]
        )
    return ModelResponse(
        parts=[
            ToolCallPart(
                info.output_tools[0].name,
                {"answer": "父完成"},
                tool_call_id="final-1",
            )
        ]
    )


# -- 成功路径：官方 chunk 发布与展示生命周期 ---------------------------------


def test_run_sync_publishes_official_chunks_and_lifecycle(tmp_path: Path) -> None:
    calls = 0

    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("你好，展示层")])

    factory = _factory(tmp_path, {TEXT_PROFILE.use_case_id: FunctionModel(model)})
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 内核测试")

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=TEXT_PROFILE,
            instructions="直接回答。",
            user_prompt="打招呼。",
            toolset=EMPTY_TOOLSET,
        )
    assert outcome.output == "你好，展示层"
    assert calls == 1
    outcome.complete()

    payload = registry.status_payload(presentation_id)
    assert payload is not None
    assert payload["had_agent_run"] is True
    # 业务终检之后、HTTP 边界收尾之前处于 finalizing（终态由边界标记）
    assert payload["status"] == FINALIZING

    registry.finish_request(presentation_id, request_failed=False)
    payload = registry.status_payload(presentation_id)
    assert payload is not None
    assert payload["status"] == COMPLETED
    assert payload["terminal"] is True

    text = _drain_chunks(registry, presentation_id)
    frames = _parse_frames(text)
    assert frames, "成功运行必须发布官方 Vercel chunk"
    deltas = [
        str(frame.get("delta", ""))
        for frame in frames
        if frame.get("type") == "text-delta"
    ]
    assert "".join(deltas) == "你好，展示层"
    assert any(frame.get("type") == "finish" for frame in frames), (
        "必须包含官方 finish 帧"
    )

    # 消息历史仍只持久化一次（与重构前等价）
    history = factory.message_store.get(outcome.conversation_id)
    assert history is not None
    assert history.messages_json == ModelMessagesTypeAdapter.dump_json(
        outcome.messages
    )


# -- 失败路径：流中期与装配期 ------------------------------------------------


def test_run_sync_mid_stream_failure_publishes_error_chunks(
    tmp_path: Path,
) -> None:
    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("test-model", "不得泄露的 provider body")

    factory = _factory(tmp_path, {TEXT_PROFILE.use_case_id: FunctionModel(model)})
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 失败测试")

    with bind_presentation_context(scope):
        with pytest.raises(AiAgentExecutionError) as caught:
            factory.run_sync(
                profile=TEXT_PROFILE,
                instructions="直接回答。",
                user_prompt="触发失败。",
                toolset=EMPTY_TOOLSET,
            )
    assert caught.value.code == "ModelAPIError"

    registry.finish_request(
        presentation_id,
        request_failed=True,
        error_code=caught.value.code,
        error_message=str(caught.value),
    )
    payload = registry.status_payload(presentation_id)
    assert payload is not None
    assert payload["status"] == FAILED
    assert payload["had_agent_run"] is True
    assert payload["error_code"] == "ModelAPIError"

    # 流中期失败由官方 transform 转成 error/finish 帧，不丢不乱
    text = _drain_chunks(registry, presentation_id)
    frames = _parse_frames(text)
    errors = [frame for frame in frames if frame.get("type") == "error"]
    assert errors and errors[0].get("errorText"), "必须包含官方 error 帧"
    assert any(
        frame.get("type") == "finish" and frame.get("finishReason") == "error"
        for frame in frames
    )

    # 部分历史仍按重构前语义保存
    history = factory.message_store.get(caught.value.conversation_id)
    assert history is not None
    assert history.model_messages()


def test_run_sync_pre_stream_failure_publishes_error_chunks(
    tmp_path: Path,
) -> None:
    message_store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))

    def broken_binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        raise RuntimeError("binding unavailable")

    factory = AiAgentFactory(
        app_dir=tmp_path,
        app_config={},
        message_store=message_store,
        model_binding_factory=broken_binding,
    )
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 装配失败")

    with bind_presentation_context(scope):
        with pytest.raises(AiAgentExecutionError):
            factory.run_sync(
                profile=TEXT_PROFILE,
                instructions="直接回答。",
                user_prompt="不会进入模型。",
                toolset=EMPTY_TOOLSET,
            )

    registry.finish_request(presentation_id, request_failed=True)
    payload = registry.status_payload(presentation_id)
    assert payload is not None
    assert payload["status"] == FAILED
    # 装配期失败：Agent 从未启动
    assert payload["had_agent_run"] is False

    # observer 仍补发官方 error 帧，订阅者不会永久等待
    text = _drain_chunks(registry, presentation_id)
    frames = _parse_frames(text)
    errors = [frame for frame in frames if frame.get("type") == "error"]
    assert errors and errors[0].get("errorText")
    assert any(frame.get("type") == "finish" for frame in frames)


# -- root/child 派生与 contextvar 穿透 ---------------------------------------


def test_nested_run_sync_derives_child_and_tool_threads_see_context(
    tmp_path: Path,
) -> None:
    seen: dict[str, Any] = {}

    def child_model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        seen["child_context"] = current_presentation_context()
        return ModelResponse(parts=[TextPart("子回答")])

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        # 工具执行线程必须能看到 presentation context（§16 风险控制）
        seen["tool_context"] = current_presentation_context()
        child_outcome = factory.run_sync(
            profile=CHILD_PROFILE,
            instructions="回答子任务。",
            user_prompt="子任务。",
            toolset=CHILD_TOOLSET,
        )
        seen["child_outcome"] = child_outcome
        child_outcome.complete()
        return {"child_answer": str(child_outcome.output)}

    factory = _factory(
        tmp_path,
        {
            PARENT_PROFILE.use_case_id: FunctionModel(_parent_model),
            CHILD_PROFILE.use_case_id: FunctionModel(child_model),
        },
    )
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 父子运行")

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=PARENT_PROFILE,
            instructions="先调用子 Agent，再给出最终答案。",
            user_prompt="执行。",
            toolset=parent_toolset(executor),
        )
    assert outcome.output == Answer(answer="父完成")
    outcome.complete()
    registry.finish_request(presentation_id, request_failed=False)

    tool_context = seen["tool_context"]
    assert tool_context is not None
    assert tool_context.is_root_run
    assert tool_context.presentation_id == presentation_id
    assert tool_context.run_id == outcome.run_id
    assert tool_context.observer is scope.observer

    child_context = seen["child_context"]
    child_outcome = seen["child_outcome"]
    assert child_context is not None
    assert child_context.is_child_run
    assert child_context.presentation_id == presentation_id
    assert child_context.root_run_id == scope.root_run_id
    assert child_context.conversation_id == scope.conversation_id
    assert child_context.parent_run_id == outcome.run_id
    assert child_context.run_id == child_outcome.run_id
    assert child_context.observer is scope.observer

    # 父子历史分别保存，互不覆盖
    parent_history = factory.message_store.get(outcome.conversation_id)
    child_history = factory.message_store.get(child_outcome.conversation_id)
    assert parent_history is not None
    assert child_history is not None
    assert outcome.conversation_id != child_outcome.conversation_id

    payload = registry.status_payload(presentation_id)
    assert payload is not None
    assert payload["status"] == COMPLETED
    assert payload["had_agent_run"] is True


def test_child_run_lifecycle_via_recording_observer(tmp_path: Path) -> None:
    recorder = RecordingObserver()
    seen: dict[str, Any] = {}

    def child_model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("子回答")])

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        child_outcome = factory.run_sync(
            profile=CHILD_PROFILE,
            instructions="回答子任务。",
            user_prompt="子任务。",
            toolset=CHILD_TOOLSET,
        )
        seen["child_outcome"] = child_outcome
        child_outcome.complete()
        return {"child_answer": str(child_outcome.output)}

    factory = _factory(
        tmp_path,
        {
            PARENT_PROFILE.use_case_id: FunctionModel(_parent_model),
            CHILD_PROFILE.use_case_id: FunctionModel(child_model),
        },
    )
    scope = _recording_scope(recorder)

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=PARENT_PROFILE,
            instructions="先调用子 Agent，再给出最终答案。",
            user_prompt="执行。",
            toolset=parent_toolset(executor),
        )
    assert outcome.output == Answer(answer="父完成")
    outcome.complete()

    child_run_id = seen["child_outcome"].run_id
    # root run 生命周期只属于父运行
    assert recorder.run_started_calls == [
        (outcome.run_id, "", PARENT_PROFILE.use_case_id)
    ]
    assert recorder.running_calls and set(recorder.running_calls) == {
        outcome.run_id
    }
    assert recorder.finalizing_calls == [outcome.run_id]
    assert recorder.completed_calls == [outcome.run_id]
    assert recorder.failed_calls == []
    # 首期：只有 root run 的 native stream 被编码发布一次；子运行不建立
    # 第二条 assistant stream，只通过紧凑状态展示。
    assert recorder.observe_calls == 1
    assert recorder.child_status_calls == [
        (child_run_id, "running", CHILD_PROFILE.use_case_id),
        (child_run_id, "completed", CHILD_PROFILE.use_case_id),
    ]


def test_child_failure_is_reported_via_child_status_and_parent_continues(
    tmp_path: Path,
) -> None:
    recorder = RecordingObserver()
    seen: dict[str, Any] = {}

    def failing_child_model(
        _messages: list[Any],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise ModelAPIError("test-model", "子运行 provider 失败")

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        try:
            factory.run_sync(
                profile=CHILD_PROFILE,
                instructions="回答子任务。",
                user_prompt="子任务。",
                toolset=CHILD_TOOLSET,
            )
        except AiAgentExecutionError as error:
            seen["child_error"] = error
            return {"child_answer": ""}
        raise AssertionError("子运行应当失败")

    factory = _factory(
        tmp_path,
        {
            PARENT_PROFILE.use_case_id: FunctionModel(_parent_model),
            CHILD_PROFILE.use_case_id: FunctionModel(failing_child_model),
        },
    )
    scope = _recording_scope(recorder)

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=PARENT_PROFILE,
            instructions="先调用子 Agent，再给出最终答案。",
            user_prompt="执行。",
            toolset=parent_toolset(executor),
        )
    assert outcome.output == Answer(answer="父完成")
    outcome.complete()

    child_error = seen["child_error"]
    assert child_error.code == "ModelAPIError"
    child_run_id = child_error.run_id
    assert child_run_id
    # 子失败通过 child_status 报告，不冒泡为 root failed；父运行照常完成
    assert recorder.failed_calls == []
    assert recorder.completed_calls == [outcome.run_id]
    assert (child_run_id, "running", CHILD_PROFILE.use_case_id) in (
        recorder.child_status_calls
    )
    assert (child_run_id, "failed", CHILD_PROFILE.use_case_id) in (
        recorder.child_status_calls
    )
    assert recorder.observe_calls == 1


# -- observer 故障降级：业务语义与执行次数不受影响 ----------------------------


class _BrokenLifecycleObserver:
    """生命周期调用全部抛错；事件包装透传。"""

    def __init__(self) -> None:
        self.observe_calls = 0

    def _broken(self) -> None:
        raise RuntimeError("display unavailable")

    def claim_root_run(self, *, run_id: str) -> str:
        # claim 成功：保持展示关联，只有生命周期调用降级。
        return str(run_id or "")

    def run_started(self, **kwargs: Any) -> None:
        self._broken()

    def running(self, **kwargs: Any) -> None:
        self._broken()

    def tool_activity(self, **kwargs: Any) -> None:
        self._broken()

    def finalizing(self, **kwargs: Any) -> None:
        self._broken()

    def completed(self, **kwargs: Any) -> None:
        self._broken()

    def failed(self, **kwargs: Any) -> None:
        self._broken()

    def cancelled(self, **kwargs: Any) -> None:
        self._broken()

    def child_status(self, **kwargs: Any) -> None:
        self._broken()

    def observe_native_events(self, events):
        self.observe_calls += 1

        async def passthrough():
            async for event in events:
                yield event

        return passthrough()


class _BrokenWrapObserver(_BrokenLifecycleObserver):
    """事件包装本身抛错：内核必须降级为无展示运行。"""

    def run_started(self, **kwargs: Any) -> None:
        return None

    def observe_native_events(self, events):
        raise RuntimeError("wrap failed")


@pytest.mark.parametrize(
    "observer_type",
    [_BrokenLifecycleObserver, _BrokenWrapObserver],
)
def test_observer_failure_degrades_display_without_rerunning_agent(
    tmp_path: Path,
    observer_type: type,
) -> None:
    calls = 0

    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("业务照常完成")])

    factory = _factory(tmp_path, {TEXT_PROFILE.use_case_id: FunctionModel(model)})
    observer = observer_type()
    scope = root_presentation_context(
        presentation_id="presentation_" + "11" * 16,
        root_run_id="root_run_" + "22" * 16,
        conversation_id="conversation_" + "33" * 16,
        origin="business.ui",
        observer=observer,
    )

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=TEXT_PROFILE,
            instructions="直接回答。",
            user_prompt="打招呼。",
            toolset=EMPTY_TOOLSET,
        )

    assert outcome.output == "业务照常完成"
    assert calls == 1, "observer 故障不得让 Agent 重复执行"
    outcome.complete()
    history = factory.message_store.get(outcome.conversation_id)
    assert history is not None


# -- P1-a：实时 conversation 与持久化历史 ID 一致 ----------------------------


def test_root_run_persists_history_under_scope_conversation_id(
    tmp_path: Path,
) -> None:
    """root Agent 必须用前端预留的 conversation 保存历史（真实 store）。"""

    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("历史一致性")])

    factory = _factory(tmp_path, {TEXT_PROFILE.use_case_id: FunctionModel(model)})
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 历史一致性")

    with bind_presentation_context(scope):
        outcome = factory.run_sync(
            profile=TEXT_PROFILE,
            instructions="直接回答。",
            user_prompt="写入历史。",
            toolset=EMPTY_TOOLSET,
        )
    outcome.complete()
    registry.finish_request(presentation_id, request_failed=False)

    # 实时流 conversation 与 registry/presentation conversation 同一 ID。
    assert outcome.conversation_id == scope.conversation_id
    assert registry.conversation_id(presentation_id) == scope.conversation_id
    # 真实 PydanticMessageStore：任务结束后按 presentation conversation 可读。
    history = factory.message_store.get(scope.conversation_id)
    assert history is not None
    assert history.messages_json == ModelMessagesTypeAdapter.dump_json(
        outcome.messages
    )


# -- P1-b：同一请求顺序多 Agent，只允许一个根流 ------------------------------


def test_sequential_runs_share_single_root_second_becomes_child(
    tmp_path: Path,
) -> None:
    """root 结束后 contextvar 恢复 root scope，第二个顺序 Agent 只能作 child。"""

    seen: dict[str, Any] = {}

    def first_model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("第一个 root")])

    def second_model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        seen["second_context"] = current_presentation_context()
        return ModelResponse(parts=[TextPart("第二个 child")])

    factory = _factory(
        tmp_path,
        {
            TEXT_PROFILE.use_case_id: FunctionModel(first_model),
            CHILD_PROFILE.use_case_id: FunctionModel(second_model),
        },
    )
    registry = AiPresentationRegistry()
    presentation_id, scope = _reserve_and_claim(registry, title="AI 单根流")

    with bind_presentation_context(scope):
        first = factory.run_sync(
            profile=TEXT_PROFILE,
            instructions="回答。",
            user_prompt="一。",
            toolset=EMPTY_TOOLSET,
        )
        second = factory.run_sync(
            profile=CHILD_PROFILE,
            instructions="回答。",
            user_prompt="二。",
            toolset=CHILD_TOOLSET,
        )
    first.complete()
    second.complete()
    registry.finish_request(presentation_id, request_failed=False)

    # 第一个运行是 root，conversation 用 presentation 预留 ID。
    assert first.conversation_id == scope.conversation_id
    # 第二个运行必须作为 child：parent 指向第一个运行，不再成为第二个 root。
    second_context = seen["second_context"]
    assert second_context is not None
    assert second_context.is_child_run is True
    assert second_context.parent_run_id == first.attempt_id
    assert second_context.presentation_id == presentation_id
    assert second_context.observer is scope.observer
    # child 不占用 presentation conversation。
    assert second.conversation_id != scope.conversation_id

    # 一次前台交互最多一个根流：chunk 只包含第一个（root）运行的输出。
    text = _drain_chunks(registry, presentation_id)
    frames = _parse_frames(text)
    assert frames
    deltas = "".join(
        str(frame.get("delta", ""))
        for frame in frames
        if frame.get("type") == "text-delta"
    )
    assert deltas == "第一个 root"
    assert "第二个" not in text
