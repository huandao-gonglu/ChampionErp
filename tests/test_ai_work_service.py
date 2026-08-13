from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
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
    assert row["parent_session_id"] is None
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


def test_reasoning_stream_is_persisted_before_assistant_text(journal) -> None:
    conversation = journal.start_conversation(
        use_case_id="category.attribute_fill",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
        stream=True,
    )

    conversation.emit_reasoning_delta("正在判断")
    conversation.emit_reasoning_delta("商品属性")
    conversation.emit_text_delta('{"ok":true}')
    conversation.finish_assistant_message()
    conversation.finish({"ok": True})

    events = journal.read_events(conversation.conversation_id)
    message_events = [
        event
        for event in events
        if event["type"].startswith("REASONING_")
        or event["type"].startswith("TEXT_MESSAGE_")
    ]
    assert [event["type"] for event in message_events] == [
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert "".join(
        str(event.get("delta") or "")
        for event in message_events
        if event["type"] == "REASONING_MESSAGE_CONTENT"
    ) == "正在判断商品属性"


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

    conversation_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        conversation_handler,
        urllib.parse.urlparse(
            f"/api/v1/ai-work/conversations/{conversation.conversation_id}"
        ),
    )
    assert conversation_handler.json_payload["conversation"][
        "conversation_id"
    ] == conversation.conversation_id
    assert conversation_handler.json_payload["conversation"][
        "parent_conversation_id"
    ] is None

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


def test_ai_work_route_rejects_invalid_conversation_id_with_stable_json(
    journal,
) -> None:
    class FakeHandler:
        json_payload = None
        status = None

        def send_json(self, data, status=200):
            self.json_payload = data
            self.status = status

    handler = FakeHandler()

    assert ai_work_routes.handle_get(
        handler,
        urllib.parse.urlparse(
            "/api/v1/ai-work/conversations/invalid%2Fconversation"
        ),
    )

    assert handler.status == 400
    assert handler.json_payload == {
        "ok": False,
        "error": "AI 对话 ID 无效。",
        "error_code": "AI_WORK_CONVERSATION_ID_INVALID",
    }


def test_global_agent_conversation_keeps_a_bounded_stable_projection(journal) -> None:
    conversation_id = journal.start_global_agent_conversation()
    execution = journal.start_conversation(
        use_case_id="global.task.plan",
        capability="agent",
        provider_id="fake",
        model={"id": "one"},
    )

    journal.require_global_agent_conversation(conversation_id)
    journal.project_global_agent_event(
        conversation_id,
        "global.user_message",
        {
            "task_id": "t" * 300,
            "message": "m" * 5000,
            "full_product": {"description": "不得复制到投影"},
        },
    )
    journal.project_global_agent_event(
        conversation_id,
        "global.task_state",
        {
            "task_id": "task-1",
            "status": "s" * 100,
            "summary": "x" * 3000,
            "publish_payload": {"secret": "不得复制到投影"},
        },
    )
    journal.project_global_agent_event(
        conversation_id,
        "global.agent_execution_link",
        {
            "task_id": "task-1",
            "conversation_id": execution.conversation_id,
            "tool_output": {"items": ["不得复制到投影"]},
        },
    )
    duplicate = journal.project_global_agent_event(
        conversation_id,
        "global.agent_execution_link",
        {
            "task_id": "task-1",
            "conversation_id": execution.conversation_id,
        },
    )

    events = journal.read_events(conversation_id)
    custom_events = [event for event in events if event["type"] == "CUSTOM"]
    assert [event["name"] for event in custom_events] == [
        "global.user_message",
        "global.task_state",
        "global.agent_execution_link",
    ]
    assert not any(event["type"] == "RUN_RESUMED" for event in events)

    user_projection = custom_events[0]["value"]
    assert user_projection == {
        "task_id": "t" * 160,
        "message": "m" * 4000,
    }
    task_projection = custom_events[1]["value"]
    assert task_projection == {
        "task_id": "task-1",
        "status": "s" * 80,
        "summary": "x" * 2000,
    }
    execution_projection = custom_events[2]["value"]
    assert execution_projection == {
        "task_id": "task-1",
        "conversation_id": execution.conversation_id,
    }
    assert duplicate == custom_events[2]
    assert get_context().db.get_ai_session(execution.conversation_id)[
        "parent_session_id"
    ] == conversation_id

    summary = next(
        item
        for item in journal.list_conversations()
        if item["conversation_id"] == conversation_id
    )
    assert summary["use_case_id"] == ai_work_service.GLOBAL_AGENT_CHAT_USE_CASE_ID
    assert summary["parent_conversation_id"] is None
    assert summary["status"] == "running"
    assert summary["latest_task_status"] == "s" * 80
    assert execution.conversation_id not in {
        item["conversation_id"] for item in journal.list_conversations()
    }
    child_summary = journal.list_child_conversations(conversation_id)[0]
    assert child_summary["conversation_id"] == execution.conversation_id
    assert child_summary["parent_conversation_id"] == conversation_id


