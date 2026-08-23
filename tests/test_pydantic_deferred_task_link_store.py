"""Deferred task link ledger 的当前持久化契约测试。"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from erp_web.db import ErpDatabase
from erp_web.stores.pydantic_ai_event_outbox_store import (
    PydanticAiEventOutboxStore,
)
from erp_web.stores.pydantic_deferred_task_link_store import (
    ABANDONED,
    AWAITING_HISTORY,
    MAX_OUTBOX_EVENT_CHUNKS,
    MAX_OUTBOX_EVENT_CHUNK_BYTES,
    MAX_OUTBOX_EVENT_TOTAL_BYTES,
    MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS,
    READY,
    RESOLVED,
    PydanticDeferredLinkError,
    PydanticDeferredTaskLinkStore,
)


CONVERSATION = "conversation_global_chat_" + "a" * 32


def _task_payload(task_id: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "task_id": task_id,
        "goal": "测试任务",
        "product_id": "",
        "draft_id": "",
        "platform": "",
        "status": "running",
        "steps": [],
        "current_step_index": 0,
        "assistant_message": "任务计划已创建。",
        "created_at": now,
        "updated_at": now,
    }


def _history(conversation_id: str) -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[UserPromptPart("创建任务")],
            run_id="run_1",
            conversation_id=conversation_id,
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "global_task_start",
                    {"goal": "测试任务"},
                    tool_call_id="call_1",
                )
            ],
            model_name="test-model",
            provider_name="test",
            run_id="run_1",
            conversation_id=conversation_id,
        ),
    ]


@pytest.fixture()
def stores(tmp_path: Path):
    db = ErpDatabase(tmp_path / "erp.sqlite3")
    return (
        db,
        PydanticDeferredTaskLinkStore(db),
        PydanticAiEventOutboxStore(db),
    )


def test_create_task_and_provisional_link_atomically(stores) -> None:
    db, links, _ = stores

    payload, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )

    assert payload["task_id"] == "gtask_1"
    assert link.link_status == AWAITING_HISTORY
    assert link.ready_at == ""
    assert link.task_id == "gtask_1"
    assert link.conversation_id == CONVERSATION
    assert link.tool_call_id == "call_1"
    assert link.request_run_id == "run_1"
    assert db.get_deferred_task_link_by_task("gtask_1") is not None


def test_second_active_link_on_same_conversation_is_rejected(stores) -> None:
    _, links, _ = stores
    links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )

    with pytest.raises(sqlite3.IntegrityError):
        links.create_with_task(
            _task_payload("gtask_2"),
            conversation_id=CONVERSATION,
            request_run_id="run_1",
            tool_call_id="call_2",
        )

    # 失败的事务不能留下孤儿 Task。
    assert links.get_by_task("gtask_2") is None


def test_same_task_cannot_bind_two_links(stores) -> None:
    _, links, _ = stores
    links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    with pytest.raises(sqlite3.IntegrityError):
        links.create_with_task(
            _task_payload("gtask_1"),
            conversation_id="conversation_global_chat_" + "b" * 32,
            request_run_id="run_2",
            tool_call_id="call_9",
        )


def test_initial_deferred_commit_sets_ready_and_outbox_atomically(stores) -> None:
    db, links, outbox = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )

    version = links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=["data: {\"type\":\"start\"}\n\n", "data: [DONE]\n\n"],
    )

    ready = links.get(link.link_id)
    assert ready is not None
    assert ready.link_status == READY
    assert ready.ready_at != ""
    assert ready.history_version == version
    history = db.get_pydantic_message_history(CONVERSATION)
    assert history is not None
    assert int(history["history_version"]) == version
    batches = outbox.list_after(CONVERSATION, after_history_version=0)
    assert [batch.history_version for batch in batches] == [version]
    assert batches[0].kind == "deferred_handshake"
    assert batches[0].run_id == "run_1"
    assert len(batches[0].events) == 2


def test_initial_deferred_commit_rejects_non_awaiting_link(stores) -> None:
    _, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )

    with pytest.raises(PydanticDeferredLinkError) as caught:
        links.commit_initial_deferred_history(
            CONVERSATION,
            _history(CONVERSATION),
            link_id=link.link_id,
            request_run_id="run_1",
            encoded_chunks=[],
        )
    assert caught.value.code == "PYDANTIC_DEFERRED_LINK_NOT_AWAITING"


def test_outbox_commit_bounds_terminal_segment_and_enforces_byte_caps(
    stores,
) -> None:
    """线上缺陷 L-01 回归 + 报告 §8-11（outbox 侧）。

    流式 run 按 delta 逐条编码，chunk 数轻松超过旧 512 条防线：提交必须
    成功，且 outbox 只持久化按原顺序取尾的有界终态段；单条/总字节上限
    仍然成立。
    """

    db, links, outbox = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )

    def expect_too_large(encoded_chunks: list[str]) -> None:
        with pytest.raises(PydanticDeferredLinkError) as caught:
            links.commit_initial_deferred_history(
                CONVERSATION,
                _history(CONVERSATION),
                link_id=link.link_id,
                request_run_id="run_1",
                encoded_chunks=encoded_chunks,
            )
        assert caught.value.code == "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE"

    # 单条 chunk 超过 64 KiB。
    expect_too_large(["data: " + "x" * (MAX_OUTBOX_EVENT_CHUNK_BYTES + 1)])
    # 每条不超单条上限，但总字节超过 1 MiB（条数在终态段长度内）。
    chunk = "data: " + "y" * (MAX_OUTBOX_EVENT_CHUNK_BYTES // 2) + "\n\n"
    count = (MAX_OUTBOX_EVENT_TOTAL_BYTES // len(chunk.encode("utf-8"))) + 2
    assert count <= MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS
    expect_too_large([chunk] * count)

    # 上限校验失败不得改动 link 与 history：随后正常提交仍然成功。
    version = links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=["data: {\"type\":\"finish\"}\n\n"],
    )
    ready = links.get(link.link_id)
    assert ready is not None
    assert ready.link_status == READY
    assert ready.history_version == version
    assert int(db.get_pydantic_message_history(CONVERSATION)["history_version"]) == (
        version
    )

    # 线上事故复现（独立 conversation）：远超 512 条的编码 chunk 批次必须能
    # 提交，outbox 只持久化有界终态段。
    large_conversation = "conversation_global_chat_" + "c" * 32
    _, large_link = links.create_with_task(
        _task_payload("gtask_2"),
        conversation_id=large_conversation,
        request_run_id="run_2",
        tool_call_id="call_2",
    )
    many_chunks = [
        f"data: {{\"type\":\"text-delta\",\"seq\":{index}}}\n\n"
        for index in range(MAX_OUTBOX_EVENT_CHUNKS + 700)
    ]
    large_version = links.commit_initial_deferred_history(
        large_conversation,
        _history(large_conversation),
        link_id=large_link.link_id,
        request_run_id="run_2",
        encoded_chunks=many_chunks,
    )
    batches = outbox.list_after(large_conversation, after_history_version=0)
    assert [batch.history_version for batch in batches] == [large_version]
    assert len(batches[0].events) == MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS
    # 终态段按原顺序取尾：回合闭合事件（finish/[DONE] 等）得以保留。
    assert list(batches[0].events) == many_chunks[
        -MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS:
    ]


def test_continuation_commit_cas_and_resolved(stores) -> None:
    db, links, outbox = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    frozen_version = links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )
    db.save_global_task(
        {**_task_payload("gtask_1"), "status": "completed", "revision": 1},
        expected_revision=1,
    )

    claimed = links.claim(link.link_id)
    assert claimed is not None
    _, lease_id = claimed

    resolved_version = links.commit_continuation_history(
        CONVERSATION,
        [*_history(CONVERSATION), *_history(CONVERSATION)],
        link_id=link.link_id,
        expected_version=frozen_version,
        continuation_run_id="run_2",
        lease_id=lease_id,
        encoded_chunks=["data: {\"type\":\"start\"}\n\n"],
    )

    assert resolved_version == frozen_version + 1
    resolved = links.get(link.link_id)
    assert resolved is not None
    assert resolved.link_status == RESOLVED
    assert resolved.resolved_at != ""
    assert resolved.continuation_run_id == "run_2"
    assert resolved.lease_id == ""
    batches = outbox.list_after(CONVERSATION, after_history_version=0)
    assert [batch.kind for batch in batches] == [
        "deferred_handshake",
        "continuation",
    ]


def test_continuation_commit_cas_failure_returns_none(stores) -> None:
    db, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    frozen_version = links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )
    claimed = links.claim(link.link_id)
    assert claimed is not None
    _, lease_id = claimed

    result = links.commit_continuation_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        expected_version=frozen_version + 5,
        continuation_run_id="run_2",
        lease_id=lease_id,
        encoded_chunks=[],
    )

    assert result is None
    current = links.get(link.link_id)
    assert current is not None
    assert current.link_status == READY
    # CAS 失败不得改动已提交 history。
    assert int(db.get_pydantic_message_history(CONVERSATION)["history_version"]) == (
        frozen_version
    )

    # 租约被第二个 worker 接管后，原执行者的最终提交必须失败。
    assert links.release_claim(link.link_id, lease_id) is True
    taken_over = links.claim(link.link_id)
    assert taken_over is not None
    _, new_lease_id = taken_over
    assert new_lease_id != lease_id
    stale_result = links.commit_continuation_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        expected_version=frozen_version,
        continuation_run_id="run_2",
        lease_id=lease_id,
        encoded_chunks=[],
    )
    assert stale_result is None
    assert links.get(link.link_id).link_status == READY


def test_continuation_claim_lease_serializes_single_winner(stores) -> None:
    db, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )
    db.save_global_task(
        {**_task_payload("gtask_1"), "status": "completed", "revision": 1},
        expected_revision=1,
    )

    continuable = links.list_continuable()
    assert [item.link_id for item in continuable] == [link.link_id]

    first = links.claim(link.link_id)
    second = links.claim(link.link_id)
    assert first is not None
    assert second is None

    assert links.release_claim(link.link_id, first[1]) is True
    third = links.claim(link.link_id)
    assert third is not None


def test_continuable_requires_terminal_task_and_ready_link(stores) -> None:
    db, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    # Task 尚未终结：不可 continuation。
    assert links.list_continuable() == []

    links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )
    # ready 但 Task 仍 running：不可 continuation。
    assert links.list_continuable() == []

    db.save_global_task(
        {**_task_payload("gtask_1"), "status": "failed", "revision": 1},
        expected_revision=1,
    )
    assert [item.link_id for item in links.list_continuable()] == [link.link_id]


def test_abandon_expired_link_cancels_unexecuted_task(stores) -> None:
    db, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )

    abandoned = links.abandon_expired(
        link.link_id,
        cancel_assistant_message="首次握手未完成，任务已取消。",
    )

    assert abandoned is not None
    assert abandoned.link_status == ABANDONED
    assert abandoned.abandoned_at != ""
    task = db.load_global_task("gtask_1")
    assert task["status"] == "cancelled"
    # conversation 被释放：可以接受新的用户回合（无 active link）。
    assert links.active_for_conversation(CONVERSATION) is None


def test_ready_link_cannot_be_abandoned(stores) -> None:
    _, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )

    assert links.abandon_expired(
        link.link_id,
        cancel_assistant_message="不允许放弃",
    ) is None
    current = links.get(link.link_id)
    assert current is not None
    assert current.link_status == READY


def test_repair_provisional_link_to_ready(stores) -> None:
    db, links, _ = stores
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    # 模拟崩溃前捕获消息已经保存：history 存在但 link 仍是 provisional。
    saved = db.replace_pydantic_message_history(
        CONVERSATION,
        b"[]",
        now=datetime.now(timezone.utc).isoformat(),
    )

    repaired = links.repair_to_ready(
        link.link_id,
        history_version=int(saved["history_version"]),
    )

    assert repaired is not None
    assert repaired.link_status == READY
    assert repaired.history_version == int(saved["history_version"])


def test_expired_provisional_sweep_uses_ttl(stores) -> None:
    db, links, _ = stores
    _, fresh = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    # 手工把另一条 link 的 created_at 拨到 TTL 之外。
    old_time = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    with db._connect() as conn:
        conn.execute(
            "UPDATE pydantic_deferred_task_links SET created_at = ? WHERE link_id = ?",
            (old_time, fresh.link_id),
        )
        conn.commit()

    expired = links.list_expired_provisional(ttl_seconds=300)
    assert [item.link_id for item in expired] == [fresh.link_id]
    assert links.list_expired_provisional(ttl_seconds=3600 * 3) == []


def test_has_active_blocks_conversation_until_resolved(stores) -> None:
    db, links, _ = stores
    assert links.has_active(CONVERSATION) is False
    _, link = links.create_with_task(
        _task_payload("gtask_1"),
        conversation_id=CONVERSATION,
        request_run_id="run_1",
        tool_call_id="call_1",
    )
    assert links.has_active(CONVERSATION) is True
    version = links.commit_initial_deferred_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        request_run_id="run_1",
        encoded_chunks=[],
    )
    assert links.has_active(CONVERSATION) is True
    db.save_global_task(
        {**_task_payload("gtask_1"), "status": "completed", "revision": 1},
        expected_revision=1,
    )
    assert links.has_active(CONVERSATION) is True
    claimed = links.claim(link.link_id)
    assert claimed is not None
    _, lease_id = claimed
    links.commit_continuation_history(
        CONVERSATION,
        _history(CONVERSATION),
        link_id=link.link_id,
        expected_version=version,
        continuation_run_id="run_2",
        lease_id=lease_id,
        encoded_chunks=[],
    )
    assert links.has_active(CONVERSATION) is False
