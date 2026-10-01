"""通过原生 SDK 和真实流迭代验证审计终态，不访问外网。"""
import asyncio
from collections import Counter
import json

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
import pytest

from erp_web.context import get_context
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.external_httpx_transport import ManagedAsyncTransport


class CompletionStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        chunk = {
            "id": "offline-completion", "object": "chat.completion.chunk",
            "created": 0, "model": "offline",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "完成"},
                         "finish_reason": None}],
        }
        yield ("data: " + json.dumps(chunk) + "\n\n").encode()
        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        yield ("data: " + json.dumps(chunk) + "\n\n").encode()
        # 原生 SDK 在这里退出，底层 HTTP 迭代器尚未到达 EOF。
        yield b"data: [DO"
        yield b"NE]\n\n"


def client_for(stream_factory):
    def respond(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            stream=stream_factory(),
        )

    return httpx.AsyncClient(transport=ManagedAsyncTransport(
        platform="ai:test", account_id="offline",
        transport_factory=lambda: httpx.MockTransport(respond),
    ))


@pytest.mark.parametrize("consumer", ["sdk", "pydantic"])
def test_native_completion_and_generator_cleanup_never_trip_breaker(consumer, monkeypatch):
    store = get_context().external_requests.store
    finished = []
    original_finish = store.finish

    def finish(request_id, **kwargs):
        finished.append(request_id)
        return original_finish(request_id, **kwargs)

    monkeypatch.setattr(store, "finish", finish)

    async def invoke():
        async with client_for(CompletionStream) as http_client:
            sdk = AsyncOpenAI(
                api_key="offline-placeholder", base_url="https://offline.invalid/v1",
                http_client=http_client, max_retries=0,
            )
            if consumer == "sdk":
                stream = await sdk.chat.completions.create(
                    model="offline", messages=[{"role": "user", "content": "测试"}],
                    stream=True,
                )
                async with stream:
                    text = "".join([
                        chunk.choices[0].delta.content or "" async for chunk in stream
                    ])
                assert text == "完成"
            else:
                model = OpenAIChatModel("offline", provider=OpenAIProvider(openai_client=sdk))
                async with Agent(model).run_stream("测试") as result:
                    assert await result.get_output() == "完成"

    for _ in range(6):
        # 同时验证 aclose 之后的异步生成器清理，旧实现此时会重复记录网络故障。
        asyncio.run(invoke())

    rows = store.query()["items"]
    assert len(rows) == 6
    assert all(row["sent"] == 1 and row["decision"] == "allowed" for row in rows)
    assert all(row["result"] == {"http_status": 200, "outcome": "stream_closed"} for row in rows)
    assert all(row["completed"] and row["lease_until"] == 0 for row in rows)
    assert Counter(finished) == Counter({row["id"]: 1 for row in rows})
    assert store.blocks() == []


@pytest.mark.parametrize("failure_type", [httpx.ReadError, httpx.ReadTimeout])
def test_real_stream_failures_still_block_after_five_attempts(failure_type):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: partial\n\n"
            raise failure_type("离线模拟网络故障")

    async def invoke():
        async with client_for(BrokenStream) as client:
            async with client.stream("POST", "https://offline.invalid/v1/chat/completions") as response:
                await response.aread()

    for _ in range(5):
        with pytest.raises(failure_type):
            asyncio.run(invoke())
    with pytest.raises(ExternalRequestBlocked, match="连续失败"):
        asyncio.run(invoke())

    rows = get_context().external_requests.store.query()["items"]
    assert rows[0]["decision"] == "rejected" and rows[0]["sent"] == 0
    assert rows[0]["result"]["code"] == "EXTERNAL_REPEATED_FAILURE"
    assert all(row["result"]["outcome"] == "outcome_unknown" for row in rows[1:])
    assert all(row["result"]["cancelled"] is False for row in rows[1:])


def test_cancelled_read_remains_unknown_without_counting_as_network_failure():
    class CancelledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: partial\n\n"
            raise asyncio.CancelledError()

    async def invoke():
        async with client_for(CancelledStream) as client:
            async with client.stream("POST", "https://offline.invalid/v1/chat/completions") as response:
                await response.aread()

    for _ in range(6):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(invoke())

    store = get_context().external_requests.store
    rows = store.query()["items"]
    assert len(rows) == 6
    assert all(row["result"]["outcome"] == "outcome_unknown" for row in rows)
    assert all(row["result"]["cancelled"] is True for row in rows)
    assert all(block["scope"] == "request" for block in store.blocks())


def test_caller_closing_partial_stream_releases_resources_once():
    closed = []

    class PartialStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: partial\n\n"
            pytest.fail("调用方关闭后不得继续读取响应")

        async def aclose(self):
            closed.append("response")

    async def invoke():
        async with client_for(PartialStream) as client:
            async with client.stream("POST", "https://offline.invalid/v1/chat/completions") as response:
                iterator = response.aiter_bytes()
                assert await anext(iterator) == b"data: partial\n\n"
                await response.aclose()
                await response.aclose()
                await iterator.aclose()

    asyncio.run(invoke())
    store = get_context().external_requests.store
    row = store.query()["items"][0]
    assert row["result"] == {"http_status": 200, "outcome": "stream_closed"}
    assert row["lease_until"] == 0
    assert store.blocks() == []
    assert closed == ["response"]
