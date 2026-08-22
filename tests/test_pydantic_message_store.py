from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from erp_web.db import ErpDatabase
from erp_web.stores.pydantic_message_store import (
    PydanticMessageStore,
    PydanticMessageStoreError,
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


def _deferred_open_history() -> list[ModelMessage]:
    """合法 Deferred 开口：存在 ToolCallPart 但没有对应 ToolReturnPart。"""

    return [
        ModelRequest(
            parts=[UserPromptPart("创建任务")],
            run_id="run_1",
            conversation_id="conversation_1",
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "global_task_start",
                    {"goal": "发布商品"},
                    tool_call_id="call-deferred-1",
                )
            ],
            model_name="test-model",
            provider_name="test",
            run_id="run_1",
            conversation_id="conversation_1",
        ),
    ]


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


# -- history version：每次保存递增，供 Deferred CAS 与订阅 cursor 使用 -------


def test_save_increments_history_version(tmp_path: Path) -> None:
    _, store = _store(tmp_path)

    first = store.save("conversation_1", _messages(answered=False))
    second = store.save("conversation_1", _messages())

    assert first.history_version == 1
    assert second.history_version == 2
    assert store.get_version("conversation_1") == 2
    assert store.get_version("conversation_missing") == 0


# -- Deferred 开口历史：读取保持官方原貌，不合成 tool return -----------------


def test_read_preserves_deferred_open_history_without_synthesis(
    tmp_path: Path,
) -> None:
    _, store = _store(tmp_path)
    messages = _deferred_open_history()

    store.save("conversation_1", messages)
    loaded = store.get("conversation_1")

    assert loaded is not None
    reloaded = loaded.model_messages()
    # 读取不得补造任何 ToolReturnPart；Deferred 开口必须留给官方
    # DeferredToolResults 的后续 run 显式闭合。
    assert ModelMessagesTypeAdapter.dump_json(reloaded) == (
        ModelMessagesTypeAdapter.dump_json(messages)
    )
    returned_ids = {
        str(getattr(part, "tool_call_id", "") or "")
        for message in reloaded
        for part in getattr(message, "parts", ())
        if type(part).__name__.endswith("ToolReturnPart")
    }
    assert returned_ids == set()
