from __future__ import annotations

import asyncio

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

from erp_web.services.ai_agent_factory import _record_agent_stream_events


def test_agent_stream_projects_reasoning_and_text_separately() -> None:
    calls: list[tuple[str, str]] = []

    class Recorder:
        def emit_reasoning_delta(self, delta: str) -> None:
            calls.append(("reasoning", delta))

        def finish_reasoning_message(self) -> None:
            calls.append(("reasoning_end", ""))

        def emit_text_delta(self, delta: str) -> None:
            calls.append(("text", delta))

    async def events():
        yield PartStartEvent(index=0, part=ThinkingPart("分析"))
        yield PartDeltaEvent(
            index=0,
            delta=ThinkingPartDelta(content_delta="商品属性"),
        )
        yield PartEndEvent(index=0, part=ThinkingPart("分析商品属性"))
        yield PartStartEvent(index=1, part=TextPart('{"ok":'))
        yield PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="true}"))

    asyncio.run(_record_agent_stream_events(Recorder(), events()))  # type: ignore[arg-type]

    assert calls == [
        ("reasoning", "分析"),
        ("reasoning", "商品属性"),
        ("reasoning_end", ""),
        ("text", '{"ok":'),
        ("text", "true}"),
    ]
