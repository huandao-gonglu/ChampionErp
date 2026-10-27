"""真实 OpenAI SDK 离线流：推理分类、存储形状、工具、用量及中断。"""

import asyncio
import json

import httpx2 as httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter, PartStartEvent, PartDeltaEvent, TextPart, TextPartDelta,
    ThinkingPart, ToolCallPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from erp_web.services.ai_model_factory import create_pydantic_model_binding
from erp_web.services.ai_tagged_thinking import PinccResponsesModel, TaggedThinkingParser
from tests.test_ai_model_factory import model_config
from tests.test_ai_native_transport import install_network


def configured_model(**overrides):
    return create_pydantic_model_binding(model_config(
        api_style="openai_responses", base_url="https://v2.pincc.ai/v1", model="grok-4.7", **overrides,
    )).model


def response(output):
    return {"id": "resp_test", "object": "response", "created_at": 1, "model": "grok-4.7",
            "status": "completed", "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}


def message(text):
    return {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def events(chunks, *, tool=False, native_reasoning=False):
    output = []
    raw = [{"type": "response.created", "response": {**response([]), "status": "in_progress"}}]
    if native_reasoning:
        reasoning = {"type": "reasoning", "id": "rs_native", "summary": [], "encrypted_content": "signature"}
        raw += [{"type": "response.output_item.added", "output_index": 0, "item": reasoning},
                {"type": "response.reasoning_summary_text.delta", "item_id": "rs_native", "output_index": 0,
                 "summary_index": 0, "delta": "原生推理"}]
        output.append({**reasoning, "summary": [{"type": "summary_text", "text": "原生推理"}]})
        raw.append({"type": "response.output_item.done", "output_index": 0, "item": output[-1]})
    raw.append({"type": "response.output_item.added", "output_index": len(output), "item": message("")})
    for chunk in chunks:
        raw.append({"type": "response.output_text.delta", "item_id": "msg_test", "output_index": len(output),
                    "content_index": 0, "delta": chunk})
    raw.append({"type": "response.output_text.done", "item_id": "msg_test", "output_index": len(output),
                "content_index": 0, "text": "".join(chunks)})
    output.append(message("".join(chunks)))
    if tool:
        item = {"id": "fc_test", "type": "function_call", "call_id": "call_test", "name": "read",
                "arguments": "{}", "status": "completed"}
        raw += [{"type": "response.output_item.added", "output_index": len(output), "item": {**item, "arguments": ""}},
                {"type": "response.function_call_arguments.delta", "item_id": "fc_test", "output_index": len(output), "delta": "{}"}]
        output.append(item)
    raw.append({"type": "response.completed", "response": response(output)})
    return [{**event, "sequence_number": i} for i, event in enumerate(raw)]


def encoded(event):
    return ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()


def mock_stream(monkeypatch, chunks, **options):
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, events(chunks, **options))), request=request)
    install_network(monkeypatch, respond)


def collect(chunks, monkeypatch, **options):
    mock_stream(monkeypatch, chunks, **options)
    async def run():
        model = configured_model()
        async with model:
            async with model.request_stream([], None, ModelRequestParameters()) as stream:
                seen = [event async for event in stream]
                return stream.get(), seen
    return asyncio.run(run())


@pytest.mark.parametrize("chunks", [
    ["<think>内部分析</think>最终答案"],
    list("<think>内部分析</think>最终答案"),
    ["\n<thi", "nk>内部", "分析</th", "ink>最", "终答案"],
    ["<think>", "内部分析", "</think>", "最终答案"],
])
def test_stream_never_emits_reasoning_as_text_and_history_matches(monkeypatch, chunks):
    result, seen = collect(chunks, monkeypatch)
    text = "".join(event.part.content if isinstance(event, PartStartEvent) else event.delta.content_delta
                   for event in seen if (isinstance(event, PartStartEvent) and isinstance(event.part, TextPart))
                   or (isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta)))
    assert text == "最终答案"
    assert [(p.part_kind, p.content) for p in result.parts] == [("thinking", "内部分析"), ("text", "最终答案")]
    assert result.parts[0].id is None
    assert result.usage.input_tokens == 10 and result.usage.output_tokens == 20
    assert result.provider_response_id == "resp_test" and result.state == "complete"
    stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([result]))
    ui = VercelAIAdapter.dump_messages(stored)
    assert [p.type for p in ui[0].parts] == ["reasoning", "text"]


@pytest.mark.parametrize("text", ["普通答案", "例子：<think>文本</think>", "```xml\n<think>示例</think>\n```", "<thing>正常标签"])
def test_ordinary_text_and_code_examples_are_unchanged(monkeypatch, text):
    result, _ = collect(list(text), monkeypatch)
    assert [(p.part_kind, p.content) for p in result.parts] == [("text", text)]


def test_unclosed_reasoning_and_native_tool_call_remain_distinct(monkeypatch):
    result, _ = collect(["<think>内部分析</thi"], monkeypatch, tool=True, native_reasoning=True)
    assert not any(isinstance(part, TextPart) for part in result.parts)
    assert "".join(p.content for p in result.parts if isinstance(p, ThinkingPart)) == "原生推理内部分析</thi"
    original = result.parts[0]
    assert original.id == "rs_native" and original.signature == "signature"
    tool = next(p for p in result.parts if isinstance(p, ToolCallPart))
    assert tool.tool_call_id == "call_test" and tool.tool_name == "read"


