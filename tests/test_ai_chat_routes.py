from __future__ import annotations

import asyncio
import gc
import json
import socket
import threading
import time
import warnings
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import http.client
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.settings import ModelSettings

from erp_web.ai_capability_composition import GLOBAL_CHAT_DIRECT_CAPABILITIES
from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.runtime_units.global_ai_control_tools import (
    GLOBAL_TASK_CONTROL_CATALOG,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
from erp_web.services.vercel_ai_ui_service import VERCEL_SDK_VERSION


CHAT_PATH = "/api/v1/ai-chat/runs"
CONVERSATION = "conversation_global_chat_" + "f" * 32

#: global.chat 主 Agent 工具 = Direct 只读能力 + 任务控制能力（动态同源）。
EXPECTED_GLOBAL_CHAT_TOOLS = set(GLOBAL_CHAT_DIRECT_CAPABILITIES) | set(
    GLOBAL_TASK_CONTROL_CATALOG.tools
)


def test_global_chat_profile_supports_bounded_multi_step_task_observation() -> None:
    assert GLOBAL_CHAT_PROFILE.max_model_requests == 16
    assert GLOBAL_CHAT_PROFILE.max_tool_calls == 12


def _submit_body(conversation_id: str, message_id: str, text: str) -> dict:
    return {
        "trigger": "submit-message",
        "id": conversation_id,
        "messages": [
            {
                "id": message_id,
                "role": "user",
                "parts": [{"type": "text", "text": text}],
            }
        ],
    }


def _post(
    port: int,
    path: str,
    payload: dict | None,
    *,
    content_type: str = "application/json",
    raw: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        body = raw if raw is not None else json.dumps(payload or {})
        conn.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": content_type},
        )
        response = conn.getresponse()
        data = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers, data
    finally:
        conn.close()


def _get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class _BlockingModel(TestModel):
    """在模型请求阶段阻塞，等待 gate 释放；用于稳定复现并发持锁。"""

    def __init__(self, gate: threading.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._gate = gate

    def _request(self, *args: Any, **kwargs: Any) -> Any:
        self._gate.wait(timeout=10)
        return super()._request(*args, **kwargs)


class _FailingModel(TestModel):
    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelAPIError("test-model", "provider failure")


class _PaymentRequiredModel(TestModel):
    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelHTTPError(
            402,
            "test-model",
            {"error": {"code": "invalid_request_error", "message": "余额不足"}},
        )


class _SecretLeakModel(TestModel):
    """Provider 原始错误中带敏感信息，验证 SSE 不泄漏。"""

    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelAPIError(
            "test-model",
            "upstream rejected api_key=SUPERSECRETKEY value "
            "and token Bearer abcdef123456.xyz and key sk-live0123456789abcdef",
        )


def _patch_chat_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 GlobalAgentChatService 使用的 factory 换成可替换 TestModel。"""

    from erp_web.services import ai_agent_factory as factory_module
    from erp_web.services import global_agent_chat_service as chat_module

    holder: dict[str, Any] = {
        "model": TestModel(custom_output_text="这是全局对话回复。", call_tools=[])
    }
    real_factory = factory_module.AiAgentFactory

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        return PydanticModelBinding(
            model=holder["model"],
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    def patched_factory(**kwargs: Any) -> Any:
        kwargs.setdefault("model_binding_factory", binding)
        return real_factory(**kwargs)

    monkeypatch.setattr(chat_module, "AiAgentFactory", patched_factory)
    return holder


@pytest.fixture()
def chat_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """进程内 ThreadingHTTPServer + 可替换 TestModel 的聊天服务。"""

    holder = _patch_chat_model(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"port": server.server_address[1], "model": holder}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chat_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """不经过 HTTP 的 VercelAiUiService，便于测试流生命周期与断连。"""

    from erp_web.facades.global_task_facade import build_global_chat_toolset
    from erp_web.services.global_agent_chat_service import (
        GlobalAgentChatService,
    )
    from erp_web.services.vercel_ai_ui_service import VercelAiUiService

    holder = _patch_chat_model(monkeypatch)
    context = get_context()
    agent_chat = GlobalAgentChatService(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=context.pydantic_messages,
        toolset=build_global_chat_toolset(context),
    )
    service = VercelAiUiService(
        chat_service=agent_chat,
        claim_store=context.chat_turn_claims,
        run_registry=context.chat_runs,
        deferred_links=context.deferred_task_links,
        event_outbox=context.ai_event_outbox,
        event_bus=context.conversation_event_bus,
    )
    return {"service": service, "model": holder}


def test_sse_run_streams_official_chunks_and_persists_history(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    status, headers, data = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "你好")
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["cache-control"] == "no-store"
    assert headers["connection"] == "close"
    assert headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert "content-length" not in headers

    text = data.decode("utf-8")
    chunks = [
        line[len("data: ") :]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    assert chunks[0] == '{"type":"start"}'
    assert chunks[-1] == "[DONE]"
    delta_chunks = [
        json.loads(chunk)
        for chunk in chunks
        if chunk != "[DONE]" and json.loads(chunk).get("type") == "text-delta"
    ]
    assert delta_chunks
    streamed_text = "".join(chunk["delta"] for chunk in delta_chunks)
    assert streamed_text == "这是全局对话回复。"
    assert any('"type":"finish"' in chunk for chunk in chunks)
    # 官方 SSE 之外不允许出现项目自定义 JSON 事件。
    assert all(
        chunk == "[DONE]" or json.loads(chunk).get("type")
        for chunk in chunks
    )

    history = get_context().pydantic_messages.get(CONVERSATION)
    assert history is not None
    # 完成后的历史仍可由官方 adapter 恢复。
    assert history.model_messages() == ModelMessagesTypeAdapter.validate_json(
        history.messages_json
    )


def test_sse_run_streams_official_tool_chunks(
    chat_server: dict[str, Any],
) -> None:
    turns = 0

    async def model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            # global.chat 主 Agent 暴露 Direct 只读能力 + 任务控制能力。
            assert (
                {tool.name for tool in agent_info.function_tools}
                == EXPECTED_GLOBAL_CHAT_TOOLS
            )
            yield {
                0: DeltaThinkingPart(content="先查询当前草稿。"),
                1: DeltaToolCall(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="draft-query-1",
                ),
            }
            return
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert returns[-1].tool_call_id == "draft-query-1"
        yield "草稿查询完成。"

    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    conversation = "conversation_global_chat_" + "7" * 32

    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(conversation, "tool-stream-1", "查询当前草稿"),
    )

    assert status == 200
    chunks = [
        json.loads(payload)
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ")
        and (payload := line[len("data: ") :]) != "[DONE]"
    ]
    chunk_types = {chunk["type"] for chunk in chunks}
    assert "reasoning-delta" in chunk_types
    assert "tool-input-available" in chunk_types
    assert "tool-output-available" in chunk_types
    assert "finish" in chunk_types


def test_second_turn_reuses_trusted_history_without_duplicates(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]

    def user_texts() -> list[str]:
        history = get_context().pydantic_messages.get(CONVERSATION)
        texts: list[str] = []
        for message in history.model_messages():
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(
                        part.content, str
                    ):
                        texts.append(part.content)
        return texts

    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "第一轮"))
    assert user_texts() == ["第一轮"]

    # 客户端即使重复携带历史，服务端也只信任自己的历史并只追加本轮消息。
    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-2", "第二轮"))
    assert user_texts() == ["第一轮", "第二轮"]


def test_resending_same_client_message_id_is_idempotent(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    body = _submit_body(CONVERSATION, "ui-dup", "幂等测试")

    status_first, _, _ = _post(port, CHAT_PATH, body)
    assert status_first == 200
    history_after_first = get_context().pydantic_messages.get(CONVERSATION)

    status_second, _, data_second = _post(port, CHAT_PATH, body)
    payload = json.loads(data_second.decode("utf-8"))
    assert status_second == 409
    assert payload["error_code"] == "AI_CHAT_TURN_ALREADY_ACCEPTED"

    # 重复领取不会再次运行 Agent，也不新增第二个 user turn。
    history_after_second = get_context().pydantic_messages.get(CONVERSATION)
    assert (
        history_after_second.messages_json
        == history_after_first.messages_json
    )


def test_same_conversation_concurrent_run_is_rejected(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    gate = threading.Event()
    chat_server["model"]["model"] = _BlockingModel(
        gate, custom_output_text="慢回复。", call_tools=[]
    )

    results: dict[str, tuple[int, bytes]] = {}

    def request(message_id: str) -> None:
        status, _headers, data = _post(
            port, CHAT_PATH, _submit_body(CONVERSATION, message_id, "并发")
        )
        results[message_id] = (status, data)

    first = threading.Thread(target=request, args=("slow-1",))
    first.start()

    # 等待第一个 run 真正持锁（阻塞在模型请求阶段）。
    registry = get_context().chat_runs
    deadline = time.monotonic() + 5
    while not registry.is_active(CONVERSATION):
        if time.monotonic() > deadline:
            gate.set()
            first.join(timeout=5)
            raise AssertionError("第一个 run 未能持锁")
        time.sleep(0.02)

    status_second, _, data_second = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "slow-2", "并发第二轮")
    )
    assert status_second == 409
    assert (
        json.loads(data_second.decode("utf-8"))["error_code"]
        == "AI_CHAT_RUN_ACTIVE"
    )

    gate.set()
    first.join(timeout=10)
    assert results["slow-1"][0] == 200
    # 结束后锁被释放，可以再次领取。
    assert registry.is_active(CONVERSATION) is False


def test_distinct_conversations_run_concurrently(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    other = "conversation_global_chat_" + "0" * 32
    results: dict[str, int] = {}

    def request(conversation_id: str) -> None:
        status, _, _ = _post(
            port,
            CHAT_PATH,
            _submit_body(conversation_id, "parallel", "并行"),
        )
        results[conversation_id] = status

    threads = [
        threading.Thread(target=request, args=(CONVERSATION,)),
        threading.Thread(target=request, args=(other,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert results[CONVERSATION] == 200
    assert results[other] == 200


def test_model_failure_streams_official_error_chunk_and_releases_lock(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    chat_server["model"]["model"] = _FailingModel(call_tools=[])

    status, headers, data = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "fail-1", "失败测试")
    )

    # 失败也必须走官方 SSE，而不是退回 JSON。
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    chunks = [
        line[len("data: ") :]
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ")
    ]
    error_chunks = [
        json.loads(chunk)
        for chunk in chunks
        if chunk != "[DONE]" and json.loads(chunk).get("type") == "error"
    ]
    assert error_chunks
    assert chunks[-1] == "[DONE]"

    # 失败后锁被释放，可重新领取；claim 记录为 failed。
    assert get_context().chat_runs.is_active(CONVERSATION) is False
    claim = get_context().chat_turn_claims.find_for_conversation(CONVERSATION)
    assert claim is not None
    assert claim.status == "failed"


def test_client_disconnect_keeps_draining_until_completed(
    chat_service: dict[str, Any],
) -> None:
    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "3" * 32

    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "dc-1", "断连测试")).encode(),
    )

    def broken_writer(chunk: bytes) -> None:
        # 第一个 chunk（start）写出后立即模拟客户端断连。
        raise BrokenPipeError("client gone")

    # 断连不应把异常抛给调用方；服务端继续 drain，收尾仍要完成。
    asyncio.run(run.stream(broken_writer))

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # 已接受的 run 不因客户端断线取消：drain 完成后是 completed。
    assert claim.status == "completed"
    # 官方最终历史已保存，可被 /ui-messages 恢复。
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.model_messages()


def test_coroutine_cancellation_lets_producer_complete_and_releases(
    chat_service: dict[str, Any],
) -> None:
    """B-04：producer 生命周期独立于 observer；取消请求协程后仍完成并提交。

    旧实现把 producer 随请求协程一起取消，留下未 await 的协程
    （RuntimeWarning）并把已接受的 run 误记为 cancelled。修复后：
    取消 observer 只停止 SSE 写入，producer 与收尾移交独立任务完成，
    claim 到达 completed，且不再出现 "was never awaited" 警告。
    """

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "4" * 32

    async def slow_final_stream(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        # 终态在 observer 被取消之后才产生：验证 producer 不被连坐取消。
        await asyncio.sleep(0.05)
        yield "回复在取消之后才完成。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=slow_final_stream
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "cancel-1", "取消测试")).encode(),
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        async def cancel_observer_then_wait_detached() -> None:
            task = asyncio.create_task(run.stream(lambda _chunk: None))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            context = get_context()
            # detached 收尾任务独立继续：等待 claim 终态与 registry 释放。
            for _ in range(1000):
                pending = context.chat_turn_claims.find_for_conversation(
                    conversation
                )
                if pending is not None and pending.status in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    break
                await asyncio.sleep(0.01)
            for _ in range(1000):
                if not context.chat_runs.is_active(conversation):
                    break
                await asyncio.sleep(0.01)

        asyncio.run(cancel_observer_then_wait_detached())
        gc.collect()
        unawaited = [
            str(item.message)
            for item in caught
            if issubclass(item.category, RuntimeWarning)
            and "never awaited" in str(item.message)
        ]

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # 已接受的 run 不因 observer 取消而取消：producer 完成后是 completed。
    assert claim.status == "completed"
    # 官方最终历史已保存，可被 /ui-messages 恢复。
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.model_messages()
    assert unawaited == []


def test_request_loop_closed_immediately_after_cancel_lets_producer_finalize(
    chat_service: dict[str, Any],
) -> None:
    """报告 R-02：路由为每个请求创建一次性 event loop 并立即关闭。

    最小探针窗口：取消请求协程后马上执行 ``shutdown_asyncgens()`` 与
    ``loop.close()``（与 ``ai_chat_routes.handle_chat_run`` 相同的收尾序列）。
    旧实现把 finish_detached 创建在同一个即将关闭的 loop 上，producer 随之被
    取消：claim=failed/claimed、history 不存在、两个 "never awaited" 警告。
    修复后 producer 由进程级 runner 托管：history/link/claim 正常收尾，
    无 RuntimeWarning。
    """

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "a2" + "0" * 30

    async def slow_final_stream(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        # 终态在请求 loop 关闭之后才产生：producer 必须在 runner 上存活。
        await asyncio.sleep(0.3)
        yield "回复在请求 loop 关闭之后才完成。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=slow_final_stream
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "loop-close-1", "关闭测试")).encode(),
    )

    received_first_chunk = threading.Event()

    def write_chunk(_chunk: bytes) -> None:
        received_first_chunk.set()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        loop = asyncio.new_event_loop()
        try:
            async def cancel_after_first_chunk() -> None:
                task = loop.create_task(run.stream(write_chunk))
                while not received_first_chunk.is_set():
                    await asyncio.sleep(0.01)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

            loop.run_until_complete(cancel_after_first_chunk())
        finally:
            # 与真实路由完全一致：立即 shutdown_asyncgens + close。
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

        context = get_context()
        # producer 在进程级 runner 上继续：同步轮询 claim 终态。
        deadline = time.monotonic() + 10.0
        claim = context.chat_turn_claims.find_for_conversation(conversation)
        while time.monotonic() < deadline and (
            claim is None
            or claim.status not in {"completed", "failed", "cancelled"}
        ):
            time.sleep(0.05)
            claim = context.chat_turn_claims.find_for_conversation(conversation)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and context.chat_runs.is_active(
            conversation
        ):
            time.sleep(0.05)
        gc.collect()
        unawaited = [
            str(item.message)
            for item in caught
            if issubclass(item.category, RuntimeWarning)
            and "never awaited" in str(item.message)
        ]

    assert claim is not None
    # 请求 loop 已关闭，producer 仍在 runner 上完成：claim completed。
    assert claim.status == "completed"
    assert claim.error_code == ""
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.model_messages()
    assert context.chat_runs.is_active(conversation) is False
    assert unawaited == []


def test_chat_route_closes_pending_async_generators_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.http_route_units import ai_chat_routes

    class RunWithPendingResource:
        conversation_id = "conversation_global_chat_" + "5" * 32

        def __init__(self) -> None:
            self.resource_closed = False
            self.resource: Any = None

        def sse_headers(self) -> dict[str, str]:
            return {"Content-Type": "text/event-stream"}

        async def stream(self, _writer: Any) -> None:
            async def resource() -> Any:
                try:
                    yield object()
                finally:
                    self.resource_closed = True

            self.resource = resource()
            await anext(self.resource)

    class HandlerStub:
        path = CHAT_PATH

        def send_sse_headers(self, _headers: dict[str, str]) -> None:
            return None

        def write_sse_chunk(self, _chunk: bytes) -> None:
            return None

    run = RunWithPendingResource()
    monkeypatch.setattr(
        ai_chat_routes,
        "safe_json_body_with_raw",
        lambda _handler: ({}, b"{}"),
    )
    monkeypatch.setattr(
        ai_chat_routes,
        "validate_request_payload",
        lambda _payload, *, endpoint: None,
    )
    monkeypatch.setattr(
        ai_chat_routes.ai_chat_facade,
        "run_chat_stream",
        lambda _raw: run,
    )

    try:
        ai_chat_routes.handle_chat_run(HandlerStub())  # type: ignore[arg-type]
        assert run.resource_closed is True
    finally:
        if run.resource is not None and not run.resource_closed:
            asyncio.run(run.resource.aclose())


def test_header_write_disconnect_releases_run_lock_and_claim(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-02：写 SSE 响应头阶段断开不得遗留永久 run lock。

    探针：registry/claim 已在 prepare_run() 领取，send_sse_headers 抛
    BrokenPipeError 后旧实现永久占用 conversation（后续请求全部
    AI_CHAT_RUN_ACTIVE）。修复后：claim=failed、registry 释放、无 history，
    同一 conversation 可立即重试。
    """

    from erp_web.http_route_units import ai_chat_routes

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "7" * 32

    class BrokenHeaderHandler:
        path = CHAT_PATH

        def send_sse_headers(self, _headers: dict[str, str]) -> None:
            raise BrokenPipeError("client gone before headers")

        def write_sse_chunk(self, _chunk: bytes) -> None:
            return None

    monkeypatch.setattr(
        ai_chat_routes,
        "safe_json_body_with_raw",
        lambda _handler: ({}, b"{}"),
    )
    monkeypatch.setattr(
        ai_chat_routes,
        "validate_request_payload",
        lambda _payload, *, endpoint: None,
    )
    monkeypatch.setattr(
        ai_chat_routes.ai_chat_facade,
        "run_chat_stream",
        lambda _raw: service.prepare_run(
            json.dumps(_submit_body(conversation, "hdr-1", "响应头断线")).encode(),
        ),
    )

    with pytest.raises(BrokenPipeError):
        ai_chat_routes.handle_chat_run(BrokenHeaderHandler())  # type: ignore[arg-type]

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"
    assert context.pydantic_messages.get(conversation) is None

    # 同一 conversation 可立即重试：不卡在 AI_CHAT_RUN_ACTIVE。
    retry = service.prepare_run(
        json.dumps(_submit_body(conversation, "hdr-2", "断线后重试")).encode(),
    )
    assert retry.conversation_id == conversation
    asyncio.run(retry.stream(lambda _chunk: None))
    final_claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert final_claim is not None
    assert final_claim.status == "completed"


def test_disconnect_does_not_cancel_server_side_run(
    chat_service: dict[str, Any],
) -> None:
    """客户端断线只停止 SSE 写入；Agent run 继续 drain 到终态并保存历史。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "6" * 32
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "dc-after-delta", "保留部分历史")
        ).encode(),
    )
    written: list[bytes] = []

    def disconnect_after_delta(chunk: bytes) -> None:
        written.append(chunk)
        if b'"type":"text-delta"' in chunk:
            raise BrokenPipeError("client gone after delta")

    asyncio.run(run.stream(disconnect_after_delta))

    assert any(b'"type":"text-delta"' in chunk for chunk in written)
    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # 已接受的 run 不因客户端断线取消：drain 完成后是 completed 终态。
    assert claim.status == "completed"
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    # 服务端完整消费后保存的是官方最终历史，不存在中断占位状态。
    assert not any(
        isinstance(message, ModelResponse) and message.state == "interrupted"
        for message in messages
    )
    assert any(isinstance(message, ModelResponse) for message in messages)


def test_disconnect_during_multi_tool_turn_persists_complete_tool_pairs(
    chat_service: dict[str, Any],
) -> None:
    """断线后服务端 drain 完成多工具回合，历史里 call/return 真实成对。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "9" * 32
    turns = 0

    async def two_tool_model(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="disconnect-drafts",
                ),
                1: DeltaToolCall(
                    name="products_index_query",
                    json_args="{}",
                    tool_call_id="disconnect-products",
                ),
            }
            return
        yield "查询完成。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=two_tool_model
    )
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-disconnect", "查询商品和草稿")
        ).encode(),
    )

    def disconnect_after_first_output(chunk: bytes) -> None:
        if b'"type":"tool-output-available"' in chunk:
            raise BrokenPipeError("disconnect after first tool output")

    asyncio.run(run.stream(disconnect_after_first_output))

    history = get_context().pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert sorted(call_ids) == ["disconnect-drafts", "disconnect-products"]
    assert sorted(return_ids) == sorted(call_ids)
    claim = get_context().chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"