def test_ai_work_parent_binding_rejects_takeover_and_supports_root_filter_limit(
    journal,
) -> None:
    first_parent = journal.start_global_agent_conversation()
    child = journal.start_conversation(
        use_case_id="global.task.plan",
        capability="agent",
        provider_id="fake",
        model={"id": "one"},
        parent_conversation_id=first_parent,
    )
    second_parent = journal.start_global_agent_conversation()

    with pytest.raises(ValueError, match="已属于其他父对话"):
        journal.project_global_agent_event(
            second_parent,
            "global.agent_execution_link",
            {
                "task_id": "task-2",
                "conversation_id": child.conversation_id,
            },
        )

    roots = journal.list_conversations(limit=2)
    assert len(roots) == 2
    assert child.conversation_id not in {
        item["conversation_id"] for item in roots
    }
    all_recent = journal.list_conversations(
        limit=3,
        include_children=True,
    )
    assert child.conversation_id in {
        item["conversation_id"] for item in all_recent
    }


def test_ai_work_children_route_returns_only_direct_children(journal) -> None:
    parent_id = journal.start_global_agent_conversation()
    child = journal.start_conversation(
        use_case_id="global.task.plan",
        capability="agent",
        provider_id="fake",
        model={"id": "one"},
        parent_conversation_id=parent_id,
    )
    unrelated = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "two"},
    )

    class FakeHandler:
        json_payload = None
        status = None

        def send_json(self, data, status=200):
            self.json_payload = data
            self.status = status

    children_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        children_handler,
        urllib.parse.urlparse(
            f"/api/v1/ai-work/conversations/{parent_id}/children"
        ),
    )
    assert [
        item["conversation_id"]
        for item in children_handler.json_payload["conversations"]
    ] == [child.conversation_id]
    assert unrelated.conversation_id not in {
        item["conversation_id"]
        for item in children_handler.json_payload["conversations"]
    }

    nested_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        nested_handler,
        urllib.parse.urlparse(
            f"/api/v1/ai-work/conversations/{child.conversation_id}/children"
        ),
    )
    assert nested_handler.status == 400
    assert "只有根对话" in nested_handler.json_payload["error"]

    extra_segment_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        extra_segment_handler,
        urllib.parse.urlparse(
            f"/api/v1/ai-work/conversations/{parent_id}/children/extra"
        ),
    )
    assert extra_segment_handler.status == 404

    list_handler = FakeHandler()
    assert ai_work_routes.handle_get(
        list_handler,
        urllib.parse.urlparse(
            "/api/v1/ai-work/conversations?include_children=true"
        ),
    )
    assert child.conversation_id in {
        item["conversation_id"]
        for item in list_handler.json_payload["conversations"]
    }


def test_global_agent_projection_rejects_regular_agent_execution_conversation(
    journal,
) -> None:
    execution = journal.start_conversation(
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one"},
    )

    with pytest.raises(ValueError, match="只有全局 Agent 对话"):
        journal.require_global_agent_conversation(execution.conversation_id)
    with pytest.raises(ValueError, match="只有全局 Agent 对话"):
        journal.project_global_agent_event(
            execution.conversation_id,
            "global.user_message",
            {"task_id": "task-1", "message": "你好"},
        )


def test_idle_global_agent_conversation_is_not_marked_interrupted(
    journal,
) -> None:
    conversation_id = journal.start_global_agent_conversation()
    path = journal.find_conversation_path(conversation_id)
    assert path is not None
    old = time.time() - 2 * 60 * 60
    os.utime(path, (old, old))

    summary = next(
        item
        for item in journal.list_conversations()
        if item["conversation_id"] == conversation_id
    )

    assert summary["status"] == "running"


def test_global_agent_projection_compacts_old_display_events(
    journal,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_work_service,
        "MAX_GLOBAL_AGENT_PROJECTION_EVENTS",
        3,
    )
    conversation_id = journal.start_global_agent_conversation()

    for index in range(5):
        journal.project_global_agent_event(
            conversation_id,
            "global.user_message",
            {"task_id": "task-1", "message": f"消息 {index}"},
        )

    events = journal.read_events(conversation_id)
    assert events[0]["type"] == "RUN_STARTED"
    assert [
        event["value"]["message"]
        for event in events[1:]
    ] == ["消息 2", "消息 3", "消息 4"]
    assert [event["seq"] for event in events] == [1, 4, 5, 6]
    record = journal._db.get_ai_session(conversation_id)
    assert record is not None
    assert record["last_seq"] == 6
