from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
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
