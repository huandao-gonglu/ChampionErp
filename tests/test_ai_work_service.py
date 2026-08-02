from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import urllib.parse

import pytest

from erp_web.context import get_context
from erp_web.http_route_units import ai_work_routes
from erp_web.services import ai_gateway, ai_gateway_providers, ai_work_service
from tests.runtime_test_utils import temp_app_context


@pytest.fixture()
def journal(tmp_path: Path):
    """把 AppContext 绑到测试目录：journal 文件和 ai_sessions 表都落在 tmp_path。"""
    with temp_app_context(tmp_path):
        yield get_context().ai_journal


def _chat_config() -> dict:
    return {
        "ai_models": [
            {
                "id": "chat_model",
                "provider": "OpenAI",
                "api_key": "test-key",
                "base_url": "https://ai.example.com/v1",
                "model": "test-model",
                "capabilities": ["chat", "json"],
            }
        ]
    }


def test_each_ai_work_conversation_uses_an_independent_jsonl(tmp_path: Path, journal) -> None:
    first = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one", "model": "model-one"},
    )
    second = journal.start_conversation(
        use_case_id="category.attribute_fill",
        capability="chat_json",
        provider_id="fake",
        model={"id": "two", "model": "model-two"},
    )

    first.emit_text_delta("first")
    first.finish_assistant_message()
    first.finish({"ok": True})
    second.emit_text_delta("second")
    second.finish_assistant_message()
    second.finish({"ok": True})

    assert first.path != second.path
    assert first.path.exists()
    assert second.path.exists()
    assert first.path.is_relative_to(tmp_path / ai_work_service.AI_WORK_RELATIVE_DIR)
    assert {path.name for path in first.path.parent.glob("*.jsonl")} == {
        f"{first.conversation_id}.jsonl",
        f"{second.conversation_id}.jsonl",
    }
    if os.name != "nt":
        journal_root = (
            tmp_path / ai_work_service.AI_WORK_RELATIVE_DIR
        )
        assert journal_root.stat().st_mode & 0o777 == 0o700
        assert first.path.parent.stat().st_mode & 0o777 == 0o700
        assert first.path.stat().st_mode & 0o777 == 0o600
        assert second.path.stat().st_mode & 0o777 == 0o600
    first_events = journal.read_events(first.conversation_id)
    second_events = journal.read_events(second.conversation_id)
    assert [event["seq"] for event in first_events] == list(range(1, len(first_events) + 1))
    assert [event.get("delta") for event in first_events if event["type"] == "TEXT_MESSAGE_CONTENT"] == ["first"]
    assert [event.get("delta") for event in second_events if event["type"] == "TEXT_MESSAGE_CONTENT"] == ["second"]


def test_ai_sessions_rows_track_metadata_and_conditions_are_released(journal) -> None:
    conversation = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
    )
    conversation_id = conversation.conversation_id
    row = get_context().db.get_ai_session(conversation_id)
    assert row["status"] == "running"
    assert row["day"] == conversation.path.parent.name
    assert row["last_seq"] == 1
    assert conversation_id in journal._conditions

    conversation.emit_text_delta("hola")
    conversation.finish({"ok": True})

    events = journal.read_events(conversation_id)
    row = get_context().db.get_ai_session(conversation_id)
    assert row["status"] == "completed"
    assert row["last_seq"] == events[-1]["seq"]
    assert row["updated_at"] == events[-1]["occurred_at"]
    # 会话终态后 Condition 条目必须清理，防止长期泄漏。
    assert conversation_id not in journal._conditions

    failed = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
    )
    failed.fail(RuntimeError("boom"))
    assert get_context().db.get_ai_session(failed.conversation_id)["status"] == "failed"
    assert failed.conversation_id not in journal._conditions

    # 列表按表驱动，倒序返回最近会话。
    listed = journal.list_conversations(limit=10)
    assert [item["conversation_id"] for item in listed] == [failed.conversation_id, conversation_id]
    assert listed[0]["status"] == "failed"
    assert listed[1]["status"] == "completed"


def test_ai_work_wait_returns_only_incremental_events(journal) -> None:
    conversation = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
    )
    started = threading.Event()

    def writer() -> None:
        started.wait()
        conversation.emit_text_delta("delta")

    worker = threading.Thread(target=writer)
    worker.start()
    started.set()
    events = journal.wait_for_events(
        conversation.conversation_id,
        after_seq=1,
        wait_ms=1_000,
    )
    if not any(event["type"] == "TEXT_MESSAGE_CONTENT" for event in events):
        events.extend(
            journal.wait_for_events(
                conversation.conversation_id,
                after_seq=int(events[-1]["seq"]),
                wait_ms=1_000,
            )
        )
    worker.join(timeout=1)

    assert [event["type"] for event in events] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert events[-1]["delta"] == "delta"


