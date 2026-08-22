"""Deferred continuation：任务终结后自动把最终回复写回主对话。

覆盖计划 §4/§7 的关键不变量：
- continuation 复用相同 conversation、新 run_id、不合成新用户 prompt；
- 用官方 ``DeferredToolResults`` 闭合悬空 ``global_task_start`` 调用；
- 最终 history + link resolved + outbox 同事务 CAS 提交；
- CAS 冲突/版本漂移时不重复调用模型、不重复提交；
- provisional link 过期清理：无法对上 history 则 abandoned + 取消任务并
  释放 conversation；能对上则修复为 ready。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskStartControlRequest,
)
from erp_web.services.ai_agent_factory import AiAgentFactory
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.services.ai_conversation_event_bus import AiConversationEventBus
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.global_task_continuation_service import (
    GlobalTaskContinuationService,
)


CONVERSATION = "conversation_global_chat_" + "a" * 32
TOOL_CALL_ID = "call-cont-1"


class _RecordingBus(AiConversationEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, int, str]] = []

    def publish(self, conversation_id, batch):  # type: ignore[override]
        self.published.append(
            (conversation_id, batch.history_version, batch.kind)
        )
        return super().publish(conversation_id, batch)


def _continuation_model(
    final_text: str,
    calls: list[str],
    streamed_fragments: int = 0,
) -> FunctionModel:
    async def model(messages: list, agent_info: AgentInfo):
        calls.append("model-run")
        for index in range(streamed_fragments):
            yield f"流式片段 {index}。"
        yield final_text

    return FunctionModel(stream_function=model)


def _build_environment(
    tmp_path,
    final_text: str,
    streamed_fragments: int = 0,
    model_factory: Any = None,
) -> dict[str, Any]:
    """在隔离 AppContext 上组装 continuation；模型绑定替换为测试 FunctionModel。

    ``model_factory`` 提供一个自定义 ``FunctionModel``（报告 A-05 需要模型在
    continuation 中再次调用工具）；缺省使用只产出最终文本的
    ``_continuation_model``。
    """

    context = get_context()
    controller = global_task_facade.build_global_task_controller(context)
    model_calls: list[str] = []

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        model = (
            model_factory(model_calls)
            if model_factory is not None
            else _continuation_model(
                final_text, model_calls, streamed_fragments
            )
        )
        return PydanticModelBinding(
            model=model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    factory = AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=context.pydantic_messages,
        model_binding_factory=binding,
    )
    chat_service = GlobalAgentChatService(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=context.pydantic_messages,
        toolset=global_task_facade.build_global_chat_toolset(context),
        factory=factory,
    )
    bus = _RecordingBus()
    continuation = GlobalTaskContinuationService(
        chat_service=chat_service,
        task_store=context.global_tasks,
        deferred_links=context.deferred_task_links,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
        run_registry=AiChatRunRegistry(),
    )
    return {
        "context": context,
        "controller": controller,
        "continuation": continuation,
        "links": context.deferred_task_links,
        "message_store": context.pydantic_messages,
        "outbox": context.ai_event_outbox,
        "bus": bus,
        "task_store": context.global_tasks,
        "model_calls": model_calls,
    }


def _start_request() -> GlobalTaskStartControlRequest:
    return GlobalTaskStartControlRequest.model_validate(
        {
            "goal": "查询草稿",
            "platform": "mercadolibre",
            "steps": [
                {
                    "capability_name": "drafts_query",
                    "arguments": {"scope": "active", "view": "summary"},
                }
            ],
        }
    )


def _first_history(call_id: str) -> list:
    """首次 Deferred run 的官方 history：悬空的 global_task_start 调用。"""

    return [
        ModelRequest(parts=[UserPromptPart("帮我整理商品")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "global_task_start",
                    {"goal": "查询草稿"},
                    tool_call_id=call_id,
                )
            ],
            model_name="test-model",
            provider_name="test",
        ),
    ]


def _backdate_link_created_at(env: dict[str, Any], link_id: str) -> None:
    """把 link 的 created_at 回拨到 TTL 之外，模拟过期 provisional。"""

    db = env["context"].db
    with db._connect() as conn:
        conn.execute(
            "UPDATE pydantic_deferred_task_links SET created_at = ? "
            "WHERE link_id = ?",
            ("2020-01-01T00:00:00+00:00", link_id),
        )
        conn.commit()


def _seed_ready_task(env: dict[str, Any]) -> str:
    """创建一个已 ready 的任务并推进到 completed；返回 task_id。"""

    controller = env["controller"]
    links = env["links"]
    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id="run-initial",
        tool_call_id=TOOL_CALL_ID,
        message_id="message-initial",
    )
    links.commit_initial_deferred_history(
        CONVERSATION,
        _first_history(TOOL_CALL_ID),
        link_id=acceptance.link_id,
        request_run_id="run-initial",
        encoded_chunks=["data: {\"type\":\"finish\"}\n\n"],
    )
    task = controller.resume_task(acceptance.task_id)
    assert task.status == "completed"
    return acceptance.task_id


def test_continuation_writes_final_reply_and_resolves_link(tmp_path) -> None:
    env = _build_environment(tmp_path, "任务已完成，这是最终回复。")
    task_id = _seed_ready_task(env)
    link = env["links"].get_by_task(task_id)
    assert link is not None
    assert link.link_status == "ready"
    frozen_version = link.history_version

    resolved = env["continuation"].recover_pending()

    assert resolved == 1
    # link resolved，且 continuation_run_id 与原 request_run 不同。
    updated = env["links"].get_by_task(task_id)
    assert updated is not None
    assert updated.link_status == "resolved"
    assert updated.resolved_at != ""
    assert updated.continuation_run_id != ""
    assert updated.continuation_run_id != "run-initial"

    # 最终 history：原始 user prompt 保留、tool call 被官方结果闭合、
    # 追加了最终 Assistant 文本；没有合成新的 UserPromptPart。
    history = env["message_store"].get(CONVERSATION)
    assert history is not None
    messages = history.model_messages()
    user_prompts = [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert user_prompts == ["帮我整理商品"]
    returned_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert TOOL_CALL_ID in returned_ids
    final_texts = [
        part.content
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert "任务已完成，这是最终回复。" in final_texts

    # history version 在冻结版本上 +1。
    assert history.history_version == frozen_version + 1

    # outbox 记录了 continuation 批次，并广播给订阅者。
    batches = env["outbox"].list_after(
        CONVERSATION,
        after_history_version=frozen_version,
    )
    assert [b.kind for b in batches] == ["continuation"]
    assert env["bus"].published == [
        (CONVERSATION, frozen_version + 1, "continuation")
    ]


def test_continuation_rejects_restart_global_task_with_model_visible_error(
    tmp_path,
) -> None:
    """报告 A-05：continuation 再次调用 global_task_start 必须稳定拒绝。

    旧实现中 continuation 只注入 conversation_id，缺失 message_id，再次调用
    global_task_start 会命中 ``TOOL_IDEMPOTENCY_CONTEXT_REQUIRED``——该错误不
    是模型可见的，Bridge 直接抛出并终止 continuation。修复后 Runtime 依据
    continuation 标记（active link）在任何副作用之前返回稳定、模型可见的
    拒绝，模型据此继续生成最终回复：不创建第二个任务，也不形成模型重试环。
    """

    final_text = "任务已完成，这是最终回复。"

    def restart_model_factory(calls: list[str]) -> FunctionModel:
        async def model(messages: list, agent_info: AgentInfo):
            calls.append("model-run")
            if len(calls) == 1:
                # 第一次 continuation 调用：模型错误地再次发起 global_task_start。
                yield {
                    0: DeltaToolCall(
                        name="global_task_start",
                        json_args=json.dumps(
                            _start_request().model_dump(),
                            ensure_ascii=False,
                        ),
                        tool_call_id="call-cont-restart",
                    )
                }
                return
            # 收到稳定拒绝后：模型继续生成最终回复。
            yield final_text

        return FunctionModel(stream_function=model)

    env = _build_environment(
        tmp_path, final_text, model_factory=restart_model_factory
    )
    task_id = _seed_ready_task(env)

    resolved = env["continuation"].recover_pending()

    # continuation 正常收敛：没有因工具错误中断。
    assert resolved == 1
    updated = env["links"].get_by_task(task_id)
    assert updated is not None
    assert updated.link_status == "resolved"

    # 模型共被调用两次（一次发起被拒的工具调用，一次产出最终回复），
    # 没有形成重试环。
    assert env["model_calls"] == ["model-run", "model-run"]

    # 最终 history 含拒绝 ToolReturn 与最终文本；没有合成新 UserPrompt。
    history = env["message_store"].get(CONVERSATION)
    assert history is not None
    messages = history.model_messages()
    returned = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    restart_returns = [
        part for part in returned if part.tool_call_id == "call-cont-restart"
    ]
    assert len(restart_returns) == 1
    assert "GLOBAL_TASK_DEFERRED_ALREADY_ACTIVE" in str(
        restart_returns[0].content
    )
    final_texts = [
        part.content
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert final_text in final_texts

    # 拒绝发生在 Runtime 副作用之前：没有创建第二个任务。
    with env["context"].db._connect() as conn:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM global_tasks"
        ).fetchone()[0]
    assert task_count == 1

    # conversation 已释放：不再有 active link。
    assert env["links"].has_active(CONVERSATION) is False


def test_bounded_task_result_carries_successful_step_business_results(
    tmp_path,
) -> None:
    """报告 A-06（单元）：成功步骤的业务结果必须进入 continuation 载荷。

    载荷必须包含 completed 步骤的 ``result``（能力输出），供模型生成包含实际
    结果的最终回复；单个超大结果被替换为截断标记而不是让整体超限；极值下仍
    不超过上限。
    """

    import json

    from erp_web.schemas.global_tasks import LocalGlobalTaskState
    from erp_web.services.global_task_continuation_service import (
        MAX_CONTINUATION_RESULT_BYTES,
        GlobalTaskContinuationService,
    )

    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)
    task = env["task_store"].load_task(task_id)
    assert task is not None
    assert task.steps and task.steps[0].status == "completed"
    business_result = dict(task.steps[0].result or {})

    # 正常完成任务：结果载荷携带业务结果。
    payload = GlobalTaskContinuationService._bounded_task_result(task)
    assert payload["steps"][0]["result"] == business_result
    assert "truncated" not in payload

    # 单个超大结果：被替换为截断标记，整体不超限。
    step_dump = task.steps[0].model_dump(mode="json")
    huge_step = {
        **step_dump,
        "status": "completed",
        "result": {"blob": "大" * 20000},
    }
    extreme = LocalGlobalTaskState.model_validate(
        {
            **task.model_dump(mode="json"),
            "status": "completed",
            "steps": [dict(huge_step)],
        }
    )
    bounded = GlobalTaskContinuationService._bounded_task_result(extreme)
    serialized = json.dumps(bounded, ensure_ascii=False).encode("utf-8")
    assert len(serialized) <= MAX_CONTINUATION_RESULT_BYTES
    assert bounded["steps"][0]["result"] == {"truncated": True}


def test_bounded_task_result_terminates_on_extreme_task_level_fields(
    tmp_path,
) -> None:
    """报告 A-06：结果收缩后任务级长字段仍超限时，收缩必须在确定时间内终止。

    合法极值数据（goal 4000 / assistant_message 4000 / error_message 2000，
    一个 completed step.result 与一个 failed step）在旧实现中让第一级循环原地
    重复替换同一截断标记而永不进入后续阶段（SIGALRM 探针：non-terminating
    shrink），永久阻塞 recovery worker。修复后每个 result 最多收缩一次，循环
    必然推进到去错误详情/截断文本/去步骤阶段并在上限内返回。
    """

    import json
    import signal

    from erp_web.schemas.global_tasks import LocalGlobalTaskState
    from erp_web.services.global_task_continuation_service import (
        MAX_CONTINUATION_RESULT_BYTES,
        GlobalTaskContinuationService,
    )

    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)
    task = env["task_store"].load_task(task_id)
    assert task is not None
    completed_step = task.steps[0].model_dump(mode="json")
    completed_step["status"] = "completed"
    completed_step["result"] = {"summary": "x" * 3000}
    failed_step = {
        **completed_step,
        "status": "failed",
        "result": None,
        "error": {"code": "FAKE_FAIL", "message": "y" * 900, "retryable": False},
    }
    extreme = LocalGlobalTaskState.model_validate(
        {
            **task.model_dump(mode="json"),
            "status": "failed",
            "goal": "目" * 4000,
            "assistant_message": "回" * 4000,
            "error_code": "GLOBAL_TASK_FAILED",
            "error_message": "错" * 2000,
            "steps": [completed_step, failed_step],
        }
    )

    # SIGALRM 探针：任何合法输入都必须在确定时间内返回。
    def timeout_handler(signum, frame):
        del signum, frame
        raise TimeoutError("non-terminating shrink")

    previous = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        bounded = GlobalTaskContinuationService._bounded_task_result(extreme)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    serialized = json.dumps(bounded, ensure_ascii=False).encode("utf-8")
    assert len(serialized) <= MAX_CONTINUATION_RESULT_BYTES
    assert bounded["task_id"] == extreme.task_id
    assert bounded["status"] == "failed"
    assert bounded.get("truncated") is True


def test_continuation_model_receives_successful_step_result(tmp_path) -> None:
    """报告 A-06（纵向）：无需第二条用户消息，最终回复可基于任务结果生成。

    continuation 模型的入参 ToolReturn 必须携带成功步骤的业务结果（此处为
    drafts_query 的 snapshot_id/total），证明 DeferredToolResults 没有丢失能力
    输出；模型据此产出最终回复并写回主对话。
    """

    final_text = "任务已完成，这是最终回复。"
    seen_tool_returns: list[str] = []

    def capturing_model_factory(calls: list[str]) -> FunctionModel:
        async def model(messages: list, agent_info: AgentInfo):
            calls.append("model-run")
            for message in messages:
                if not isinstance(message, ModelRequest):
                    continue
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        seen_tool_returns.append(str(part.content))
            yield final_text

        return FunctionModel(stream_function=model)

    env = _build_environment(
        tmp_path, final_text, model_factory=capturing_model_factory
    )
    task_id = _seed_ready_task(env)
    task = env["task_store"].load_task(task_id)
    assert task is not None
    business_result = dict(task.steps[0].result or {})
    # drafts_query 的业务结果至少包含快照标识，作为可辨识的业务输出。
    assert "snapshot_id" in business_result

    resolved = env["continuation"].recover_pending()

    assert resolved == 1
    # continuation 模型收到的 ToolReturn 携带业务结果（snapshot_id 可辨识）。
    assert any(
        str(business_result.get("snapshot_id")) in tool_return
        for tool_return in seen_tool_returns
    ), seen_tool_returns

    # 最终回复写回主对话。
    history = env["message_store"].get(CONVERSATION)
    assert history is not None
    final_texts = [
        part.content
        for message in history.model_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert final_text in final_texts


def test_large_streaming_continuation_commits_bounded_terminal_segment(
    tmp_path,
) -> None:
    """线上缺陷 L-01 回归（continuation 侧）。

    最终回复按 delta 逐条编码时 chunk 数同样可以超过旧 512 条 outbox 防线；
    continuation 提交必须成功，且 outbox 只持久化有界终态段。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import (
        MAX_OUTBOX_EVENT_CHUNKS,
        MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS,
    )

    # 600 条流式片段 + 终态文本：编码 chunk 数必然远超旧 512 条防线。
    env = _build_environment(
        tmp_path, "任务已完成。", streamed_fragments=MAX_OUTBOX_EVENT_CHUNKS + 88
    )
    task_id = _seed_ready_task(env)

    assert env["continuation"].recover_pending() == 1
    assert env["model_calls"] == ["model-run"]

    updated = env["links"].get_by_task(task_id)
    assert updated is not None
    assert updated.link_status == "resolved"

    history = env["message_store"].get(CONVERSATION)
    assert history is not None
    final_texts = [
        part.content
        for message in history.model_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert any("任务已完成。" in text for text in final_texts)

    continuation_batches = [
        batch
        for batch in env["outbox"].list_after(
            CONVERSATION, after_history_version=0
        )
        if batch.kind == "continuation"
    ]
    assert len(continuation_batches) == 1
    # 批次只持久化有界终态段，不再是整条 delta 流。
    assert len(continuation_batches[0].events) <= (
        MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS
    )
    assert any(
        '"type":"finish"' in event
        for event in continuation_batches[0].events
    )


def test_continuation_outbox_too_large_degrades_to_resync_only_batch(
    tmp_path,
) -> None:
    """报告 R-03：最终回复稳定超出 outbox 字节上限时确定性降级。

    旧实现把超限异常释放为普通失败（lease 释放、link 保持 ready），下一轮
    recovery 重新调用模型；模型稳定生成同样的超限终态段时会无限重试并持续
    锁定 conversation。修复后降级为 resync-only 批次（空事件列表恒满足上
    限）：history 仍 CAS 提交、link resolved，连续多轮 recovery 不重复调用
    模型。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import (
        MAX_OUTBOX_EVENT_CHUNK_BYTES,
    )

    # 单条 text-delta chunk 即超过 64 KiB 单条上限：终态段必然 TOO_LARGE。
    huge_text = "x" * (MAX_OUTBOX_EVENT_CHUNK_BYTES + 4096)
    env = _build_environment(tmp_path, huge_text)
    task_id = _seed_ready_task(env)

    assert env["continuation"].recover_pending() == 1
    assert env["model_calls"] == ["model-run"]

    updated = env["links"].get_by_task(task_id)
    assert updated is not None
    assert updated.link_status == "resolved"

    # history 仍按 CAS 提交：最终回复内容可从 /ui-messages 恢复。
    history = env["message_store"].get(CONVERSATION)
    assert history is not None
    final_texts = [
        part.content
        for message in history.model_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert any(huge_text in text for text in final_texts)

    continuation_batches = [
        batch
        for batch in env["outbox"].list_after(
            CONVERSATION, after_history_version=0
        )
        if batch.kind == "continuation"
    ]
    assert len(continuation_batches) == 1
    # resync-only 批次：空事件列表，订阅端推进游标并重读 /ui-messages。
    assert list(continuation_batches[0].events) == []

    # 连续多轮 recovery：link 已 resolved，不再调用模型。
    assert env["continuation"].recover_pending() == 0
    assert env["continuation"].recover_pending() == 0
    assert env["model_calls"] == ["model-run"]


def test_continuation_commit_success_survives_publish_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 R-05：continuation durable commit 之后的通知异常必须被隔离。

    publish 抛异常：本轮恢复不得记为失败（不重复调用模型），不得把已成功
    resolved 的 link 误记为 CONTINUATION_RECOVERY_FAILED；批次保留未发布，
    由后台 outbox publisher 重投。
    """

    env = _build_environment(tmp_path, "任务已完成。")
    task_id = _seed_ready_task(env)

    def failing_publish(conversation_id: str, batch: Any) -> None:
        raise RuntimeError("注入的事件总线故障。")

    monkeypatch.setattr(env["bus"], "publish", failing_publish)

    # 通知失败不改变恢复结果：成功提交数量仍为 1。
    assert env["continuation"].recover_pending() == 1
    assert env["model_calls"] == ["model-run"]

    updated = env["links"].get_by_task(task_id)
    assert updated is not None
    assert updated.link_status == "resolved"
    # resolved link 不得被通知异常污染为 CONTINUATION_RECOVERY_FAILED。
    assert updated.last_error_code == ""

    continuation_batches = [
        batch
        for batch in env["outbox"].list_after(
            CONVERSATION, after_history_version=0
        )
        if batch.kind == "continuation"
    ]
    assert len(continuation_batches) == 1
    # 未记账 published_at：后台 outbox publisher 可重投。
    assert continuation_batches[0].published_at == ""

    # 再次恢复：无重复模型调用，无重复提交。
    assert env["continuation"].recover_pending() == 0
    assert env["model_calls"] == ["model-run"]


def test_continuation_is_idempotent_and_single_commit(tmp_path) -> None:
    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)

    assert env["continuation"].recover_pending() == 1
    first = env["links"].get_by_task(task_id)
    version_after_first = env["message_store"].get(CONVERSATION).history_version

    # 再次恢复不应重复提交或改动历史。
    assert env["continuation"].recover_pending() == 0
    second = env["links"].get_by_task(task_id)
    assert second.link_status == "resolved"
    assert second.continuation_run_id == first.continuation_run_id
    assert env["message_store"].get(CONVERSATION).history_version == (
        version_after_first
    )


