from __future__ import annotations

import json
from pathlib import Path
import threading
import urllib.parse

from erp_web.http_route_units import ai_work_routes
from erp_web.services import ai_gateway, ai_work_service


def _chat_config(stream: bool = False) -> dict:
    return {
        "ai_models": [
            {
                "id": "chat_model",
                "provider": "OpenAI-Compatible",
                "api_key": "test-key",
                "base_url": "https://ai.example.com/v1",
                "model": "test-model",
                "capabilities": ["chat", "json"],
                "extra": {"request_body": {"stream": stream}},
            }
        ]
    }


def test_each_ai_work_conversation_uses_an_independent_jsonl(tmp_path: Path) -> None:
    first = ai_work_service.start_conversation(
        tmp_path,
        use_case_id="copy.generate",
        capability="chat_json",
        provider_id="fake",
        model={"id": "one", "model": "model-one"},
    )
    second = ai_work_service.start_conversation(
        tmp_path,
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
    assert {path.name for path in first.path.parent.glob("*.jsonl")} == {
        f"{first.conversation_id}.jsonl",
        f"{second.conversation_id}.jsonl",
    }
    first_events = ai_work_service.load_conversation_events(tmp_path, first.conversation_id)
    second_events = ai_work_service.load_conversation_events(tmp_path, second.conversation_id)
    assert [event["seq"] for event in first_events] == list(range(1, len(first_events) + 1))
    assert [event.get("delta") for event in first_events if event["type"] == "TEXT_MESSAGE_CONTENT"] == ["first"]
    assert [event.get("delta") for event in second_events if event["type"] == "TEXT_MESSAGE_CONTENT"] == ["second"]


def test_ai_work_wait_returns_only_incremental_events(tmp_path: Path) -> None:
    conversation = ai_work_service.start_conversation(
        tmp_path,
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
    events = ai_work_service.wait_for_conversation_events(
        tmp_path,
        conversation.conversation_id,
        after_seq=1,
        wait_ms=1_000,
    )
    if not any(event["type"] == "TEXT_MESSAGE_CONTENT" for event in events):
        events.extend(
            ai_work_service.wait_for_conversation_events(
                tmp_path,
                conversation.conversation_id,
                after_seq=int(events[-1]["seq"]),
                wait_ms=1_000,
            )
        )
    worker.join(timeout=1)

    assert [event["type"] for event in events] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert events[-1]["delta"] == "delta"


def test_ai_work_wait_rechecks_events_before_blocking(tmp_path: Path, monkeypatch) -> None:
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

    monkeypatch.setattr(
        ai_work_service,
        "load_conversation_events",
        lambda app_dir, conversation_id, after_seq=0: next(reads),
    )
    monkeypatch.setattr(ai_work_service, "_condition_for", lambda conversation_id: FakeCondition())

    events = ai_work_service.wait_for_conversation_events(
        tmp_path,
        "conversation_1",
        after_seq=1,
        wait_ms=1_000,
    )

    assert events == [event]


def test_chat_json_records_actual_provider_request_and_response(tmp_path: Path, monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"choices":[{"message":{"content":"{\\"title\\":\\"Hola\\"}"}}]}'

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", lambda request, timeout: FakeResponse())
    messages = [
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": "Localize this product."},
    ]

    result = ai_gateway.chat_json(tmp_path, _chat_config(), "copy.generate", messages)

    assert result == {"title": "Hola"}
    conversations = ai_work_service.list_conversations(tmp_path)
    assert len(conversations) == 1
    events = ai_work_service.load_conversation_events(tmp_path, conversations[0]["conversation_id"])
    request_event = next(event for event in events if event.get("name") == "provider.request")
    request_value = request_event["value"]
    assert request_value["messages"] == messages
    assert request_value["provider_payload"]["model"] == "test-model"
    assert request_value["provider_payload"]["messages"] == messages
    assert [event.get("delta") for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"] == [
        '{"title":"Hola"}'
    ]
    result_event = next(event for event in events if event.get("name") == "business.result")
    assert result_event["value"]["parsed"] == {"title": "Hola"}
    assert events[-1]["type"] == "RUN_FINISHED"
    for line in (tmp_path / ai_work_service.AI_WORK_RELATIVE_DIR).glob("*/*.jsonl"):
        for raw_line in line.read_text(encoding="utf-8").splitlines():
            assert isinstance(json.loads(raw_line), dict)


def test_streaming_chat_appends_provider_deltas_in_order(tmp_path: Path, monkeypatch) -> None:
    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"{\\"title\\":"}}]}\n',
                    b'data: {"choices":[{"delta":{"content":"\\"Hola\\"}"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", lambda request, timeout: FakeStreamResponse())

    result = ai_gateway.chat_json(
        tmp_path,
        _chat_config(stream=True),
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
        stream=True,
    )

    assert result == {"title": "Hola"}
    conversation = ai_work_service.list_conversations(tmp_path)[0]
    events = ai_work_service.load_conversation_events(tmp_path, conversation["conversation_id"])
    assert [event.get("delta") for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"] == [
        '{"title":',
        '"Hola"}',
    ]
    assert events[-1]["type"] == "RUN_FINISHED"


def test_ai_work_routes_list_and_incrementally_read_conversations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ai_work_routes, "APP_DIR", tmp_path)
    conversation = ai_work_service.start_conversation(
        tmp_path,
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