def test_ai_work_wait_rechecks_events_before_blocking(journal, monkeypatch) -> None:
    event = {"seq": 2, "type": "TEXT_MESSAGE_CONTENT", "delta": "just-written"}
    reads = iter([[], [event]])

    class FakeCondition:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def wait(timeout: float) -> None:
            raise AssertionError("复查已经读到事件时不应继续等待")

    monkeypatch.setattr(journal, "read_events", lambda conversation_id, after_seq=0: next(reads))
    monkeypatch.setattr(journal, "_condition_for", lambda conversation_id: FakeCondition())

    events = journal.wait_for_events(
        "conversation_1",
        after_seq=1,
        wait_ms=1_000,
    )

    assert events == [event]


def test_ai_work_projects_provider_exchange_without_copying_messages(
    tmp_path: Path, journal, monkeypatch
) -> None:
    def fake_direct_chat(**kwargs):
        kwargs["recorder"].finish_assistant_message('{"title":"Hola"}')
        return {"title": "Hola"}

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        fake_direct_chat,
    )
    messages = [
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": "Localize this product."},
    ]

    result = ai_gateway.chat_json(tmp_path, _chat_config(), "copy.generate", messages, stream=False)

    assert result == {"title": "Hola"}
    conversations = journal.list_conversations()
    assert len(conversations) == 1
    events = journal.read_events(conversations[0]["conversation_id"])
    assert not any(event.get("name") == "provider.request" for event in events)
    response_event = next(
        event for event in events if event.get("name") == "provider.response"
    )
    assert response_event["value"]["character_count"] == len('{"title":"Hola"}')
    started = events[0]
    assert started["type"] == "RUN_STARTED"
    assert started["input"] == {}
    assert [event.get("delta") for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"] == [
        '{"title":"Hola"}'
    ]
    result_event = next(event for event in events if event.get("name") == "business.result")
    assert result_event["value"]["parsed"] == {"title": "Hola"}
    assert events[-1]["type"] == "RUN_FINISHED"
    serialized = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    assert "Return JSON." not in serialized
    assert "Localize this product." not in serialized
    for line in (tmp_path / ai_work_service.AI_WORK_RELATIVE_DIR).glob("*/*.jsonl"):
        for raw_line in line.read_text(encoding="utf-8").splitlines():
            assert isinstance(json.loads(raw_line), dict)


def test_chat_streams_provider_deltas_by_default(tmp_path: Path, journal, monkeypatch) -> None:
    def fake_direct_chat(**kwargs):
        kwargs["recorder"].emit_text_delta('{"title":')
        kwargs["recorder"].emit_text_delta('"Hola"}')
        kwargs["recorder"].finish_assistant_message()
        return {"title": "Hola"}

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        fake_direct_chat,
    )

    result = ai_gateway.chat_json(
        tmp_path,
        _chat_config(),
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
    )

    assert result == {"title": "Hola"}
    conversation = journal.list_conversations()[0]
    events = journal.read_events(conversation["conversation_id"])
    assert [event.get("delta") for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"] == [
        '{"title":',
        '"Hola"}',
    ]
    assert events[-1]["type"] == "RUN_FINISHED"


def test_ai_work_routes_list_and_incrementally_read_conversations(journal) -> None:
    conversation = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
    )
    conversation.emit_text_delta("hello")

    class FakeHandler:
        def __init__(self) -> None:
            self.json_payload = None
            self.ndjson_payload = None
            self.status = None

        def send_json(self, data, status=200):
            self.json_payload = data
            self.status = status

        def send_ndjson(self, items, status=200):
            self.ndjson_payload = items
            self.status = status

    list_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        list_handler,
        urllib.parse.urlparse("/api/v1/ai-work/conversations"),
    )
    assert list_handler.json_payload["conversations"][0]["conversation_id"] == conversation.conversation_id

    events_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        events_handler,
        urllib.parse.urlparse(
            f"/api/v1/ai-work/conversations/{conversation.conversation_id}/events?after_seq=1"
        ),
    )
    assert [event["type"] for event in events_handler.ndjson_payload] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
    ]