def test_continuation_skips_when_history_version_drifted(tmp_path) -> None:
    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)
    # 模拟 history 在 link ready 之后被其他提交推进（版本漂移）。
    env["message_store"].save(
        CONVERSATION,
        [ModelRequest(parts=[UserPromptPart("另一轮")])],
    )

    assert env["continuation"].recover_pending() == 0
    link = env["links"].get_by_task(task_id)
    assert link.link_status == "ready"
    assert link.last_error_code == "CONTINUATION_HISTORY_MISMATCH"


def test_provisional_sweep_abandons_and_releases_conversation(tmp_path) -> None:
    env = _build_environment(tmp_path, "最终回复。")
    controller = env["controller"]
    links = env["links"]

    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id="run-x",
        tool_call_id="call-x",
    )
    assert links.has_active(CONVERSATION) is True
    _backdate_link_created_at(env, acceptance.link_id)
    # 不提交首次 history，直接 sweep：无法对上 history → abandoned + 取消任务。
    handled = env["continuation"].sweep_provisional_links(ttl_seconds=1)

    assert handled == 1
    abandoned = links.get(acceptance.link_id)
    assert abandoned is not None
    assert abandoned.link_status == "abandoned"
    task = env["task_store"].load_task(acceptance.task_id)
    assert task.status == "cancelled"
    # conversation 被释放。
    assert links.has_active(CONVERSATION) is False


