"""只读 API：conversation→task link 与后台事件订阅（SSE）。

覆盖计划 §5：
- task-link 只返回 ready link 的任务；awaiting_history 不向前端宣告；
- 任务终结并 continuation 提交后（resolved）task-link 回到空任务；
- 事件订阅按 after_history_version 重放 outbox 并转 live；
- 快照版本超出已重放范围时明确 resync_required，不静默从 live 开始。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskStartControlRequest,
)
from erp_web.schemas.ai_tools import PUBLISH_JOB_TYPE
from erp_web.schemas.global_tasks import (
    LocalGlobalTaskState,
    LocalTaskStep,
    TaskActiveJob,
)
from erp_web.services.ai_conversation_event_bus import (
    SUBSCRIPTION_QUEUE_MAXSIZE,
    AiConversationEventBus,
    ConversationResyncRequired,
)
from erp_web.services.ai_conversation_event_stream import ConversationEventStream
from erp_web.stores.pydantic_ai_event_outbox_store import OutboxEventBatch


CONVERSATION = "conversation_global_chat_" + "c" * 32
TOOL_CALL_ID = "call-ro-1"


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


def _accept_task() -> str:
    """受理任务并提交首次 Deferred history；返回 task_id。"""

    context = get_context()
    controller = global_task_facade.build_global_task_controller(context)
    acceptance = controller.accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id="run-ro",
        tool_call_id=TOOL_CALL_ID,
    )
    context.deferred_task_links.commit_initial_deferred_history(
        CONVERSATION,
        [
            ModelRequest(parts=[UserPromptPart("帮我查询")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "global_task_start",
                        {"goal": "查询草稿"},
                        tool_call_id=TOOL_CALL_ID,
                    )
                ],
                model_name="test-model",
                provider_name="test",
            ),
        ],
        link_id=acceptance.link_id,
        request_run_id="run-ro",
        encoded_chunks=["data: {\"type\":\"finish\"}\n\n"],
    )
    return acceptance.task_id


def test_task_link_returns_ready_task() -> None:
    task_id = _accept_task()

    payload, status = global_task_facade.conversation_task_link_payload(
        CONVERSATION
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["task_id"] == task_id
    assert payload["link_status"] == "ready"
    # 报告 A-15：task-link 只返回最小公开状态，不内嵌完整任务；任务详情由
    # 规范 Task GET 单一 owner 提供。
    assert payload["task"] is None


def test_task_link_hides_provisional_link() -> None:
    context = get_context()
    controller = global_task_facade.build_global_task_controller(context)
    controller.accept_deferred_task(
        _start_request(),
        conversation_id=CONVERSATION,
        request_run_id="run-p",
        tool_call_id="call-p",
    )
    # 不提交首次 history：link 仍是 awaiting_history，前端不可见。
    payload, status = global_task_facade.conversation_task_link_payload(
        CONVERSATION
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["task"] is None
    assert payload["task_id"] == ""


def test_task_link_empty_after_resolved() -> None:
    task_id = _accept_task()
    context = get_context()
    controller = global_task_facade.build_global_task_controller(context)
    controller.resume_task(task_id)
    link = context.deferred_task_links.get_by_task(task_id)
    # 直接把 link 标记为 resolved，模拟 continuation 已提交。
    with context.db._connect() as conn:
        conn.execute(
            "UPDATE pydantic_deferred_task_links SET link_status='resolved', "
            "resolved_at='2026-08-20T00:00:00+00:00' WHERE link_id=?",
            (link.link_id,),
        )
        conn.commit()

    payload, status = global_task_facade.conversation_task_link_payload(
        CONVERSATION
    )
    assert status == 200
    assert payload["task"] is None


def test_task_link_rejects_empty_conversation() -> None:
    payload, status = global_task_facade.conversation_task_link_payload("  ")
    assert status == 400
    assert payload["ok"] is False


def _make_stream(
    *,
    after_history_version: int,
    message_store,
    event_outbox,
    event_bus,
) -> ConversationEventStream:
    return ConversationEventStream(
        conversation_id=CONVERSATION,
        after_history_version=after_history_version,
        message_store=message_store,
        event_outbox=event_outbox,
        event_bus=event_bus,
    )


def test_event_stream_replays_outbox_batches() -> None:
    _accept_task()
    context = get_context()
    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=0,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []

    async def drain() -> None:
        # 只收集重放阶段；用超时退出避免阻塞在 live 等待。
        subscription = bus.subscribe(CONVERSATION)
        try:
            for batch in context.ai_event_outbox.list_after(
                CONVERSATION,
                after_history_version=0,
            ):
                chunks.append(stream._batch_event(batch).encode("utf-8"))
        finally:
            bus.unsubscribe(subscription)

    asyncio.run(drain())
    assert len(chunks) >= 1
    assert any(b'"kind":"deferred_handshake"' in c for c in chunks)


def test_event_stream_emits_resync_when_history_ahead_of_outbox() -> None:
    _accept_task()
    context = get_context()
    # 让 history version 前进，但不写对应 outbox（制造不可重放的空洞）。
    context.pydantic_messages.save(
        CONVERSATION,
        [ModelRequest(parts=[UserPromptPart("另一轮")])],
    )
    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=0,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []

    def write(chunk: bytes) -> None:
        chunks.append(chunk)

    async def run() -> None:
        await stream.stream(write)

    # resync 分支会在发完 resync_required 后返回，不会阻塞。
    asyncio.run(asyncio.wait_for(run(), timeout=5))

    text = b"".join(chunks).decode("utf-8")
    assert '"type":"resync_required"' in text


def test_event_stream_detects_retention_cursor_gap_before_replay() -> None:
    """报告 A-14：cursor 落在最早保留批次之前时，缺口必须触发 resync。

    retention 清理后保留 [4, 5]，请求 cursor=1：中间版本 2、3 已不存在。
    旧实现直接发送不连续的 v4、v5 并认为重放完整；修复后重放前识别
    cursor+1 与最早保留批次的缺口，直接回 resync_required，不发送任何批次。
    """

    _accept_task()
    context = get_context()
    published_at = "2026-08-22T00:00:00+00:00"
    # 首次握手批次在存储层默认未发布（由服务层投递后记账）；先记账再清理。
    context.ai_event_outbox.mark_published(
        context.ai_event_outbox.list_unpublished()
    )
    with context.db._connect() as conn:
        for version in range(2, 6):
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
                    published_at,
                ),
            )
        conn.commit()
    # 每会话保留最近 2 条已发布批次：v4、v5；v1-v3 被清理。
    pruned = context.ai_event_outbox.prune_published(keep_latest=2)
    assert pruned == 3

    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=1,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []

    async def run() -> None:
        await stream.stream(chunks.append)

    asyncio.run(asyncio.wait_for(run(), timeout=5))

    text = b"".join(chunks).decode("utf-8")
    # 缺口被识别：只有 resync_required，不发送任何不连续批次。
    assert '"type":"resync_required"' in text
    assert '"type":"batch"' not in text


def test_event_stream_replays_contiguous_batches_after_cursor() -> None:
    """报告 A-14 对照面：cursor 与最早保留批次连续时仍正常重放。"""

    _accept_task()
    context = get_context()
    published_at = "2026-08-22T00:00:00+00:00"
    with context.db._connect() as conn:
        for version in range(2, 4):
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
                    published_at,
                ),
            )
        conn.commit()

    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=2,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []

    async def run() -> None:
        await stream.stream(chunks.append)

    # cursor=2，最早保留批次 v3 连续：重放 v3 后 history 已是 v3，
    # stream 不会 resync，也不会阻塞（无 live 事件时用超时退出）。
    try:
        asyncio.run(asyncio.wait_for(run(), timeout=0.5))
    except asyncio.TimeoutError:
        pass

    text = b"".join(chunks).decode("utf-8")
    assert '"type":"batch"' in text
    assert '"type":"resync_required"' not in text


def test_event_stream_streams_live_batches_after_replay() -> None:
    _accept_task()
    context = get_context()
    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=0,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []
    replay_count = len(
        context.ai_event_outbox.list_after(CONVERSATION, after_history_version=0)
    )

    def write(chunk: bytes) -> None:
        chunks.append(chunk)

    async def publish_soon() -> None:
        await asyncio.sleep(0.2)
        batch = OutboxEventBatch(
            outbox_id=999,
            conversation_id=CONVERSATION,
            run_id="run-live",
            history_version=100,
            kind="continuation",
            events=("data: {\"type\":\"finish\"}\n\n",),
            created_at="",
            published_at="",
        )
        bus.publish(CONVERSATION, batch)

    async def run() -> None:
        publisher = asyncio.create_task(publish_soon())
        # stream 会一直 live；我们等到收到 live batch 后取消。
        task = asyncio.create_task(stream.stream(write))
        while True:
            await asyncio.sleep(0.05)
            if any(b'"kind":"continuation"' in c for c in chunks):
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await publisher

    asyncio.run(asyncio.wait_for(run(), timeout=5))

    text = b"".join(chunks).decode("utf-8")
    assert '"kind":"continuation"' in text
    # 重放批次也应出现。
    assert text.count('"type":"batch"') >= replay_count + 1


def _live_batch(version: int) -> OutboxEventBatch:
    return OutboxEventBatch(
        outbox_id=1000 + version,
        conversation_id=CONVERSATION,
        run_id=f"run-{version}",
        history_version=version,
        kind="continuation",
        events=("data: {\"type\":\"finish\"}\n\n",),
        created_at="",
        published_at="",
    )


def test_event_bus_subscription_queue_is_bounded_under_slow_subscriber() -> None:
    """报告 A-17：慢订阅者不消费时，订阅队列不得随发布线性增长。

    旧实现队列无界（maxsize=0），publish 的 queue.Full 分支永不触发，5000
    个未消费批次全部驻留进程内存。修复后队列有界，溢出订阅被标记，下一次
    poll 抛出显式 resync 信号，绝不静默造成 history_version 缺口。
    """

    bus = AiConversationEventBus()
    subscription = bus.subscribe(CONVERSATION)
    try:
        for index in range(5000):
            bus.publish(CONVERSATION, _live_batch(100 + index))

        # 队列有界：驻留批次不超过上限。
        assert subscription._queue.qsize() <= SUBSCRIPTION_QUEUE_MAXSIZE
        # 溢出订阅的下一次 poll 返回显式 resync 信号。
        with pytest.raises(ConversationResyncRequired):
            subscription.poll(timeout=0.1)
    finally:
        bus.unsubscribe(subscription)


def test_event_bus_close_terminates_on_full_queue() -> None:
    """报告 A-17：满队列下 close/unsubscribe 不得阻塞，哨兵必达。"""

    bus = AiConversationEventBus()
    subscription = bus.subscribe(CONVERSATION)
    for index in range(SUBSCRIPTION_QUEUE_MAXSIZE * 2):
        bus.publish(CONVERSATION, _live_batch(100 + index))
    assert subscription._overflowed is True

    bus.unsubscribe(subscription)

    # 消费必须在有界步数内以 StopIteration 终止（close 先清空再投哨兵）。
    drained = 0
    with pytest.raises(StopIteration):
        while drained <= SUBSCRIPTION_QUEUE_MAXSIZE + 1:
            subscription.poll(timeout=0.1)
            drained += 1


def test_event_stream_emits_resync_when_subscription_overflows() -> None:
    """报告 A-17：live 阶段订阅溢出时，SSE 明确回 resync_required 并结束。"""

    _accept_task()
    context = get_context()
    bus = AiConversationEventBus()
    stream = _make_stream(
        after_history_version=0,
        message_store=context.pydantic_messages,
        event_outbox=context.ai_event_outbox,
        event_bus=bus,
    )
    chunks: list[bytes] = []

    def write(chunk: bytes) -> None:
        chunks.append(chunk)

    async def flood_soon() -> None:
        await asyncio.sleep(0.2)
        for index in range(SUBSCRIPTION_QUEUE_MAXSIZE * 3):
            bus.publish(CONVERSATION, _live_batch(1000 + index))

    async def run() -> None:
        publisher = asyncio.create_task(flood_soon())
        await stream.stream(write)
        await publisher

    # 溢出后 stream 以 resync_required 结束，不会挂起在 live 等待。
    asyncio.run(asyncio.wait_for(run(), timeout=5))

    text = b"".join(chunks).decode("utf-8")
    assert '"type":"resync_required"' in text


# -- GlobalTask GET 只读进度视图（进度计划 §11.2） -------------------------


def _create_in_progress_publish_task() -> str:
    """直接在 Store 落一个 in_progress + active_job 的任务，供 GET 纯读验证。"""

    context = get_context()
    now = datetime.now(timezone.utc)
    task = LocalGlobalTaskState(
        task_id="gtask_get_purity",
        goal="验证 GET 纯读与进度视图",
        status="in_progress",
        steps=[
            LocalTaskStep(
                step_id="step_1",
                capability_name="product_publish_request",
                capability_version="1",
                operation_key="op:get-purity:1",
                status="running",
            )
        ],
        current_step_index=0,
        active_job=TaskActiveJob(
            step_id="step_1",
            capability_name="product_publish_request",
            job_id="job-get-purity",
            job_type=PUBLISH_JOB_TYPE,
            started_at=now,
        ),
        created_at=now,
        updated_at=now,
    )
    created = context.global_tasks.create_task(task)
    return created.task_id


def test_get_returns_typed_execution_progress() -> None:
    task_id = _create_in_progress_publish_task()

    payload, status = global_task_facade.read_global_task_state_payload(task_id)

    assert status == 200
    assert payload["ok"] is True
    assert payload["task_id"] == task_id
    assert payload["task"]["status"] == "in_progress"

    progress = payload["execution_progress"]
    assert progress is not None
    assert progress["observed_at"]
    assert progress["task_elapsed_seconds"] >= 0
    current_step = progress["current_step"]
    assert current_step is not None
    assert current_step["ordinal"] == 1
    assert current_step["total"] == 1
    assert current_step["status"] == "running"
    # Job 在 PublishingBus 中不存在 → 降级展示但 active_job 仍存在。
    active_job = progress["active_job"]
    assert active_job is not None
    assert active_job["job_id"] == "job-get-purity"
    assert active_job["summary"] == "暂时无法读取后台任务进度。"


def test_consecutive_gets_do_not_mutate_task_or_job() -> None:
    task_id = _create_in_progress_publish_task()
    context = get_context()
    before = context.global_tasks.require_task(task_id)

    payload_first, status_first = global_task_facade.read_global_task_state_payload(
        task_id
    )
    payload_second, status_second = global_task_facade.read_global_task_state_payload(
        task_id
    )

    after = context.global_tasks.require_task(task_id)
    assert status_first == 200
    assert status_second == 200
    # GET 不得递增 revision、不得刷新 updated_at、不得改变任务状态。
    assert before.revision == after.revision
    assert before.updated_at == after.updated_at
    assert before.status == after.status
    assert payload_first["task"]["revision"] == before.revision
    assert payload_second["task"]["revision"] == before.revision