def test_business_failure_in_multi_tool_turn_persists_complete_tool_pairs(
    chat_service: dict[str, Any],
) -> None:
    """复现真实对话中的“首个读取成功、第二个商品已删除”失败序列。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "a" * 32
    turns = 0

    async def partially_failing_model(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="success-before-failure",
                ),
                1: DeltaToolCall(
                    name="product_read",
                    json_args='{"product_id":"already-deleted"}',
                    tool_call_id="missing-product",
                ),
            }
            return
        yield "已发现商品不存在。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=partially_failing_model
    )
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-failure", "读取已删除商品")
        ).encode(),
    )

    asyncio.run(run.stream(lambda _chunk: None))

    history = get_context().pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert sorted(call_ids) == ["missing-product", "success-before-failure"]
    assert sorted(return_ids) == sorted(call_ids)


def test_pre_stream_conversion_failure_finishes_claim_and_releases_lock(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "8" * 32

    def fail_conversion(_message: Any) -> list[Any]:
        raise VercelUiProtocolError(
            400,
            "AI_CHAT_MESSAGE_INVALID",
            "模拟 Adapter 转换失败。",
        )

    monkeypatch.setattr(service, "_load_new_messages", fail_conversion)

    with pytest.raises(VercelUiProtocolError) as caught:
        service.prepare_run(
            json.dumps(
                _submit_body(conversation, "pre-stream-fail", "转换失败")
            ).encode(),
        )

    assert caught.value.code == "AI_CHAT_MESSAGE_INVALID"
    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"


def test_real_socket_receives_incremental_chunks_before_finish(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    gate = threading.Event()

    # 两阶段模型（修复计划 14.6）：先输出第一段文本并实时下发，再在 gate 上
    # 暂停；客户端此时必须已收到首个 text-delta 且尚未收到 finish；放行后
    # 输出剩余文本并完成。
    async def two_phase_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        yield "增量"
        await asyncio.to_thread(gate.wait, 10)
        yield "回复。"

    chat_server["model"]["model"] = FunctionModel(stream_function=two_phase_model)
    conversation = "conversation_global_chat_" + "4" * 32
    body = json.dumps(
        _submit_body(conversation, "sock-1", "增量测试")
    ).encode("utf-8")

    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        request = (
            f"POST {CHAT_PATH} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8") + body
        sock.sendall(request)

        buffer = b""
        # 在 finish 之前（模型仍被 gate 阻塞）就应读到首个真实 text-delta。
        deadline = time.monotonic() + 10
        while b'"type":"text-delta"' not in buffer:
            if time.monotonic() > deadline:
                raise AssertionError("未在 finish 前收到首个 text-delta")
            try:
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            if not piece:
                break
            buffer += piece
        assert b"HTTP/1.0 200" in buffer or b"HTTP/1.1 200" in buffer
        assert b"text/event-stream" in buffer
        assert b'{"type":"start"}' in buffer
        # 此时模型尚未产出完整结果，finish 不应已经出现。
        assert b'"type":"finish"' not in buffer

        # 放行模型并把剩余流读完整，验证增量最终收敛到 [DONE]。
        gate.set()
        deadline = time.monotonic() + 10
        while b"[DONE]" not in buffer:
            if time.monotonic() > deadline:
                raise AssertionError("未在超时前收到 [DONE]")
            try:
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            if not piece:
                break
            buffer += piece
        assert b'"type":"finish"' in buffer
        assert b"[DONE]" in buffer
        streamed_text = "".join(
            json.loads(line[len("data: ") :])["delta"]
            for line in buffer.decode("utf-8").splitlines()
            if line.startswith("data: ")
            and line[len("data: ") :] != "[DONE]"
            and json.loads(line[len("data: ") :]).get("type") == "text-delta"
        )
        assert streamed_text == "增量回复。"
    finally:
        gate.set()
        sock.close()

    # 流正常结束后历史完成持久化。
    deadline = time.monotonic() + 10
    while get_context().pydantic_messages.get(conversation) is None:
        if time.monotonic() > deadline:
            raise AssertionError("流结束后历史未持久化")
        time.sleep(0.02)


def test_provider_secret_does_not_leak_into_sse(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    chat_server["model"]["model"] = _SecretLeakModel(call_tools=[])

    status, headers, data = _post(
        port,
        CHAT_PATH,
        _submit_body(CONVERSATION, "secret-1", "密钥测试"),
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    text = data.decode("utf-8")
    assert "SUPERSECRETKEY" not in text
    assert "abcdef123456.xyz" not in text
    assert "sk-live0123456789abcdef" not in text
    # 错误仍以官方 error chunk 形式出现。
    assert any(
        json.loads(chunk).get("type") == "error"
        for chunk in (
            line[len("data: ") :]
            for line in text.splitlines()
            if line.startswith("data: ") and line[len("data: ") :] != "[DONE]"
        )
    )


def test_provider_payment_required_has_stable_sse_and_claim_error(
    chat_server: dict[str, Any],
) -> None:
    conversation = "conversation_global_chat_" + "e" * 32
    message_id = "payment-required-1"
    chat_server["model"]["model"] = _PaymentRequiredModel(call_tools=[])

    status, headers, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(conversation, message_id, "测试计费错误"),
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    chunks = [
        json.loads(line[len("data: ") :])
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ") and line[len("data: ") :] != "[DONE]"
    ]
    errors = [item for item in chunks if item.get("type") == "error"]
    assert errors == [
        {
            "type": "error",
            "errorText": "AI Provider 拒绝请求（HTTP 402）：余额不足或计费配置不可用。",
        }
    ]
    claim = get_context().chat_turn_claims.get(conversation, message_id)
    assert claim is not None
    assert claim.status == "failed"
    assert claim.error_code == "AI_PROVIDER_PAYMENT_REQUIRED"


@pytest.mark.parametrize(
    "body,status_code,error_code",
    [
        (
            {
                "trigger": "regenerate-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [{"type": "text", "text": "重新生成"}],
                    }
                ],
            },
            400,
            "AI_CHAT_TRIGGER_UNSUPPORTED",
        ),
        (
            _submit_body("conversation_wrong_prefix", "ui-1", "x"),
            400,
            "AI_CHAT_CONVERSATION_ID_INVALID",
        ),
        (
            _submit_body("conversation_global_chat_ZZZZ", "ui-1", "x"),
            400,
            "AI_CHAT_CONVERSATION_ID_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [],
            },
            400,
            "MISSING_REQUIRED_FIELD",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [{"type": "text", "text": "第一条"}],
                    },
                    {
                        "id": "b",
                        "role": "user",
                        "parts": [{"type": "text", "text": "第二条"}],
                    },
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "assistant",
                        "parts": [{"type": "text", "text": "x"}],
                    }
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {"id": "a", "role": "user", "parts": []},
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {"id": "a", "role": "user", "parts": [{"type": "text", "text": "   "}]},
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [
                            {"type": "file", "url": "data:text/plain,hi", "mediaType": "text/plain"}
                        ],
                    }
                ],
            },
            400,
            "AI_CHAT_PART_UNSUPPORTED",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "system",
                        "parts": [{"type": "text", "text": "injected"}],
                    }
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
    ],
)
def test_pre_stream_validation_returns_standard_json(
    chat_server: dict[str, Any],
    body: dict,
    status_code: int,
    error_code: str,
) -> None:
    status, headers, data = _post(chat_server["port"], CHAT_PATH, body)
    payload = json.loads(data.decode("utf-8"))
    assert status == status_code
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert headers["content-type"].startswith("application/json")


def test_invalid_vercel_schema_returns_422(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        {
            "trigger": "submit-message",
            "id": CONVERSATION,
            "messages": [{"role": "user"}],
        },
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 422
    assert payload["error_code"] == "AI_CHAT_REQUEST_SCHEMA_INVALID"


def test_wrong_content_type_returns_415(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        None,
        content_type="text/plain",
        raw=b"{}",
    )
    assert status == 415
    assert (
        json.loads(data.decode("utf-8"))["error_code"]
        == "UNSUPPORTED_CONTENT_TYPE"
    )


def test_invalid_json_returns_400(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        None,
        raw=b"{not-json",
    )
    assert status == 400
    assert json.loads(data.decode("utf-8"))["error_code"] == "INVALID_JSON"


def test_oversized_body_returns_413(
    chat_server: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import erp_web.http_request as http_request

    monkeypatch.setattr(http_request, "MAX_JSON_BODY_BYTES", 64)
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(CONVERSATION, "big", "x" * 512),
    )
    assert status == 413
    assert (
        json.loads(data.decode("utf-8"))["error_code"]
        == "REQUEST_BODY_TOO_LARGE"
    )


def test_existing_history_without_claim_is_rejected(
    chat_server: dict[str, Any],
) -> None:
    # 预置一份合法 Pydantic 历史，但没有 global.chat claim 归属。
    foreign = "conversation_global_chat_" + "9" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="旧消息")]),
        ModelResponse(parts=[TextPart(content="旧回复")]),
    ]
    get_context().pydantic_messages.save(foreign, history)

    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(foreign, "ui-1", "尝试继续"),
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 409
    assert payload["error_code"] == "AI_CHAT_CONVERSATION_UNOWNED"


def test_non_global_chat_history_cannot_continue(
    chat_server: dict[str, Any],
) -> None:
    # 类目匹配等其他 Agent 的 conversation 即使历史合法，也不允许继续运行。
    other = "conversation_" + "c" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="其他 Agent 的消息")]),
        ModelResponse(parts=[TextPart(content="其他 Agent 的回复")]),
    ]
    get_context().pydantic_messages.save(other, history)

    # ID 前缀不满足 global chat 严格格式，直接在入口拒绝。
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(other, "ui-1", "尝试继续"),
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 400
    assert payload["error_code"] == "AI_CHAT_CONVERSATION_ID_INVALID"


def test_ui_messages_derives_official_ui_messages(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "你好"))

    status, data = _get(
        port, f"/api/v1/ai-work/conversations/{CONVERSATION}/ui-messages"
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    assert payload["ok"] is True
    assert payload["conversation_id"] == CONVERSATION
    messages = payload["messages"]
    assert messages
    roles = {message["role"] for message in messages}
    assert "user" in roles
    assert "assistant" in roles
    # UI 消息使用官方 camelCase alias。
    for message in messages:
        for part in message["parts"]:
            assert "type" in part


def test_ui_messages_missing_conversation_returns_404(
    chat_server: dict[str, Any],
) -> None:
    missing = "conversation_global_chat_" + "1" * 32
    status, data = _get(
        chat_server["port"],
        f"/api/v1/ai-work/conversations/{missing}/ui-messages",
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 404
    assert payload["error_code"] == "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND"


def test_ui_messages_renders_text_reasoning_and_tool_parts(
    chat_server: dict[str, Any],
) -> None:
    # 预置包含 text、reasoning、tool call/result 的历史。
    rich = "conversation_global_chat_" + "2" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="查询草稿")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="先调用 drafts_query。"),
                ToolCallPart(
                    tool_name="drafts_query",
                    args={"query": {}},
                    tool_call_id="call_1",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="drafts_query",
                    content={"total": 0, "items": []},
                    tool_call_id="call_1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="当前没有草稿。")]),
    ]
    get_context().pydantic_messages.save(rich, history)

    status, data = _get(
        chat_server["port"],
        f"/api/v1/ai-work/conversations/{rich}/ui-messages",
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    part_types = {
        part["type"]
        for message in payload["messages"]
        for part in message["parts"]
    }
    assert "text" in part_types
    assert "reasoning" in part_types
    assert any(
        part_type.startswith("tool-") for part_type in part_types
    )


def test_retired_ai_work_subresources_stay_404(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    # events 已由 Deferred 迁移恢复为正式订阅端点，不再是 retired。
    for suffix in ("raw", "children", "wait"):
        status, _ = _get(
            port,
            f"/api/v1/ai-work/conversations/{CONVERSATION}/{suffix}",
        )
        assert status == 404


def test_deferred_handshake_commits_history_link_outbox_atomically(
    chat_service: dict[str, Any],
) -> None:
    """global_task_start 以官方 Deferred 语义暂停：history/link/outbox 同事务
    提交，conversation 被锁定，任务在首次 history 提交前不可执行。"""

    from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "e" * 32
    tool_call_id = "call-deferred-e2e"

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=json.dumps(
                        {
                            "goal": "翻译一段文本",
                            "steps": [
                                {
                                    "capability_name": "text_translate",
                                    "arguments": {
                                        "target_language": "en",
                                        "content": {"title": "便携风扇"},
                                    },
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=start_task_model)

    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "deferred-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))

    context = get_context()
    # run 以 DeferredToolRequests 结束：SSE 正常收尾，claim completed。
    text = b"".join(chunks).decode("utf-8")
    assert '"type":"finish"' in text
    assert tool_call_id in text
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"

    # link ready：首次 history 已原子提交并冻结版本。
    links = context.deferred_task_links
    link = links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.ready_at != ""
    assert link.tool_call_id == tool_call_id
    assert link.history_version == context.pydantic_messages.get_version(
        conversation
    )

    # 官方 history 保留未闭合 ToolCallPart（没有合成 ToolReturnPart）。
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    assert tool_call_id in call_ids
    assert not any(
        isinstance(part, ToolReturnPart)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )

    # outbox 保存官方编码事件批次，并按 history_version 可重放。
    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert [batch.history_version for batch in batches] == [link.history_version]
    assert batches[0].kind == "deferred_handshake"
    assert batches[0].run_id != ""
    assert any('"type":"finish"' in event for event in batches[0].events)

    # 未解决 Deferred 存在时，服务端拒绝新的普通用户回合。
    with pytest.raises(VercelUiProtocolError) as blocked:
        service.prepare_run(
            json.dumps(
                _submit_body(conversation, "deferred-2", "再来一条")
            ).encode(),
        )
    assert blocked.value.code == "AI_CHAT_CONVERSATION_TASK_PENDING"

    # 任务已创建但仍 running：后台 worker 在 link ready 后才能领取执行。
    task = context.global_tasks.load_task(link.task_id)
    assert task is not None
    assert task.status == "running"


def test_large_streaming_handshake_commits_with_bounded_terminal_segment(
    chat_service: dict[str, Any],
) -> None:
    """线上缺陷 L-01 回归：流式 run 按 delta 逐条编码，chunk 数轻易超过旧
    512 条 outbox 防线。

    2026-08-21，conversation_global_chat_ab3810c1bc5e4590930f792aa5636707
    的握手提交因整条 delta 流超过 512 条被误拒
    （PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE），claim failed、link 被 120 秒
    sweep abandoned、任务随之取消。修复后 outbox 批次只持久化有界终态段，
    流式 delta 全文以 /ui-messages 为事实源，握手必须正常提交。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import (
        MAX_OUTBOX_EVENT_CHUNKS,
        MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS,
    )

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "c1" + "0" * 30
    tool_call_id = "call-large-stream"

    async def large_stream_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            # 先产出远超旧 512 条上限的长流式文本，再受理任务。
            for index in range(600):
                yield f"流式片段 {index}。"
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=large_stream_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "large-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))

    # 场景自校验：本 run 的官方编码 chunk 数确实击穿了旧 512 条防线。
    assert len(chunks) > MAX_OUTBOX_EVENT_CHUNKS

    text = b"".join(chunks).decode("utf-8")
    assert '"type":"finish"' in text

    context = get_context()
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    assert claim.error_code == ""

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.history_version == context.pydantic_messages.get_version(
        conversation
    )

    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert [batch.history_version for batch in batches] == [link.history_version]
    assert batches[0].kind == "deferred_handshake"
    # 批次只持久化有界终态段，不再是整条 delta 流。
    assert len(batches[0].events) <= MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS
    assert any('"type":"finish"' in event for event in batches[0].events)