def test_two_workers_race_single_continuation_commit(tmp_path) -> None:
    """两个恢复者竞争同一 link：claim 原子，最终只有一次 continuation 提交。"""

    import threading

    env = _build_environment(tmp_path, "竞争后的最终回复。")
    task_id = _seed_ready_task(env)

    # 第二个恢复者共享同一套 store/总线，但独立的 run_registry。
    second = GlobalTaskContinuationService(
        chat_service=env["continuation"].chat_service,
        task_store=env["task_store"],
        deferred_links=env["links"],
        message_store=env["message_store"],
        event_outbox=env["outbox"],
        event_bus=env["bus"],
        run_registry=AiChatRunRegistry(),
    )

    results: list[int] = []

    def worker(service) -> None:
        results.append(service.recover_pending())

    threads = [
        threading.Thread(target=worker, args=(env["continuation"],)),
        threading.Thread(target=worker, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    # 只允许一个恢复者成功提交。
    assert sorted(results) == [0, 1]
    link = env["links"].get_by_task(task_id)
    assert link.link_status == "resolved"
    # outbox 只有一条 continuation 批次，历史只前进一次。
    batches = env["outbox"].list_after(CONVERSATION, after_history_version=0)
    assert [b.kind for b in batches] == ["deferred_handshake", "continuation"]


def test_provisional_sweep_repairs_link_with_matching_history(tmp_path) -> None:
    env = _build_environment(tmp_path, "最终回复。")
    controller = env["controller"]
    links = env["links"]
    message_store = env["message_store"]

    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id="run-y",
        tool_call_id="call-y",
    )
    # 模拟崩溃前 captured history 已落盘（含悬空 tool call），但 link 仍 provisional。
    saved = message_store.save(
        CONVERSATION,
        _first_history("call-y"),
    )
    _backdate_link_created_at(env, acceptance.link_id)

    handled = env["continuation"].sweep_provisional_links(ttl_seconds=1)

    assert handled == 1
    repaired = links.get(acceptance.link_id)
    assert repaired is not None
    assert repaired.link_status == "ready"
    assert repaired.history_version == saved.history_version
    # 任务未被取消，等待 worker 执行。
    task = env["task_store"].load_task(acceptance.task_id)
    assert task.status == "running"


def _seed_ready_link_only(env: dict[str, Any], run_id: str, call_id: str) -> str:
    """创建 ready link 但不执行任务；返回 task_id（任务仍 running）。"""

    acceptance = env["controller"].accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id=run_id,
        tool_call_id=call_id,
    )
    env["links"].commit_initial_deferred_history(
        CONVERSATION,
        _first_history(call_id),
        link_id=acceptance.link_id,
        request_run_id=run_id,
        encoded_chunks=[],
    )
    return acceptance.task_id


