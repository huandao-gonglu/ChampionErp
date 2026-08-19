from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from erp_web.db import ErpDatabase
from erp_web.stores.pydantic_message_store import (
    INTERRUPTED_TOOL_RETURN_CONTENT,
    PydanticMessageStore,
    PydanticMessageStoreError,
    SYNTHESIZED_TOOL_RETURN_METADATA_KEY,
    repair_orphaned_tool_returns,
)


def _messages(*, answered: bool = True) -> list[ModelMessage]:
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart("请检查商品草稿")],
            run_id="run_1",
            conversation_id="conversation_1",
        )
    ]
    if answered:
        messages.append(
            ModelResponse(
                parts=[TextPart("草稿已检查")],
                model_name="test-model",
                provider_name="test",
                run_id="run_1",
                conversation_id="conversation_1",
            )
        )
    return messages


def _store(tmp_path: Path) -> tuple[ErpDatabase, PydanticMessageStore]:
    db = ErpDatabase(tmp_path / "erp.sqlite3")
    return db, PydanticMessageStore(db)


def test_official_message_json_round_trips_without_custom_projection(
    tmp_path: Path,
) -> None:
    db, store = _store(tmp_path)
    messages = _messages()

    saved = store.save("conversation_1", messages)
    raw_row = db.get_pydantic_message_history("conversation_1")
    loaded = store.get("conversation_1")

    assert raw_row is not None
    assert raw_row["messages_json"] == ModelMessagesTypeAdapter.dump_json(messages)
    assert saved.messages_json == raw_row["messages_json"]
    assert loaded is not None
    assert ModelMessagesTypeAdapter.dump_json(loaded.model_messages()) == (
        saved.messages_json
    )


def test_save_atomically_replaces_one_conversation_history(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    first = store.save("conversation_1", _messages(answered=False))
    replacement_messages = _messages(answered=True)

    replaced = store.save("conversation_1", replacement_messages)

    assert replaced.created_at == first.created_at
    assert replaced.messages_json == ModelMessagesTypeAdapter.dump_json(
        replacement_messages
    )
    summaries = store.list()
    assert len(summaries) == 1
    assert summaries[0].conversation_id == "conversation_1"
    assert summaries[0].created_at == replaced.created_at
    assert summaries[0].updated_at == replaced.updated_at


def test_store_rejects_non_pydantic_messages_before_writing(tmp_path: Path) -> None:
    db, store = _store(tmp_path)

    with pytest.raises(PydanticMessageStoreError) as caught:
        store.save(
            "conversation_1",
            [{"kind": "project.custom_event", "value": "forbidden"}],  # type: ignore[list-item]
        )

    assert caught.value.code == "PYDANTIC_MESSAGE_HISTORY_INVALID"
    assert db.get_pydantic_message_history("conversation_1") is None


def test_store_rejects_corrupt_json_read_from_sqlite(tmp_path: Path) -> None:
    db, store = _store(tmp_path)
    db.replace_pydantic_message_history(
        "conversation_1",
        b'{"type":"project.custom_event"}',
        now="2026-08-14T00:00:00+00:00",
    )

    with pytest.raises(PydanticMessageStoreError) as caught:
        store.get("conversation_1")

    assert caught.value.code == "PYDANTIC_MESSAGE_HISTORY_CORRUPT"


def test_delete_removes_only_the_requested_conversation(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    store.save("conversation_1", _messages())
    store.save("conversation_2", _messages())

    assert store.delete("conversation_1") is True
    assert store.delete("conversation_1") is False
    assert store.get("conversation_1") is None
    assert [item.conversation_id for item in store.list()] == ["conversation_2"]


# -- repair_orphaned_tool_returns：工具调用/返回配对修复 --------------------

_TS = datetime(2026, 8, 19, 13, 51, 39, tzinfo=timezone.utc)


def _call_response(*calls: tuple[str, str]) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(tool_name, {}, tool_call_id=call_id)
            for tool_name, call_id in calls
        ],
        timestamp=_TS,
    )