def _run_simple_deferred_handshake(
    chat_service: dict[str, Any],
    conversation: str,
    tool_call_id: str,
) -> str:
    """跑通一次最简 Deferred 握手；返回完整 SSE 文本。"""

    service = chat_service["service"]

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=start_task_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "notify-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    return b"".join(chunks).decode("utf-8")


def test_handshake_publish_failure_does_not_block_claim_or_finish(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 R-05：首次握手 durable commit 之后的通知异常必须被隔离。

    旧实现中 publish/mark_published 位于未隔离收尾路径：任一异常都会跳过
    held chunks flush 与 claim.finish_turn()（探针：claim=claimed、原 POST
    无 finish、outbox 未发布）。修复后通知是 best-effort 独立阶段：claim 与
    原 POST 正常收尾，批次保留未发布由后台 publisher 重投。
    """

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "d1" + "0" * 30

    class FailingBus:
        def publish(self, conversation_id: str, batch: Any) -> None:
            raise RuntimeError("注入的事件总线故障。")

    monkeypatch.setattr(service, "event_bus", FailingBus())

    text = _run_simple_deferred_handshake(
        chat_service, conversation, "call-publish-fail"
    )

    # 原 POST 协议完整：缓冲段与 finish 正常送达客户端。
    assert '"type":"tool-input-available"' in text
    assert '"type":"finish"' in text

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # claim 不因通知失败停在 claimed：正常收尾为 completed。
    assert claim.status == "completed"
    assert claim.error_code == ""

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert context.pydantic_messages.get(conversation) is not None

    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert len(batches) == 1
    assert batches[0].kind == "deferred_handshake"
    # 通知失败只留下未发布批次：后台 outbox publisher 可重投。
    assert batches[0].published_at == ""


def test_handshake_mark_published_failure_does_not_block_claim_or_finish(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 R-05（mark_published 分支）：记账异常同样不得阻塞收尾。"""

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "d2" + "0" * 30

    def failing_mark_published(batches: Any) -> None:
        raise RuntimeError("注入的 outbox 记账故障。")

    monkeypatch.setattr(
        service.event_outbox,
        "mark_published",
        failing_mark_published,
    )

    text = _run_simple_deferred_handshake(
        chat_service, conversation, "call-mark-fail"
    )

    assert '"type":"finish"' in text
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert len(batches) == 1
    assert batches[0].published_at == ""


def test_exception_after_result_event_does_not_save_history_alone(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-03：首次 Deferred run 在完成阶段异常时不得单独保存 history。

    对完成阶段的超时预算调用注入异常：history/link/outbox 必须保持同一组合
    事务事实（探针旧实现：history 已保存但 link awaiting_history、outbox=0）。
    修复后：无 history、link awaiting_history、outbox 空、claim failed。
    """

    import inspect

    from erp_web.schemas.ai_trace import AiExecutionContext

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "e1" + "0" * 30
    tool_call_id = "call-result-stage-error"

    original_budget = AiExecutionContext.bounded_timeout_seconds

    def budget_raising_at_completion(self: Any, *args: Any, **kwargs: Any) -> Any:
        frames = inspect.stack()
        if any(frame.function == "_complete_with_result" for frame in frames):
            raise TimeoutError("注入的完成阶段预算异常。")
        return original_budget(self, *args, **kwargs)

    monkeypatch.setattr(
        AiExecutionContext,
        "bounded_timeout_seconds",
        budget_raising_at_completion,
    )

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=start_task_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "exc-1", "完成阶段异常")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 回合以官方 error 闭合。
    assert '"finishReason":"error"' in text

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"

    # 组合事务事实保持原子：没有单独落盘的 history。
    assert context.pydantic_messages.get(conversation) is None

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "awaiting_history"
    assert context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    ) == []


def _deferred_start_args() -> str:
    return json.dumps(
        {
            "goal": "翻译一段文本",
            "steps": [
                {
                    "capability_name": "text_translate",
                    "arguments": {
                        "target_language": "en",
                        "content": {"title": "便携风扇"},
                    },
                }
            ],
        },
        ensure_ascii=False,
    )


def test_duplicate_global_task_start_closes_second_call_with_stable_error(
    chat_service: dict[str, Any],
) -> None:
    """报告 §8-1：同一响应两次相同参数 global_task_start 只产生一个 Deferred。

    Deferred 控制 Tool 是 sequential 的：第一次调用创建任务并以 CallDeferred
    暂停；第二次相同签名调用被 Runtime 去重，以稳定错误
    GLOBAL_TASK_DEFERRED_DUPLICATE 闭合，不能再创建第二个任务或第二个
    Deferred。握手 cardinality 校验保证首次提交只包含唯一未闭合调用。
    """

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "d" * 32
    first_call = "call-dup-1"
    second_call = "call-dup-2"

    async def duplicate_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=first_call,
                ),
                1: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=second_call,
                ),
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=duplicate_task_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "dup-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))

    context = get_context()
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"

    # 只创建了一个任务与一个 link；握手提交成功本身证明 cardinality == 1。
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.tool_call_id == first_call

    history = context.pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    open_call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    assert open_call_ids == [first_call, second_call]
    returned = {
        part.tool_call_id: part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    # 第二次调用以稳定错误闭合（模型可见），第一次调用保持未闭合。
    assert set(returned) == {second_call}
    duplicate_content = str(returned[second_call].content)
    assert "GLOBAL_TASK_DEFERRED_DUPLICATE" in duplicate_content
    # 重复调用不得创建孤儿任务：link 绑定的任务是唯一活动任务。
    assert context.global_tasks.load_task(link.task_id) is not None


def test_handshake_commit_failure_withholds_uncommitted_terminal_events(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 §8-2：history/link/outbox 事务失败时，客户端不收到未提交终态。"""

    from erp_web.stores.pydantic_deferred_task_link_store import (
        PydanticDeferredLinkError,
    )

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "b" * 32
    tool_call_id = "call-commit-fail"

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=start_task_model)

    def fail_commit(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_LINK_NOT_AWAITING",
            "注入的组合事务失败。",
        )

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        fail_commit,
    )

    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "commit-fail", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 客户端收到官方 error 闭合，且未提交的成功终态事件没有发布。
    assert '"type":"error"' in text
    assert "注入的组合事务失败。" in text
    # 未提交前缓冲的成功终态事件不得发布（只允许 error 路径的收尾）。
    assert '"finishReason":"error"' in text
    # 修复计划第 14 节：工具输入骨架属于运行中临时态，可实时下发；但提交
    # 失败时不得发布任何结构化成功终态（Tool Result 成功态/任务受理），也
    # 不得回填未提交调用的 tool-input-error。error 闭合流自身的 finish
    # （finishReason=error）属于官方错误收尾，允许出现。
    assert '"type":"tool-output-available"' not in text
    assert '"type":"tool-input-error"' not in text

    # claim 记录可诊断的失败终态。
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"
    assert claim.error_code == "PYDANTIC_DEFERRED_LINK_NOT_AWAITING"

    # 事务失败：history 未保存、link 仍 provisional、outbox 无批次。
    assert context.pydantic_messages.get(conversation) is None
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "awaiting_history"
    assert context.ai_event_outbox.list_after(
        conversation, after_history_version=0
    ) == []