def test_continuation_explains_failed_and_cancelled_terminals(tmp_path) -> None:
    """报告 §8-4：failed/cancelled 终态经 continuation 生成可解释回复。"""

    env = _build_environment(tmp_path, "很抱歉，任务未能完成，详情见任务卡。")
    controller = env["controller"]
    links = env["links"]
    task_id = _seed_ready_link_only(env, "run-f", "call-f")

    # 注入 failed 终态（步骤失败、任务级错误可读）。
    task = env["task_store"].load_task(task_id)
    failed = task.model_copy(
        update={
            "status": "failed",
            "error_code": "GLOBAL_TASK_STEP_FAILED",
            "error_message": "步骤执行失败：外部系统拒绝。",
            "assistant_message": "任务在第 1 步失败。",
        }
    )
    env["task_store"].save_task(failed)

    assert env["continuation"].recover_pending() == 1
    assert env["model_calls"] == ["model-run"]

    history = env["message_store"].get(CONVERSATION)
    messages = history.model_messages()
    # 悬空调用被官方 DeferredToolResults 闭合，结果载荷携带 failed 终态。
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-f"
    ]
    assert returns
    returned_text = str(returns[0].content)
    assert "failed" in returned_text
    assert "GLOBAL_TASK_STEP_FAILED" in returned_text
    # 最终 Assistant 回复已提交进主对话。
    final_texts = [
        part.content
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert "很抱歉，任务未能完成，详情见任务卡。" in final_texts
    link = env["links"].get_by_task(task_id)
    assert link.link_status == "resolved"

    # cancelled 终态同样生成可解释回复（独立 conversation）。
    cancelled_conversation = "conversation_global_chat_" + "b" * 32
    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=cancelled_conversation,
        request_run_id="run-c",
        tool_call_id="call-c",
    )
    links.commit_initial_deferred_history(
        cancelled_conversation,
        _first_history("call-c"),
        link_id=acceptance.link_id,
        request_run_id="run-c",
        encoded_chunks=[],
    )
    cancelled_task = env["task_store"].load_task(acceptance.task_id)
    env["task_store"].save_task(
        cancelled_task.model_copy(
            update={
                "status": "cancelled",
                "assistant_message": "任务已取消，未再执行后续步骤。",
            }
        )
    )

    assert env["continuation"].recover_pending() == 1
    cancelled_history = env["message_store"].get(cancelled_conversation)
    cancelled_returns = [
        str(part.content)
        for message in cancelled_history.model_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_call_id == "call-c"
    ]
    assert cancelled_returns
    assert "cancelled" in cancelled_returns[0]
    assert env["links"].get_by_task(acceptance.task_id).link_status == "resolved"


