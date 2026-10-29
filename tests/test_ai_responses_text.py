"""真实 SDK 离线回归：正文标签不改变执行、原生推理、工具或历史。"""

import asyncio
import json

import httpx2 as httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter, ThinkingPart, ToolCallPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from erp_web.services.ai_model_factory import create_pydantic_model_binding
from pydantic_ai.models.openai import OpenAIResponsesModel
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


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("original", [
    "<think>分析过程。Final response in Chinese.没有提交库存修改。",
    "<think>分析过程</thi",
    "<think>分析过程</think>正常答复",
    "正常答复",
])
def test_text_tags_do_not_drop_answer_fail_run_or_trigger_retry(monkeypatch, streaming, original):
    calls = []
    async def respond(request):
        calls.append(request)
        if streaming:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=b"".join(map(encoded, events(list(original)))), request=request)
        return httpx.Response(200, json=response([message(original)]), request=request)
    install_network(monkeypatch, respond)
    async def run():
        model = configured_model()
        assert isinstance(model, OpenAIResponsesModel)
        async with model:
            agent = Agent(model)
            if streaming:
                async with agent.run_stream_events("离线测试") as stream:
                    received = [event async for event in stream]
                result = received[-1].result
            else:
                result = await agent.run("离线测试")
            assert result.output == original
            assert result.usage.output_tokens == 20
            stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(result.all_messages()))
            answer = stored[-1]
            assert answer.state == "complete" and answer.provider_response_id == "resp_test"
            assert len(answer.parts) == 1 and answer.parts[0].content == original
            assert VercelAIAdapter.dump_messages(stored)[-1].parts[0].text == original
    asyncio.run(run())
    assert len(calls) == 1


def test_native_tool_executes_once_even_when_final_reply_has_unclosed_tag(monkeypatch):
    calls, executed = [], []
    final = "<think>总结已执行回执。Final answer.已经读取资料。"
    async def respond(request):
        calls.append(json.loads(request.content))
        first = len(calls) == 1
        chunks = ["<think>需要读取资料"] if first else [final]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, events(chunks, tool=first, native_reasoning=first))), request=request)
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
            assert result.output == final
            messages = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(result.all_messages()))
            responses = [item for item in messages if item.kind == "response"]
            thinking = next(p for p in responses[0].parts if isinstance(p, ThinkingPart))
            assert thinking.content == "原生推理" and thinking.id == "rs_native" and thinking.signature == "signature"
            tool = next(p for p in responses[0].parts if isinstance(p, ToolCallPart))
            assert tool.tool_call_id == "call_test"
            assert [(p.part_kind, p.content) for p in responses[1].parts] == [("text", final)]
    asyncio.run(run())
    assert executed == ["读取"] and len(calls) == 2
    assert any(item.get("type") == "function_call_output" and item.get("call_id") == "call_test"
               for item in calls[1]["input"])


def test_factory_uses_native_responses_model_for_all_endpoints():
    for host, model in [("https://api.openai.com/v1", "grok-4.7"),
                        ("https://v2.pincc.ai/v1", "grok-4.7"),
                        ("https://v2.pincc.ai/v1", "gpt-5")]:
        binding = create_pydantic_model_binding(model_config(api_style="openai_responses", base_url=host, model=model))
        assert isinstance(binding.model, OpenAIResponsesModel)


@pytest.mark.parametrize("cancel", [False, True])
def test_interruption_preserves_original_text_without_replay(monkeypatch, cancel):
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
    assert [(p.part_kind, p.content) for p in snapshots[0].parts] == [("text", "<think>部分推理</thi")]
    assert snapshots[0].state != "complete"