def test_multi_tool_direct_first_handshake_failure_leaks_no_tool_events(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-01：Direct → global_task_start → 首次 commit 失败。

    模型先调用 Direct 工具 drafts_query 再调用 global_task_start 时，先行
    Direct 工具的 tool-input-* 事件在旧实现里已于组合事务前发给客户端。
    修复后缓冲边界前移到本 run 第一个工具调用的 tool-input-start：提交前
    允许到达的只有 start/start-step 信封；提交失败时任何工具事件、调用 id
    与参数都不得出现。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import (
        PydanticDeferredLinkError,
    )

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "a1" + "0" * 30
    direct_call = "call-direct-first"
    task_call = "call-task-second"

    async def direct_then_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                ),
                1: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=task_call,
                ),
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=direct_then_task_model
    )

    commit_entries = 0

    def recording_failing_commit(*args: Any, **kwargs: Any) -> int:
        nonlocal commit_entries
        del args, kwargs
        commit_entries += 1
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_LINK_NOT_AWAITING",
            "注入的组合事务失败。",
        )

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_failing_commit,
    )

    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-fail", "先查草稿再创建任务")
        ).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    assert commit_entries == 1

    # 提交失败：以官方 error 闭合。工具输入骨架与只读工具（drafts_query）
    # 输出属于运行中临时态，允许实时下发（提交失败后前端重读 /ui-messages
    # 对账）；但 Deferred 控制工具的受理成功态不得发布（修复计划第 14 节）。
    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    assert '"finishReason":"error"' in text
    leaked_acceptance = any(
        (
            _chunk_payload(line[len("data: "):].decode("utf-8")) or {}
        ).get("type")
        == "tool-output-available"
        and (
            _chunk_payload(line[len("data: "):].decode("utf-8")) or {}
        ).get("toolCallId")
        == task_call
        for line in chunks
        if line.startswith(b"data: ")
    )
    assert not leaked_acceptance

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"
    assert context.pydantic_messages.get(conversation) is None
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "awaiting_history"