def test_outbox_publisher_replays_unpublished_batches_without_model_rerun(
    tmp_path,
) -> None:
    """报告 §8-5：提交成功、广播前崩溃，恢复后只重放 outbox，不再调用模型。"""

    from erp_web.services.ai_conversation_outbox_publisher import (
        AiConversationOutboxPublisher,
    )

    env = _build_environment(tmp_path, "最终回复。")
    _seed_ready_task(env)

    assert env["continuation"].recover_pending() == 1
    calls_after_commit = list(env["model_calls"])
    assert calls_after_commit  # continuation 调用过一次模型

    # 模拟进程在提交成功后、投递记账前退出：published_at 全部清零。
    db = env["context"].db
    with db._connect() as conn:
        conn.execute("UPDATE pydantic_ai_event_outbox SET published_at = ''")
        conn.commit()

    publisher = AiConversationOutboxPublisher(
        event_outbox=env["outbox"],
        event_bus=env["bus"],
    )
    assert publisher.publish_pending() == 2  # handshake + continuation 批次
    assert env["outbox"].list_unpublished() == []
    # 重放覆盖全部已提交批次；订阅端按 history_version 去重，不产生新事实。
    all_versions = sorted(
        batch.history_version
        for batch in env["outbox"].list_after(CONVERSATION, after_history_version=0)
    )
    replayed = env["bus"].published[-2:]
    assert sorted(version for _, version, _ in replayed) == all_versions
    # 第二轮无可重投；continuation 不再运行，模型不再被调用。
    assert publisher.publish_pending() == 0
    assert env["continuation"].recover_pending() == 0
    assert env["model_calls"] == calls_after_commit


