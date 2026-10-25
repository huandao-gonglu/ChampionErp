"""离线验证原生 SDK 异常链、请求阶段及诊断脱敏。"""
import asyncio
import json
import socket
import ssl
import urllib.error

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
import pytest

from erp_web.context import get_context
from erp_web.services.ai_agent_factory import _safe_agent_error
from erp_web.services.ai_model_errors import direct_model_error_payload, map_pydantic_model_error
from erp_web.services.external_httpx_transport import ManagedAsyncTransport
from erp_web.services.external_request_diagnostics import exception_diagnostics


@pytest.mark.parametrize("stage,failure", [
    ("connect_tcp", httpx.ConnectError),
    ("start_tls", httpx.ConnectError),
    ("send_request_body", httpx.WriteError),
    ("receive_response_headers", httpx.ReadTimeout),
])
def test_native_sdk_preserves_diagnostic_correlation_without_secrets(stage, failure, caplog):
    trace_events = []
    secret = "credential-and-product-private-data"

    async def existing_trace(name, info):
        trace_events.append(name)

    async def respond(request):
        trace = request.extensions["trace"]
        await trace(f"connection.{stage}.started", {"headers": secret})
        try:
            raise OSError(54, secret)
        except OSError as cause:
            error = failure(secret + " https://user:password@provider.invalid/?key=secret")
            await trace(f"connection.{stage}.failed", {"exception": error})
            raise error from cause

    async def attach_trace(request):
        request.extensions["trace"] = existing_trace

    async def invoke():
        async with httpx.AsyncClient(
            transport=ManagedAsyncTransport(platform="ai:test", account_id="offline",
                                           transport_factory=lambda: httpx.MockTransport(respond)),
            event_hooks={"request": [attach_trace]},
        ) as client:
            sdk = AsyncOpenAI(api_key=secret, base_url="https://offline.invalid/v1", http_client=client, max_retries=0)
            model = OpenAIChatModel("offline", provider=OpenAIProvider(openai_client=sdk))
            with pytest.raises(ModelAPIError) as caught:
                await Agent(model).run(secret)
            return caught.value

    error = asyncio.run(invoke())
    displayed = _safe_agent_error(error, validator=None, conversation_id="conversation-test", task_run_id="task-test")
    row = get_context().external_requests.store.query()["items"][0]
    diagnostic = row["result"]["diagnostics"]
    request = diagnostic["external_request"]
    assert displayed.details["external_request"] == request
    assert request["request_id"] == row["id"]
    assert request["operation_id"] == row["operation_id"]
    assert request["trace_event"] == stage + ".failed"
    assert request["phase"] == "await_response_headers"
    assert request.get("http_status") is None and request["elapsed_ms"] >= 0
    assert request["request_bytes"] > 0
    chain = displayed.details["exception_chain"]
    assert chain[0]["type"].endswith("ModelAPIError")
    assert any(item["type"].endswith(failure.__name__) for item in chain)
    assert chain[-1]["errno"] == 54 and chain[-1]["frames"]
    assert len(trace_events) == 2
    assert len(get_context().external_requests.store.query()["items"]) == 1
    dumped = json.dumps(displayed.details) + caplog.text
    assert secret not in dumped and "password" not in dumped and "key=secret" not in dumped
    assert row["id"] in caplog.text
    direct = map_pydantic_model_error(error, model_id="offline", model_name="offline",
                                      api_style="responses", base_url="https://offline.invalid")
    assert direct_model_error_payload(direct)["details"] == displayed.details


def test_stream_failure_records_received_bytes_and_does_not_overwrite_on_close():
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: partial\n\n"
            raise httpx.RemoteProtocolError("peer closed connection (incomplete chunked read)")

    async def invoke():
        transport = ManagedAsyncTransport(platform="ai:test", account_id="offline", transport_factory=lambda:
            httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=BrokenStream())))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.RemoteProtocolError):
                async with client.stream("POST", "https://offline.invalid/v1/responses", json={}) as response:
                    await response.aread()

    asyncio.run(invoke())
    row = get_context().external_requests.store.query()["items"][0]
    diagnostic = row["result"]["diagnostics"]
    assert diagnostic["external_request"]["response_bytes"] == len(b"data: partial\n\n")
    assert diagnostic["external_request"]["http_status"] == 200
    assert diagnostic["external_request"]["phase"] == "read_response_body"
    assert diagnostic["exception_chain"][0]["reason"] == "INCOMPLETE_CHUNKED_READ"
    assert row["result"]["outcome"] == "outcome_unknown"


def test_diagnostics_handles_urllib_reason_and_cyclic_causes_without_raw_text():
    dns = socket.gaierror(-2, "private-host-and-key")
    error = urllib.error.URLError(dns)
    details = exception_diagnostics(error)
    assert details["exception_chain"][-1]["reason"] == "DNS_RESOLUTION_FAILED"
    assert "private-host" not in json.dumps(details)
    error.__cause__ = error
    assert len(exception_diagnostics(error)["exception_chain"]) == 1


def test_native_httpcore_trace_identifies_server_disconnect_before_headers():
    async def disconnect(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.close()
        await writer.wait_closed()

    async def invoke():
        server = await asyncio.start_server(disconnect, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            async with httpx.AsyncClient(transport=ManagedAsyncTransport(
                platform="ai:test", account_id="offline"), timeout=3) as client:
                with pytest.raises(httpx.RemoteProtocolError):
                    await client.post(f"http://127.0.0.1:{port}/v1/responses", json={})

    asyncio.run(invoke())
    diagnostic = get_context().external_requests.store.query()["items"][0]["result"]["diagnostics"]
    assert diagnostic["external_request"]["trace_event"] == "receive_response_headers.failed"
    assert diagnostic["exception_chain"][0]["reason"] == "SERVER_DISCONNECTED_BEFORE_RESPONSE"


def test_tls_certificate_diagnostics_keep_verification_code_without_private_message():
    error = ssl.SSLCertVerificationError(1, "private-certificate-content")
    error.verify_code = 20
    details = exception_diagnostics(error)
    assert details["exception_chain"][0]["tls_verify_code"] == 20
    assert "private-certificate" not in json.dumps(details)
