from __future__ import annotations

import json

import pytest

from erp_web.services import ai_gateway


def _sse(events: list[dict]) -> list[bytes]:
    lines = [f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8") for event in events]
    lines.append(b"data: [DONE]\n")
    return lines


def test_responses_stream_ignores_reasoning_and_tool_deltas() -> None:
    stream = _sse(
        [
            {"type": "response.created", "response": {}},
            {"type": "response.reasoning_text.delta", "delta": 'Thinking Process: draft {"title": "x"}'},
            {"type": "response.reasoning_text.done", "text": "Thinking Process: ..."},
            {"type": "response.function_call_arguments.delta", "delta": '{"query":'},
            {"type": "response.output_text.delta", "delta": '{"title": '},
            {"type": "response.output_text.delta", "delta": '"ok"}'},
            {"type": "response.completed", "response": {}},
        ]
    )
    seen: list[str] = []
    text = ai_gateway._read_responses_stream_text(iter(stream), seen.append)
    assert text == '{"title": "ok"}'
    assert "".join(seen) == text
    assert ai_gateway.parse_json_text(text) == {"title": "ok"}


def test_responses_stream_keeps_untyped_chat_chunk_fallback() -> None:
    stream = _sse(
        [
            {"choices": [{"delta": {"content": "hello "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
        ]
    )
    assert ai_gateway._read_responses_stream_text(iter(stream)) == "hello world"


def test_parse_json_text_recovers_trailing_object_after_leaked_thinking() -> None:
    final = {"title": "ok", "bullets": ["a", "b"]}
    text = (
        "Thinking Process:\n\n1. Analyze {braces in prose\n"
        "Draft:\n```json\n" + json.dumps({"title": "draft"}) + "\n```\n"
        "Refine the copy before answering.\n\n" + json.dumps(final)
    )
    assert ai_gateway.parse_json_text(text) == final


def test_parse_json_text_existing_strategies_unchanged() -> None:
    assert ai_gateway.parse_json_text('{"a": 1}') == {"a": 1}
    assert ai_gateway.parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai_gateway.parse_json_text('prefix {"a": 1} suffix') == {"a": 1}
    jsonl = '{"title": "one"}\n{"title": "two"}'
    assert ai_gateway.parse_json_text(jsonl) == {"items": [{"title": "one"}, {"title": "two"}]}


def test_parse_json_text_still_raises_for_plain_prose() -> None:
    with pytest.raises(ValueError):
        ai_gateway.parse_json_text("no json here")
