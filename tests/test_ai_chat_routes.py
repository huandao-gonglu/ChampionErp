from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import http.client
import pytest
from pydantic_ai.exceptions import ModelAPIError
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
from erp_web.services.vercel_ai_ui_service import VERCEL_SDK_VERSION


CHAT_PATH = "/api/v1/ai-chat/runs"
CONVERSATION = "conversation_global_chat_" + "f" * 32

#: global.chat 主 Agent 工具 = Direct 只读能力 + 任务控制能力（动态同源）。
EXPECTED_GLOBAL_CHAT_TOOLS = set(GLOBAL_CHAT_DIRECT_CAPABILITIES) | set(
    GLOBAL_TASK_CONTROL_CATALOG.tools
)


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


def test_client_disconnect_marks_claim_cancelled_and_releases(
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

    # 断连不应把异常抛给调用方；收尾仍要完成。
    asyncio.run(run.stream(broken_writer))

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "cancelled"


def test_coroutine_cancellation_marks_claim_cancelled_and_releases(
    chat_service: dict[str, Any],
) -> None:
    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "4" * 32

    async def wait_until_cancelled(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        await asyncio.Event().wait()
        yield "不会返回"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=wait_until_cancelled
    )
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "cancel-1", "取消测试")).encode(),
    )

    async def cancel_active_run() -> None:
        task = asyncio.create_task(run.stream(lambda _chunk: None))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_active_run())

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "cancelled"


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


def test_disconnect_after_delta_persists_validated_partial_history(
    chat_service: dict[str, Any],
) -> None:
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
    assert claim.status == "cancelled"
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    messages = ModelMessagesTypeAdapter.validate_json(history.messages_json)
    assert any(
        isinstance(message, ModelResponse) and message.state == "interrupted"
        for message in messages
    )


def test_disconnect_during_multi_tool_turn_persists_complete_tool_pairs(
    chat_service: dict[str, Any],
) -> None:
    """Provider 返回多工具时，中断历史也必须满足 call/return 成对不变量。"""

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
    chat_server["model"]["model"] = _BlockingModel(
        gate, custom_output_text="增量回复。", call_tools=[]
    )
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
        # 在 finish 之前（模型仍被 gate 阻塞）就应读到响应头与 start chunk。
        deadline = time.monotonic() + 10
        while b'{"type":"start"}' not in buffer:
            if time.monotonic() > deadline:
                raise AssertionError("未在 finish 前收到 start chunk")
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
        # 此时模型尚未产出结果，finish 不应已经出现。
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
    for suffix in ("events", "raw", "children", "wait"):
        status, _ = _get(
            port,
            f"/api/v1/ai-work/conversations/{CONVERSATION}/{suffix}",
        )
        assert status == 404


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