def test_multi_tool_direct_first_handshake_success_publishes_after_commit(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-01 成功面：Direct → global_task_start 提交成功后事件齐备。

    缓冲段（Direct 工具事件 + 控制工具段）必须在组合事务提交后按原顺序
    发布；提交入口前客户端收到的类型恰为 [start, start-step]。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "a3" + "0" * 30
    direct_call = "call-direct-first"
    task_call = "call-task-second"

    async def direct_then_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                ),
                1: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=task_call,
                ),
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=direct_then_task_model
    )

    chunks: list[bytes] = []
    pre_commit_types: list[list[str]] = []
    original_commit = context.deferred_task_links.commit_initial_deferred_history

    def recording_commit(*args: Any, **kwargs: Any) -> int:
        pre_commit_types.append(
            [
                str((_chunk_payload(chunk.decode("utf-8")) or {}).get("type"))
                for chunk in chunks
            ]
        )
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_commit,
    )

    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-ok", "先查草稿再创建任务")
        ).encode(),
    )
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 组合事务入口前：start/start-step 信封与临时内容（工具输入骨架、只读
    # 工具输出）可实时下发；但 finish-step/finish 成功终态必须在提交后才发布。
    assert len(pre_commit_types) == 1
    assert pre_commit_types[0][:2] == ["start", "start-step"]
    for forbidden in ("finish-step", "finish"):
        assert forbidden not in pre_commit_types[0]

    # 提交成功后：Direct 工具与控制工具的完整事件按官方顺序送达。
    assert direct_call in text
    assert task_call in text
    assert '"type":"tool-output-available"' in text
    assert '"type":"finish"' in text

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.tool_call_id == task_call
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"