def test_outbox_retention_prunes_only_published_beyond_window(tmp_path) -> None:
    """报告 A-14：保留窗口只清理已发布批次，未发布批次保留给后台重投。

    每个 conversation 保留最近 ``keep_latest`` 条已发布批次；更早的已发布批次
    被删除，未发布批次（等待重投）绝不受影响。这让「游标早于最早保留版本则
    resync」形成真实可测的边界，同时约束 outbox 无限增长。
    """

    env = _build_environment(tmp_path, "最终回复。")
    db = env["context"].db
    published_at = "2026-08-21T00:00:00+00:00"
    # 版本 1-5 为已发布批次，版本 6 为未发布批次（等待后台重投）。
    with db._connect() as conn:
        for version in range(1, 7):
            conn.execute(
                "INSERT INTO pydantic_ai_event_outbox "
                "(conversation_id, run_id, history_version, kind, "
                " events_json, created_at, published_at) "
                "VALUES (?, ?, ?, 'continuation', '[]', ?, ?)",
                (
                    CONVERSATION,
                    f"run-{version}",
                    version,
                    published_at,
                    "" if version == 6 else published_at,
                ),
            )
        conn.commit()

    outbox = env["outbox"]
    # keep_latest=2：保留最近的已发布批次 v4、v5，删除 v1-v3；v6 未发布不受影响。
    pruned = outbox.prune_published(keep_latest=2)
    assert pruned == 3

    remaining = outbox.list_after(CONVERSATION, after_history_version=0)
    assert sorted(batch.history_version for batch in remaining) == [4, 5, 6]
    # 未发布批次仍然可被后台 publisher 重投。
    assert [batch.history_version for batch in outbox.list_unpublished()] == [6]

    # 再次清理幂等：已无超出窗口的已发布批次。
    assert outbox.prune_published(keep_latest=2) == 0


