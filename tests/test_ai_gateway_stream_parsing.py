from __future__ import annotations

import json

import pytest

from erp_web.services import ai_gateway


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