def test_text_first_handshake_failure_leaks_no_content(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-01（收紧后，失败面）：text → Direct → global_task_start → 提交失败。

    首个工具出现之前的文本同样属于可能未提交的 Deferred 回合：提交失败时客户
    端除 start/start-step 信封与官方 error 闭合外，不得看到任何文本、工具事
    件、调用 id 或参数。组合事务入口打点：此前收到的类型恰为
    [start, start-step]。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload
    from erp_web.stores.pydantic_deferred_task_link_store import (
        PydanticDeferredLinkError,
    )

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f4" + "0" * 30
    direct_call = "call-text-first-direct"
    task_call = "call-text-first-task"

    async def text_direct_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield "让我先解释一下接下来要做的事情。"
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                ),
                1: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=task_call,
                ),
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=text_direct_task_model
    )

    chunks: list[bytes] = []
    pre_commit_types: list[list[str]] = []

    def recording_failing_commit(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        pre_commit_types.append(
            [
                str((_chunk_payload(chunk.decode("utf-8")) or {}).get("type"))
                for chunk in chunks
            ]
        )
        raise PydanticDeferredLinkError(
            "PYDANTIC_DEFERRED_LINK_NOT_AWAITING",
            "注入的组合事务失败。",
        )

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_failing_commit,
    )

    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "text-first-fail", "解释后创建任务")
        ).encode(),
    )
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 组合事务入口前：start/start-step 信封与临时内容可实时下发。
    assert len(pre_commit_types) == 1
    assert pre_commit_types[0][:2] == ["start", "start-step"]

    # 提交失败：以官方 error 闭合。工具前文本与工具输入骨架属于运行中临时
    # 态，允许实时下发（前端重读 /ui-messages 对账）；但 Deferred 受理成功
    # 态不得发布（修复计划第 14 节）。
    assert '"finishReason":"error"' in text
    assert '"type":"text-delta"' in text
    leaked_acceptance = any(
        (
            _chunk_payload(line[len("data: "):].decode("utf-8")) or {}
        ).get("type")
        == "tool-output-available"
        and (
            _chunk_payload(line[len("data: "):].decode("utf-8")) or {}
        ).get("toolCallId")
        == task_call
        for line in chunks
        if line.startswith(b"data: ")
    )
    assert not leaked_acceptance

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "awaiting_history"


def test_text_first_handshake_success_publishes_content_after_commit(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-01（收紧后，成功面）：text → Direct → global_task_start → 提交成功。

    缓冲段（工具前文本 + Direct 工具段 + 控制工具段）在组合事务提交后按原顺序
    发布；提交入口前类型恰为 [start, start-step]。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f5" + "0" * 30
    direct_call = "call-text-first-direct"
    task_call = "call-text-first-task"
    leading_text = "让我先解释一下接下来要做的事情。"

    async def text_direct_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield leading_text
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                ),
                1: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=task_call,
                ),
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=text_direct_task_model
    )

    chunks: list[bytes] = []
    pre_commit_types: list[list[str]] = []
    original_commit = context.deferred_task_links.commit_initial_deferred_history

    def recording_commit(*args: Any, **kwargs: Any) -> int:
        pre_commit_types.append(
            [
                str((_chunk_payload(chunk.decode("utf-8")) or {}).get("type"))
                for chunk in chunks
            ]
        )
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_commit,
    )

    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "text-first-ok", "解释后创建任务")
        ).encode(),
    )
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    assert len(pre_commit_types) == 1
    assert pre_commit_types[0][:2] == ["start", "start-step"]
    for forbidden in ("finish-step", "finish"):
        assert forbidden not in pre_commit_types[0]

    # 提交成功后：工具前文本与两个工具段按官方顺序送达。
    assert leading_text in text
    assert direct_call in text
    assert task_call in text
    assert '"type":"finish"' in text

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"


def _structural_types(chunks: list[bytes]) -> list[str]:
    """按收到顺序提取官方 chunk 类型；跳过不可解析/非 JSON 载荷（如 [DONE]）。"""

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    types: list[str] = []
    for chunk in chunks:
        payload = _chunk_payload(chunk.decode("utf-8")) or {}
        chunk_type = str(payload.get("type") or "")
        if chunk_type:
            types.append(chunk_type)
    return types


def _assert_tool_deltas_well_formed(types: list[str]) -> None:
    """tool-input-delta 必须位于对应 tool-input-start 与 tool-input-available 之间。"""

    open_call = False
    for chunk_type in types:
        if chunk_type == "tool-input-start":
            assert not open_call, "tool-input-start 出现在未闭合调用内"
            open_call = True
        elif chunk_type == "tool-input-delta":
            assert open_call, "tool-input-delta 越过调用边界（事件被重排）"
        elif chunk_type == "tool-input-available":
            assert open_call, "tool-input-available 缺少前置 tool-input-start"
            open_call = False
    assert not open_call, "存在未闭合的 tool-input 调用"