def test_active_lease_blocks_parallel_continuation_model_run(tmp_path) -> None:
    """报告 §8-6：租约存续期间第二个 worker 不能领取，也就不会并行调用模型。"""

    from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
    from erp_web.stores.pydantic_deferred_task_link_store import (
        DEFAULT_CONTINUATION_LEASE_SECONDS,
    )

    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)
    link = env["links"].get_by_task(task_id)

    # 租约默认窗口必须覆盖 global.chat 模型运行超时。
    assert DEFAULT_CONTINUATION_LEASE_SECONDS > GLOBAL_CHAT_PROFILE.timeout_seconds

    # 另一个 worker 已持有租约（模拟模型运行超过 120 秒仍未结束）。
    held = env["links"].claim(link.link_id)
    assert held is not None
    _, lease_id = held

    assert env["continuation"].recover_pending() == 0
    assert env["model_calls"] == []  # 没有并行模型调用
    current = env["links"].get_by_task(task_id)
    assert current.link_status == "ready"

    # 租约释放后恢复正常 continuation。
    assert env["links"].release_claim(link.link_id, lease_id) is True
    assert env["continuation"].recover_pending() == 1
    assert env["model_calls"] == ["model-run"]
    assert env["links"].get_by_task(task_id).link_status == "resolved"


