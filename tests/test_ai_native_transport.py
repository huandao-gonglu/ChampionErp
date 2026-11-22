"""原生 Provider/SDK 连接恢复与业务请求隔离，不访问外网。"""
import asyncio
import gzip
import json

import httpx2 as httpx
from openai import APIConnectionError
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
import pytest

from erp_web.context import get_context
from erp_web.schemas.external_requests import RequestContext, RequestFailure
from erp_web.services.ai_agent_factory import _safe_agent_error
from erp_web.services.ai_model_factory import create_pydantic_model_binding
from tests.test_ai_model_factory import model_config


def completion(content="完成", *, tool=False):
    message = {"role": "assistant", "content": content}
    if tool:
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "save-once", "type": "function", "function": {"name": "save", "arguments": "{}"}},
        ]}
    return {"id": "offline", "object": "chat.completion", "created": 0, "model": "offline",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}]}


def install_network(monkeypatch, respond):
    async def handle(transport, request):
        return await respond(request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    # 保留 SDK 自己的重试判断和次数；离线测试无需等待退避时间。
    monkeypatch.setattr("openai.AsyncOpenAI._calculate_retry_timeout", lambda *a, **kw: 0)


@pytest.mark.parametrize("provider_id,base_url", [
    ("openai", "https://offline.invalid/v1"),
    ("deepseek", "https://api.deepseek.com"),
    ("alibaba", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("omlx", "http://127.0.0.1:8000/v1"),
])
def test_native_retries_ignore_business_blocks_and_never_write_request_audit(monkeypatch, provider_id, base_url):
    manager = get_context().external_requests
    context = RequestContext(platform="ai:" + provider_id, account_id="old-account", interface="/chat/completions", source="历史记录")
    manager.limiter.record_failure(context, "hist-1", RequestFailure("EXTERNAL_ACCOUNT_DISABLED", "历史阻断", "account"), sent_at=0.0)
    manager.limiter.set_limit(context.platform, concurrency=1, rps=1.0)
    for method in ("start", "result", "network_error"):
        monkeypatch.setattr(manager, method, lambda *a, **kw: pytest.fail("AI 推理不应接入业务请求管理器"))
    calls = []

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if len(calls) == 2:
            return httpx.Response(429, json={"error": {"message": "稍后重试"}}, request=request)
        return httpx.Response(200, json=completion(), request=request)

    install_network(monkeypatch, respond)

    async def invoke():
        binding = create_pydantic_model_binding(model_config(provider_id=provider_id, base_url=base_url))
        async with binding.model:
            result = await Agent(binding.model, model_settings=binding.model_settings).run("测试连接")
            assert result.output == "完成"
    asyncio.run(invoke())
    assert len(calls) == 3
    assert manager.store.query()["items"] == []


def test_exhausted_native_retry_budget_does_not_block_next_model_request(monkeypatch):
    calls = []
    async def respond(request):
        calls.append(request)
        if len(calls) <= 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json=completion(), request=request)
    install_network(monkeypatch, respond)

    async def invoke():
        binding = create_pydantic_model_binding(model_config())
        async with binding.model:
            sdk = binding.model.client
            with pytest.raises(APIConnectionError):
                await sdk.chat.completions.create(model="offline", messages=[{"role": "user", "content": "测试"}])
            assert len(calls) == 3
            result = await sdk.chat.completions.create(model="offline", messages=[{"role": "user", "content": "再试"}])
            assert result.choices[0].message.content == "完成"
    asyncio.run(invoke())
    assert len(calls) == 4
    assert get_context().external_requests.store.query()["items"] == []


def test_responses_api_recovers_before_response_headers(monkeypatch):
    calls = []
    async def respond(request):
        calls.append(request)
        assert request.url.path.endswith("/responses")
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={
            "id": "resp_offline", "object": "response", "created_at": 0,
            "model": "offline", "status": "completed",
            "output": [{"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "已恢复", "annotations": []}]}],
        }, request=request)
    install_network(monkeypatch, respond)
    async def invoke():
        binding = create_pydantic_model_binding(model_config(api_style="openai_responses"))
        async with binding.model:
            assert (await Agent(binding.model).run("测试 Responses 连接")).output == "已恢复"
    asyncio.run(invoke())
    assert len(calls) == 2


def test_interrupted_stream_does_not_replay_already_returned_text(monkeypatch):
    calls = []
    received = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunk = {"id": "offline", "object": "chat.completion.chunk", "created": 0, "model": "offline",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "开始"}, "finish_reason": None}]}
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            raise httpx.ReadError("流已中断")
    async def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body(), request=request)
    install_network(monkeypatch, respond)
    async def invoke():
        binding = create_pydantic_model_binding(model_config())
        async with binding.model:
            stream = await binding.model.client.chat.completions.create(
                model="offline", messages=[{"role": "user", "content": "测试流中断"}], stream=True,
            )
            async with stream:
                with pytest.raises(APIConnectionError) as caught:
                    async for chunk in stream:
                        received.append(chunk.choices[0].delta.content)
                assert isinstance(caught.value.__cause__, httpx.ReadError)
    asyncio.run(invoke())
    assert received == ["开始"] and len(calls) == 1


def test_retrying_next_model_request_does_not_repeat_completed_business_tool(monkeypatch):
    calls = []
    saved = []
    async def respond(request):
        calls.append(request)
        if len(calls) == 2:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json=completion(tool=len(calls) == 1), request=request)
    install_network(monkeypatch, respond)

    async def invoke():
        binding = create_pydantic_model_binding(model_config())
        agent = Agent(binding.model)
        @agent.tool_plain
        def save() -> str:
            saved.append("已保存")
            return "已保存"
        async with binding.model:
            assert (await agent.run("保存一次并汇报")).output == "完成"
    asyncio.run(invoke())
    assert len(calls) == 3 and saved == ["已保存"]


def test_cancellation_during_native_request_stops_without_retry(monkeypatch):
    calls = []
    async def invoke():
        started = asyncio.Event()
        async def respond(request):
            calls.append(request)
            started.set()
            await asyncio.Event().wait()
        install_network(monkeypatch, respond)
        binding = create_pydantic_model_binding(model_config())
        async with binding.model:
            task = asyncio.create_task(Agent(binding.model).run("测试取消"))
            await asyncio.wait_for(started.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(invoke())
    assert len(calls) == 1


def test_native_compressed_payment_error_preserves_http_semantics(monkeypatch):
    calls = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(json.dumps({"error": {"message": "Insufficient Balance"}}).encode())
    async def respond(request):
        calls.append(request)
        return httpx.Response(402, headers={"content-encoding": "gzip", "content-type": "application/json"},
                              stream=Body(), request=request)
    install_network(monkeypatch, respond)
    async def invoke():
        binding = create_pydantic_model_binding(model_config())
        async with binding.model:
            with pytest.raises(ModelHTTPError) as caught:
                await Agent(binding.model).run("测试余额不足")
        error = _safe_agent_error(caught.value, validator=None, conversation_id="offline", task_run_id="offline")
        assert error.code == "AI_PROVIDER_PAYMENT_REQUIRED" and not error.retryable
        assert "HTTP 402" in str(error)
    asyncio.run(invoke())
    assert len(calls) == 1