def _return_request(tool_name: str, call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name,
                {"ok": True},
                tool_call_id=call_id,
                timestamp=_TS,
            )
        ]
    )


def _returned_ids(messages: list[ModelMessage]) -> set[str]:
    return {
        str(part.tool_call_id)
        for message in messages
        for part in getattr(message, "parts", ())
        if isinstance(part, ToolReturnPart)
    }


def test_repair_fills_return_for_trailing_orphan_tool_call() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("查询产品")]),
        _call_response(("product_read", "call-a")),
    ]

    repaired = repair_orphaned_tool_returns(messages)

    assert _returned_ids(repaired) == {"call-a"}
    synthesized = [
        part
        for message in repaired
        for part in getattr(message, "parts", ())
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-a"
    ]
    assert len(synthesized) == 1
    assert synthesized[0].outcome == "interrupted"
    assert synthesized[0].content == INTERRUPTED_TOOL_RETURN_CONTENT
    assert synthesized[0].metadata == {SYNTHESIZED_TOOL_RETURN_METADATA_KEY: True}
    # 使用来源响应的时间戳，保证确定性。
    assert synthesized[0].timestamp == _TS


def test_repair_handles_partial_parallel_batch_mid_conversation() -> None:
    """并行批中一个调用有返回、另一个被打断：只补缺失的那个。"""

    messages: list[ModelMessage] = [
        _call_response(("drafts_query", "call-kept"), ("product_read", "call-lost")),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "drafts_query",
                    {"total": 0},
                    tool_call_id="call-kept",
                    timestamp=_TS,
                ),
                UserPromptPart("查询一下产品"),
            ]
        ),
        _call_response(("product_read", "call-tail")),
    ]

    repaired = repair_orphaned_tool_returns(messages)

    assert _returned_ids(repaired) == {"call-kept", "call-lost", "call-tail"}
    # call-kept 已有真实返回，不能被重复合成。
    kept_returns = [
        part
        for message in repaired
        for part in getattr(message, "parts", ())
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-kept"
    ]
    assert len(kept_returns) == 1
    assert kept_returns[0].outcome == "success"


def test_repair_is_idempotent_and_deterministic() -> None:
    messages: list[ModelMessage] = [
        _call_response(("product_read", "call-a"), ("products_index_query", "call-b")),
    ]

    first = repair_orphaned_tool_returns(messages)
    second = repair_orphaned_tool_returns(first)

    assert ModelMessagesTypeAdapter.dump_json(first) == (
        ModelMessagesTypeAdapter.dump_json(second)
    )


def test_repair_leaves_complete_history_unchanged() -> None:
    messages: list[ModelMessage] = [
        _call_response(("lookup_item", "call-1")),
        _return_request("lookup_item", "call-1"),
    ]

    repaired = repair_orphaned_tool_returns(messages)

    assert len(repaired) == len(messages)
    assert ModelMessagesTypeAdapter.dump_json(repaired) == (
        ModelMessagesTypeAdapter.dump_json(messages)
    )


def test_store_read_repairs_legacy_dirty_blob_without_writing(tmp_path: Path) -> None:
    """历史遗留的脏数据（读取路径）也必须被修复，且不改变存储内容。"""

    db, store = _store(tmp_path)
    dirty = [
        _call_response(("product_read", "legacy-orphan")),
    ]
    # 直接写入未修复的原始 blob，模拟历史遗留脏数据。
    db.replace_pydantic_message_history(
        "conversation_legacy",
        ModelMessagesTypeAdapter.dump_json(dirty),
        now="2026-08-19T00:00:00+00:00",
    )

    loaded = store.get("conversation_legacy")
    assert loaded is not None
    assert _returned_ids(loaded.model_messages()) == {"legacy-orphan"}

    # 读取是纯修复：存储的原始 blob 不被改写。
    raw = db.get_pydantic_message_history("conversation_legacy")
    assert raw is not None
    assert ModelMessagesTypeAdapter.validate_json(raw["messages_json"]) == dirty