def test_nonstream_uses_same_classification_and_preserves_usage(monkeypatch):
    async def respond(request):
        return httpx.Response(200, json=response([message("<think>内部分析</think>最终答案")]), request=request)
    install_network(monkeypatch, respond)
    async def run():
        model = configured_model()
        async with model:
            result = await Agent(model).run("离线测试")
            assert result.output == "最终答案"
            assert result.usage.output_tokens == 20
            assert any(isinstance(p, ThinkingPart) for p in result.all_messages()[-1].parts)
    asyncio.run(run())


def test_factory_limits_adapter_to_verified_endpoint_and_model():
    assert isinstance(configured_model(), PinccResponsesModel)
    for host, model in [("https://api.openai.com/v1", "grok-4.7"),
                        ("https://v2.pincc.ai.invalid/v1", "grok-4.7"),
                        ("https://v2.pincc.ai/v1", "gpt-5")]:
        binding = create_pydantic_model_binding(model_config(api_style="openai_responses", base_url=host, model=model))
        assert not isinstance(binding.model, PinccResponsesModel)


def test_parser_does_not_buffer_entire_reasoning():
    parser = TaggedThinkingParser()
    assert parser.feed("<think>" + "内容" * 10000) == [("thinking", "内容" * 10000)]
    assert len(parser.pending) <= 7


def test_final_text_retains_phase_and_late_citation_metadata(monkeypatch):
    annotation = {"type": "url_citation", "start_index": 0, "end_index": 2,
                  "title": "引用资料", "url": "https://example.com/source"}
    raw = events(["<thi", "nk>分析</think>答案"])
    raw[1]["item"]["phase"] = "final_answer"
    raw.insert(-2, {"type": "response.output_text.annotation.added", "item_id": "msg_test",
                    "output_index": 0, "content_index": 0, "annotation_index": 0,
                    "annotation": annotation, "sequence_number": 4})
    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, raw)), request=request)
    install_network(monkeypatch, respond)
    async def run():
        model = configured_model()
        async with model:
            async with model.request_stream([], {"openai_include_raw_annotations": True}, ModelRequestParameters()) as stream:
                async for _ in stream:
                    pass
                text = stream.get().parts[-1]
                assert text.content == "答案" and text.id == "msg_test"
                assert text.provider_details == {"phase": "final_answer", "annotations": [annotation]}
    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_unclosed_final_answer_reports_format_error_without_retry(monkeypatch, streaming):
    calls = []
    async def respond(request):
        calls.append(request)
        if streaming:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=b"".join(map(encoded, events(["<think>没有分界的内容"]))), request=request)
        return httpx.Response(200, json=response([message("<think>没有分界的内容")]), request=request)
    install_network(monkeypatch, respond)
    async def run():
        model = configured_model()
        async with model:
            agent = Agent(model)
            with pytest.raises(UnexpectedModelBehavior, match="推理标签未闭合") as caught:
                if streaming:
                    async with agent.run_stream_events("离线测试") as stream:
                        async for _ in stream:
                            pass
                else:
                    await agent.run("离线测试")
            assert "没有分界的内容" not in str(caught.value)
    asyncio.run(run())
    assert len(calls) == 1


def test_native_agent_executes_tool_once_and_replays_classified_history(monkeypatch):
    requests, executed = [], []
    async def respond(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        chunks = ["<think>需要读取资料"] if first else ["<think>已读取</think>完成"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, events(chunks, tool=first))), request=request)
    install_network(monkeypatch, respond)
    async def run():
        model = configured_model()
        agent = Agent(model)
        @agent.tool_plain
        def read() -> str:
            executed.append("读取")
            return "资料"
        async with model:
            async with agent.run_stream_events("读取并汇报") as stream:
                received = [event async for event in stream]
            result = received[-1].result
            assert result.output == "完成"
            messages = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(result.all_messages()))
            responses = [item for item in messages if item.kind == "response"]
            assert [p.part_kind for p in responses[0].parts] == ["thinking", "tool-call"]
            assert [p.part_kind for p in responses[1].parts] == ["thinking", "text"]
    asyncio.run(run())
    assert executed == ["读取"] and len(requests) == 2
    # 回传形式仍由原生 Responses mapper 负责；本地拆出的推理没有虚构 reasoning ID。
    assert any(item.get("type") == "function_call_output" and item.get("call_id") == "call_test"
               for item in requests[1]["input"])
    assert not any(item.get("type") == "reasoning" for item in requests[1]["input"])


@pytest.mark.parametrize("cancel", [False, True])
def test_interruption_preserves_partial_thinking_without_replay(monkeypatch, cancel):
    calls, snapshots, seen = [], [], []
    async def run():
        waiting = asyncio.Event()
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for event in events(["<think>部分推理</thi"])[:-2]:
                    yield encoded(event)
                waiting.set()
                if cancel:
                    await asyncio.Event().wait()
                raise httpx.ReadError("离线模拟断流")
        async def respond(request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body(), request=request)
        install_network(monkeypatch, respond)
        model = configured_model()
        async def consume():
            async with model.request_stream([], None, ModelRequestParameters()) as stream:
                try:
                    async for event in stream:
                        seen.append(event)
                finally:
                    snapshots.append(stream.get())
        async with model:
            task = asyncio.create_task(consume())
            await asyncio.wait_for(waiting.wait(), timeout=5)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else ModelAPIError):
                await task
    asyncio.run(run())
    assert len(calls) == 1
    assert [(p.part_kind, p.content) for p in snapshots[0].parts] == [("thinking", "部分推理</thi")]
    assert not any(isinstance(event, PartStartEvent) and isinstance(event.part, TextPart) for event in seen)