def test_multi_turn_direct_then_deferred_keeps_encoder_order(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-01（多模型轮）：第一轮 Direct → 第二轮 Deferred。

    旧实现把每个 start-step 都当实时信封，第二轮 start-step 会越过仍在缓冲
    的第一轮内容，系统性重排官方 SSE。修复后实时例外只有整条 run 的初始
    start 与首个 start-step；完整收到序列必须与 encoder 原顺序一致（不是只
    检查事件集合），组合事务入口前类型恰为 [start, start-step]。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f8" + "0" * 30
    direct_call = "call-mt-direct"
    task_call = "call-mt-task"

    async def multi_turn_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                )
            }
            return
        yield {
            0: DeltaToolCall(
                name="global_task_start",
                json_args=_deferred_start_args(),
                tool_call_id=task_call,
            )
        }

    chat_service["model"]["model"] = FunctionModel(stream_function=multi_turn_model)

    chunks: list[bytes] = []
    pre_commit_types: list[list[str]] = []
    original_commit = context.deferred_task_links.commit_initial_deferred_history

    def recording_commit(*args: Any, **kwargs: Any) -> int:
        pre_commit_types.append(
            [
                str((_chunk_payload(chunk.decode("utf-8")) or {}).get("type"))
                for chunk in chunks
            ]
        )
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_commit,
    )

    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "multi-turn-1", "多轮任务")).encode(),
    )
    asyncio.run(run.stream(chunks.append))

    # 组合事务入口前：初始信封与临时内容（第一轮 Direct 段，含其 finish-step）
    # 可实时下发；Deferred 第二轮的 finish-step/finish 仍受提交屏障保护。
    assert len(pre_commit_types) == 1
    assert pre_commit_types[0][:2] == ["start", "start-step"]
    assert "finish" not in pre_commit_types[0]

    types = _structural_types(chunks)
    _assert_tool_deltas_well_formed(types)
    # 完整序列与 encoder 原顺序一致（tool-input-delta 数量可变，过滤后精确
    # 匹配；Deferred 暂停步骤在 finish-step 前编码官方 message-metadata）。
    filtered = [t for t in types if t != "tool-input-delta"]
    assert filtered == [
        "start",
        "start-step",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "finish-step",
        "start-step",
        "tool-input-start",
        "tool-input-available",
        "message-metadata",
        "finish-step",
        "finish",
    ]

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.tool_call_id == task_call
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"


def test_multi_turn_direct_then_text_keeps_encoder_order(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-01（多模型轮对照面）：第一轮 Direct → 第二轮文本。

    普通非 Deferred 多模型轮同样不得重排：第二个 start-step 必须在第一个
    finish-step 之后，文本 delta 必须在第二个 start-step 之后，整条流以
    finish 闭合。
    """

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f9" + "0" * 30
    direct_call = "call-mt2-direct"

    async def multi_turn_text_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="drafts_query",
                    json_args="{}",
                    tool_call_id=direct_call,
                )
            }
            return
        yield "这是第二轮的最终回复。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=multi_turn_text_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "multi-turn-2", "多轮回复")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))

    types = _structural_types(chunks)
    _assert_tool_deltas_well_formed(types)
    filtered = [t for t in types if t != "tool-input-delta"]

    # 结构性断言（完整顺序而非事件集合）：
    # 1) 初始信封打头；
    assert filtered[0] == "start"
    assert filtered[1] == "start-step"
    # 2) 第二个 start-step 必须在第一个 finish-step 之后（不得提前）；
    first_finish_step = filtered.index("finish-step")
    second_start_step = len(filtered) - 1 - filtered[::-1].index("start-step")
    assert second_start_step > first_finish_step
    # 3) 文本 delta 必须在第二个 start-step 之后；
    text_deltas = [i for i, t in enumerate(types) if t == "text-delta"]
    assert text_deltas and all(i > second_start_step for i in text_deltas)
    # 4) 整条流以 finish-step + finish 闭合；
    assert filtered[-2:] == ["finish-step", "finish"]

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    # 未进入 Deferred：没有 link，history 由常规路径保存。
    assert context.deferred_task_links.active_for_conversation(conversation) is None
    assert context.pydantic_messages.get(conversation) is not None


def test_long_stream_and_slow_observer_stay_bounded_and_ordered(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-13：长工具流 + 慢 observer 压力测试（探针口径修正版）。

    数千段流式文本后进入 Deferred 握手：held/queue 有界，run 正常完成，
    claim completed。断言使用真实 SDK v7 字段（text-delta 的载荷键是
    ``delta``），并且必须覆盖完整协议而不是事件子集：工具骨架
    （tool-input-start/tool-input-available）、finish-step/finish 一个都不
    能丢，finish 必须是最后一个数据 chunk，text-delta 序号严格递增（允许
    背压丢弃中间内容，不允许乱序），且确实收到了大量文本 delta。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f6" + "0" * 30
    tool_call_id = "call-stress"

    async def long_stream_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            for index in range(3000):
                yield f"流式片段 {index}。"
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=long_stream_model)
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "stress-1", "长流压力")).encode(),
    )

    received_seqs: list[int] = []
    received_types: list[str] = []

    def slow_write(chunk: bytes) -> None:
        # 慢 observer：每次写入都让出，制造背压窗口。
        payload = _chunk_payload(chunk.decode("utf-8")) or {}
        chunk_type = str(payload.get("type") or "")
        if chunk_type:
            received_types.append(chunk_type)
        if chunk_type == "text-delta":
            # 报告 A-13：SDK v7 的 text-delta 载荷键是 delta（不是 textDelta）。
            delta = str(payload.get("delta") or "")
            if delta.startswith("流式片段 ") and delta.endswith("。"):
                seq_text = delta[len("流式片段 ") : -len("。")]
                if seq_text.isdigit():
                    received_seqs.append(int(seq_text))
        time.sleep(0.0002)

    asyncio.run(run.stream(slow_write))

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"

    # 确实读到了大量文本 delta（旧测试因读错字段在空列表上平凡通过）。
    assert len(received_seqs) > 100
    # 背压下允许丢弃中间内容，不允许乱序：序号严格递增且无重复。
    assert received_seqs == sorted(received_seqs)
    assert len(set(received_seqs)) == len(received_seqs)
    # 完整协议：工具骨架与闭合事件不得被背压丢弃。
    assert "tool-input-start" in received_types
    assert "tool-input-available" in received_types
    assert "finish-step" in received_types
    assert "finish" in received_types
    # finish 必须是最后一个数据 chunk：不得以缺少闭合的截断流结束。
    assert received_types[-1] == "finish"


def test_oversize_plain_run_still_closes_with_finish(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-13（探针二）：普通非 Deferred run 超过候选缓冲上限仍完整闭合。

    旧实现在 held 溢出后静默丢弃后续官方 chunk，普通 run 会得到一个正常
    EOF 但没有 finish 的不完整流，也没有 outbox 批次触发前端重同步。修复后
    溢出的结构闭合事件进入有界尾部保留区保证补发：流必须以 finish-step +
    finish 闭合，history 正常保存。
    """

    from erp_web.services.vercel_ai_ui_service import (
        MAX_HELD_CHUNKS,
        _chunk_payload,
    )

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "f7" + "0" * 30
    fragment_count = MAX_HELD_CHUNKS + 900

    async def oversize_plain_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            for index in range(fragment_count):
                yield f"片段 {index}。"
            return
        raise AssertionError("该回合不应有第二个模型轮")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=oversize_plain_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "oversize-plain", "超长回复")).encode(),
    )

    received_types: list[str] = []

    def write(chunk: bytes) -> None:
        payload = _chunk_payload(chunk.decode("utf-8")) or {}
        chunk_type = str(payload.get("type") or "")
        if chunk_type:
            received_types.append(chunk_type)

    asyncio.run(run.stream(write))

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    assert context.pydantic_messages.get(conversation) is not None

    # 非 Deferred run 没有 outbox 批次：官方流自身必须完整闭合。
    assert received_types[0] == "start"
    assert received_types[1] == "start-step"
    assert "finish-step" in received_types
    assert "finish" in received_types
    assert received_types[-1] == "finish"


def _run_text_then_task_handshake(
    chat_service: dict[str, Any],
    conversation: str,
    text_fragments: list[str],
    tool_call_id: str,
) -> str:
    """模型先输出给定文本片段，再受理 global_task_start；返回完整 SSE 文本。"""

    service = chat_service["service"]

    async def text_then_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            for fragment in text_fragments:
                yield fragment
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=text_then_task_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "oversize-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    return b"".join(chunks).decode("utf-8")