def test_send_lock_does_not_block_explicit_task_commands(tmp_path) -> None:
    """报告 §8-9：活动 link 的发送锁定不影响补资料/取消等明确命令。"""

    from erp_web.schemas.global_tasks import (
        GlobalTaskIdRequest,
        GlobalTaskInputRequest,
        LocalGlobalTaskState,
    )

    env = _build_environment(tmp_path, "最终回复。")
    controller = env["controller"]
    links = env["links"]

    # running（当前步骤未运行）：发送锁定期间仍可明确取消。
    running_task_id = _seed_ready_link_only(env, "run-lock-1", "call-lock-1")
    assert links.has_active(CONVERSATION) is True
    cancelled = controller.cancel_task(
        GlobalTaskIdRequest(task_id=running_task_id)
    )
    assert cancelled.task.status == "cancelled"

    # needs_input：发送锁定期间仍可提交补充资料或取消（独立 conversation）。
    input_conversation = "conversation_global_chat_" + "c" * 32
    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=input_conversation,
        request_run_id="run-lock-2",
        tool_call_id="call-lock-2",
    )
    links.commit_initial_deferred_history(
        input_conversation,
        _first_history("call-lock-2"),
        link_id=acceptance.link_id,
        request_run_id="run-lock-2",
        encoded_chunks=[],
    )
    assert links.has_active(input_conversation) is True

    task = env["task_store"].load_task(acceptance.task_id)
    task_dump = task.model_dump(mode="json")
    step_dump = task_dump["steps"][task.current_step_index]
    needs_input_task = LocalGlobalTaskState.model_validate(
        {
            **task_dump,
            "status": "needs_input",
            "steps": [
                {**step_dump, "status": "needs_input"},
                *task_dump["steps"][task.current_step_index + 1:],
            ],
            "pending_inputs": [
                {"key": "scope", "label": "草稿范围", "reason": "请确认范围。"}
            ],
        }
    )
    env["task_store"].save_task(needs_input_task)

    submitted = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=acceptance.task_id,
            arguments={"scope": "active"},
        )
    )
    assert submitted.task.status == "running"
    assert submitted.task.pending_inputs == []
    # 明确命令完成后 link 仍活动：普通用户回合继续被发送锁定拒绝，
    # 直到任务终结并由 continuation 解决（见握手 E2E 测试的 409 断言）。
    assert links.has_active(input_conversation) is True

    # 同一 needs_input→running 任务也可被明确取消。
    final = controller.cancel_task(
        GlobalTaskIdRequest(task_id=acceptance.task_id)
    )
    assert final.task.status == "cancelled"


def test_bounded_task_result_reduction_respects_byte_cap(tmp_path) -> None:
    """报告 §8-11（continuation 侧）：极值输入下结果载荷仍不超上限。"""

    import json

    from erp_web.schemas.global_tasks import LocalGlobalTaskState
    from erp_web.services.global_task_continuation_service import (
        MAX_CONTINUATION_RESULT_BYTES,
        GlobalTaskContinuationService,
    )

    env = _build_environment(tmp_path, "最终回复。")
    task_id = _seed_ready_task(env)
    task = env["task_store"].load_task(task_id)

    # 极值：超长文本字段 + 大量带错误详情的步骤（字段各自不超过 schema 上限）。
    step_dump = task.steps[0].model_dump(mode="json")
    failed_step_dump = {
        **step_dump,
        "status": "failed",
        "error": {"code": "STEP_FAILED", "message": "错" * 900},
    }
    extreme = LocalGlobalTaskState.model_validate(
        {
            **task.model_dump(mode="json"),
            "status": "failed",
            "goal": "目" * 4000,
            "assistant_message": "述" * 4000,
            "error_message": "误" * 1900,
            "steps": [dict(failed_step_dump) for _ in range(12)],
        }
    )

    payload = GlobalTaskContinuationService._bounded_task_result(extreme)
    serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert len(serialized) <= MAX_CONTINUATION_RESULT_BYTES
    # 收缩必须保留投递键与终态。
    assert payload["task_id"] == task.task_id
    assert payload["status"] == "failed"

    # 正常任务不需要截断。
    normal = GlobalTaskContinuationService._bounded_task_result(task)
    assert "truncated" not in normal
    assert normal["steps"]
