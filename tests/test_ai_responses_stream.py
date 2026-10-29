"""实际 SDK 的离线 SSE 回放：生成错误不得变成正常完成，也不得重放工具。"""
import asyncio

import httpx2 as httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import ModelRequestParameters

from tests.test_ai_native_transport import install_network
from tests.test_ai_responses_text import configured_model, encoded, events, response
from tests.test_native_agent_integration import service, body, CONVERSATION


def broken_events(kind="error"):
    # 与故障一致：承诺执行、连续空白，然后 error；没有 response.completed。
    raw = events(["我先处理。", *([" \n"] * 4090)])[:-2]
    if kind == "error":
        raw.append({"type": "error", "code": None, "message": "Internal error during token generation",
                    "param": None, "sequence_number": 4122})
    elif kind == "failed":
        raw.append({"type": "response.failed", "sequence_number": 4122, "response": {
            **response([]), "status": "failed", "error": {"code": "server_error", "message": "生成中断"}}})
    return raw


@pytest.mark.parametrize("kind", ["error", "failed", "eof"])
def test_stream_failure_preserves_partial_text_without_result_or_replay(monkeypatch, kind):
    calls, snapshots, received = [], [], []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, broken_events(kind))), request=request)

    install_network(monkeypatch, respond)

    async def run():
        model = configured_model()
        async with model:
            with pytest.raises(ModelAPIError, match="模型"):
                async with model.request_stream([], None, ModelRequestParameters()) as stream:
                    try:
                        async for event in stream:
                            received.append(event)
                    finally:
                        snapshots.append(stream.get())
    asyncio.run(run())
    assert len(calls) == 1 and received
    assert snapshots[0].state != "complete"
    assert snapshots[0].parts[0].content == "我先处理。" + " \n" * 4090


def test_production_ui_marks_generation_error_failed_and_preserves_history(monkeypatch, tmp_path):
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, broken_events())), request=request)

    install_network(monkeypatch, respond)
    model = configured_model()
    ui = service(tmp_path, model)
    chunks = []
    asyncio.run(ui.prepare_run(body("处理这些 SKU")).stream(chunks.append))
    detail = ui.dump_ui_messages(CONVERSATION)
    assert detail["run_status"] == "failed"
    assert "Internal error during token generation" in detail["run_error"]["message"]
    assert b'"type":"error"' in b"".join(chunks)
    history = ui.chat_service.trusted_history(CONVERSATION)
    assert history[-1].state != "complete"
    assert history[-1].parts[0].content.endswith(" \n" * 4090)
    assert not ui.call_store.pending(CONVERSATION) and len(calls) == 1


def test_failure_after_completed_tool_does_not_execute_it_again(monkeypatch):
    calls, executions = [], []

    async def respond(request):
        calls.append(request)
        raw = events(["开始读取"], tool=True) if len(calls) == 1 else broken_events()
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, raw)), request=request)
    install_network(monkeypatch, respond)

    async def run():
        model = configured_model()
        agent = Agent(model)

        @agent.tool_plain
        def read() -> str:
            executions.append(1)
            return "资料"

        async with model:
            with pytest.raises(ModelAPIError):
                async with agent.run_stream_events("读取资料") as stream:
                    async for _ in stream:
                        pass
    asyncio.run(run())
    assert len(calls) == 2 and executions == [1]


def test_tool_in_failed_response_is_not_dispatched(monkeypatch):
    executions, calls = [], []
    raw = events(["准备读取"], tool=True)[:-1] + [broken_events()[-1]]

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, raw)), request=request)
    install_network(monkeypatch, respond)

    async def run():
        model = configured_model()
        agent = Agent(model)

        @agent.tool_plain
        def read() -> str:
            executions.append(1)
            return "不应执行"

        async with model:
            with pytest.raises(ModelAPIError):
                async with agent.run_stream_events("读取资料") as stream:
                    async for _ in stream:
                        pass
    asyncio.run(run())
    assert not executions and len(calls) == 1


def test_native_background_stream_can_suspend_without_terminal_event(monkeypatch):
    raw = [{"type": "response.created", "sequence_number": 0,
            "response": {**response([]), "status": "queued", "background": True}}]

    async def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(map(encoded, raw)), request=request)
    install_network(monkeypatch, respond)

    async def run():
        model = configured_model()
        async with model:
            async with model.request_stream([], None, ModelRequestParameters()) as stream:
                async for _ in stream:
                    pass
                assert stream.get().state == "suspended"
    asyncio.run(run())