def test_handshake_single_oversize_chunk_degrades_to_resync_only_batch(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-16（单条超限）：合法 70 KiB 官方事件不得让握手失败。

    旧实现把本轮标记 failed，已原子创建的 Task/link 永远无法提交，最终由
    provisional sweep 取消。修复后降级为 resync-only 批次：同一事务保存
    history、置 ready、递增 history_version 并写入空事件 outbox。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import (
        MAX_OUTBOX_EVENT_CHUNK_BYTES,
    )

    context = get_context()
    conversation = "conversation_global_chat_" + "f1" + "0" * 30
    oversize_text = "大" * (MAX_OUTBOX_EVENT_CHUNK_BYTES + 5000)

    text = _run_text_then_task_handshake(
        chat_service, conversation, [oversize_text], "call-oversize-single"
    )
    assert '"type":"finish"' in text

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    assert claim.error_code == ""

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert context.pydantic_messages.get(conversation) is not None

    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert len(batches) == 1
    assert batches[0].kind == "deferred_handshake"
    # resync-only 批次：空事件列表，订阅端推进游标并重读 /ui-messages。
    assert list(batches[0].events) == []


def test_handshake_total_batch_over_cap_degrades_to_resync_only_batch(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-16（总量超限）：单条不超限但总量超 1 MiB 同样确定性降级。"""

    from erp_web.stores.pydantic_deferred_task_link_store import (
        MAX_OUTBOX_EVENT_CHUNK_BYTES,
        MAX_OUTBOX_EVENT_TOTAL_BYTES,
    )

    context = get_context()
    conversation = "conversation_global_chat_" + "f2" + "0" * 30
    fragment = "流" * (MAX_OUTBOX_EVENT_CHUNK_BYTES // 3)
    fragment_count = (
        MAX_OUTBOX_EVENT_TOTAL_BYTES // len(fragment.encode("utf-8"))
    ) + 4

    text = _run_text_then_task_handshake(
        chat_service,
        conversation,
        [fragment] * fragment_count,
        "call-oversize-total",
    )
    assert '"type":"finish"' in text

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"

    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert len(batches) == 1
    assert list(batches[0].events) == []


def test_handshake_normal_batch_keeps_terminal_segment_events(
    chat_service: dict[str, Any],
) -> None:
    """报告 A-16（正常批次）：未超限时批次仍携带有界终态段，不走降级。"""

    context = get_context()
    conversation = "conversation_global_chat_" + "f3" + "0" * 30

    text = _run_text_then_task_handshake(
        chat_service, conversation, ["正常文本。"], "call-normal-batch"
    )
    assert '"type":"finish"' in text

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"

    batches = context.ai_event_outbox.list_after(
        conversation,
        after_history_version=0,
    )
    assert len(batches) == 1
    assert batches[0].kind == "deferred_handshake"
    # 正常批次不降级：终态段非空且包含回合闭合事件。
    assert batches[0].events
    assert any('"type":"finish"' in event for event in batches[0].events)


def test_handshake_publishes_no_tool_state_before_commit_transaction(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验收探针：组合事务入口前客户端不得收到任何未提交工具状态。

    旧实现在 CallDeferred 抛出后才开始缓冲，tool-input-start、
    tool-input-delta、tool-input-available 在事务前就已写客户端，未提交的
    global_task_start 会以“任务已受理”短暂出现。修复后缓冲边界前移到本
    run 第一个工具调用的 tool-input-start（报告 A-01：覆盖先行 Direct 工具
    的多工具回合）：提交前允许到达的只有惰性信封（start/start-step，错误
    闭合流同样包含同类信号），不得出现任何 tool-input-* 或成功终态。
    """

    from erp_web.services.vercel_ai_ui_service import _chunk_payload

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "b1" + "0" * 30
    tool_call_id = "call-pre-commit-zero"

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=start_task_model)

    chunks: list[bytes] = []
    pre_commit_chunks: list[list[bytes]] = []
    original_commit = context.deferred_task_links.commit_initial_deferred_history

    def recording_commit(*args: Any, **kwargs: Any) -> int:
        pre_commit_chunks.append(list(chunks))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        context.deferred_task_links,
        "commit_initial_deferred_history",
        recording_commit,
    )

    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "pre-commit", "创建任务")).encode(),
    )
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 组合事务入口前：工具输入骨架与临时内容可实时下发，但
    # finish-step/finish/tool-output 等结构化成功终态不得提前发布。
    assert len(pre_commit_chunks) == 1
    pre_commit_types = [
        (_chunk_payload(chunk.decode("utf-8")) or {}).get("type")
        for chunk in pre_commit_chunks[0]
    ]
    assert pre_commit_types[:2] == ["start", "start-step"]
    for forbidden in ("finish", "finish-step", "tool-output-available"):
        assert forbidden not in pre_commit_types, (
            f"提交前不得发布结构化成功终态：{forbidden}"
        )

    # 提交成功后，控制工具段与终态事件才按官方顺序送达。
    assert '"type":"tool-input-start"' in text
    assert '"type":"tool-input-delta"' in text
    assert '"type":"tool-input-available"' in text
    assert tool_call_id in text
    assert '"type":"finish"' in text

    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"


def test_rejected_control_tool_call_flushes_held_segment(
    chat_service: dict[str, Any],
) -> None:
    """控制工具调用未抛 CallDeferred 时，缓冲段不得被吞掉。

    ``global_task_start`` 参数无效会以校验错误终结 run：缓冲段
    （tool-input-start 及其后的闭合事件）必须仍按原顺序送达客户端，
    回合以官方错误闭合，conversation 不被锁定。
    """

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "b2" + "0" * 30
    tool_call_id = "call-rejected-args"

    async def rejected_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    # 缺少 steps/platform：类型化请求校验失败，不会 CallDeferred。
                    json_args=json.dumps(
                        {"goal": "缺少步骤的任务"}, ensure_ascii=False
                    ),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("校验失败后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(
        stream_function=rejected_task_model
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "rejected-1", "创建任务")).encode(),
    )
    chunks: list[bytes] = []
    asyncio.run(run.stream(chunks.append))
    text = b"".join(chunks).decode("utf-8")

    # 缓冲段按原顺序补发：工具调用开始与错误闭合都送达客户端。
    assert '"type":"tool-input-start"' in text
    assert tool_call_id in text
    assert '"finishReason":"error"' in text

    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "failed"
    assert claim.error_code != ""
    # 未抛 CallDeferred：没有任务与 link 被创建，conversation 不被锁定。
    assert context.deferred_task_links.active_for_conversation(conversation) is None


def test_cancellation_during_handshake_still_commits_deferred_history(
    chat_service: dict[str, Any],
) -> None:
    """报告 §8-3：握手期间客户端取消，producer 仍完成并提交。"""

    service = chat_service["service"]
    context = get_context()
    conversation = "conversation_global_chat_" + "c" * 32
    tool_call_id = "call-cancel-handshake"

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=start_task_model)
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "cancel-hs", "创建任务")).encode(),
    )

    async def cancel_observer_then_wait() -> None:
        task = asyncio.create_task(run.stream(lambda _chunk: None))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(1000):
            current = context.deferred_task_links.active_for_conversation(
                conversation
            )
            if current is not None and current.link_status == "ready":
                break
            await asyncio.sleep(0.01)
        for _ in range(1000):
            if not context.chat_runs.is_active(conversation):
                break
            await asyncio.sleep(0.01)

    asyncio.run(cancel_observer_then_wait())

    # producer 独立完成了握手提交：link ready、history 冻结、outbox 有批次。
    link = context.deferred_task_links.active_for_conversation(conversation)
    assert link is not None
    assert link.link_status == "ready"
    assert link.tool_call_id == tool_call_id
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.history_version == link.history_version
    batches = context.ai_event_outbox.list_after(
        conversation, after_history_version=0
    )
    assert [batch.kind for batch in batches] == ["deferred_handshake"]
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"
    assert context.chat_runs.is_active(conversation) is False


def test_ui_messages_normalizes_open_deferred_global_task_start(
    chat_service: dict[str, Any],
) -> None:
    """报告 §8-10：重载时开放 Deferred 不误显示为通用审批。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "a9" + "0" * 30
    tool_call_id = "call-ui-norm"

    async def start_task_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {
                0: DeltaToolCall(
                    name="global_task_start",
                    json_args=_deferred_start_args(),
                    tool_call_id=tool_call_id,
                )
            }
            return
        raise AssertionError("Deferred 暂停后不应再次请求模型")

    chat_service["model"]["model"] = FunctionModel(stream_function=start_task_model)
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "norm-1", "创建任务")).encode(),
    )
    asyncio.run(run.stream(lambda _chunk: None))

    payload = service.dump_ui_messages(conversation)
    start_parts = [
        part
        for message in payload["messages"]
        for part in message["parts"]
        if part.get("type") == "tool-global_task_start"
    ]
    assert start_parts
    for part in start_parts:
        # 官方 Adapter 的 approval-requested 已按 Deferred ledger 归一化。
        assert part["state"] == "input-available"
        assert "approval" not in part


def test_safe_json_body_with_raw_reads_body_once() -> None:
    import io

    from erp_web.http_request import safe_json_body_with_raw

    payload = {"trigger": "submit-message", "id": "x", "messages": []}
    raw = json.dumps(payload).encode("utf-8")

    class FakeHandler:
        def __init__(self) -> None:
            self.headers = {
                "Content-Length": str(len(raw)),
                "Content-Type": "application/json",
            }
            self.rfile = io.BytesIO(raw)

        def read_tracking(self) -> None:
            pass

    handler = FakeHandler()
    parsed, raw_out = safe_json_body_with_raw(handler)
    assert parsed == payload
    assert raw_out == raw
    # 单次读取后 socket 流已耗尽，二次读取返回空。
    assert handler.rfile.read() == b""
    assert VERCEL_SDK_VERSION == 7
