"""通用 presentation 服务、routes 与 HTTP 公共边界（claim/contextvar/收尾）。"""

from __future__ import annotations

import asyncio
import json
import re
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest

from erp_web import http_routes
from erp_web.http_route_units import ai_presentation_routes
from erp_web.services import ai_presentation_service
from erp_web.services.ai_presentation_context import (
    current_presentation_context,
)
from erp_web.services.ai_presentation_registry import (
    COMPLETED,
    FAILED,
    AiPresentationRegistry,
)
from erp_web.services.ai_presentation_service import (
    CLAIM_REJECTED_CODE,
    PRESENTATION_ID_PATTERN,
    RegistryAiPresentationObserver,
    claim_presentation_scope,
    reserve_presentation,
    sanitize_display_title,
)


# ---------------------------------------------------------------------------
# 基础：display_title 清洗与 reservation
# ---------------------------------------------------------------------------


def test_sanitize_display_title_strips_control_chars_and_limits_length() -> None:
    assert sanitize_display_title("AI 填充属性") == "AI 填充属性"
    assert sanitize_display_title("  AI\n\t填充\x00属性  ") == "AI填充属性"
    assert sanitize_display_title("") == ""
    assert sanitize_display_title(None) == ""
    long_title = "标" * 500
    assert len(sanitize_display_title(long_title)) == 80


def test_reserve_presentation_returns_public_descriptor() -> None:
    registry = AiPresentationRegistry()
    payload = reserve_presentation(registry, display_title="AI 填充属性")

    assert payload["ok"] is True
    assert payload["status"] == "reserved"
    assert payload["display_title"] == "AI 填充属性"
    assert PRESENTATION_ID_PATTERN.fullmatch(str(payload["presentation_id"]))
    assert re.fullmatch(
        r"conversation_[0-9a-f]{32}", str(payload["conversation_id"])
    )
    descriptor = registry.descriptor(str(payload["presentation_id"]))
    assert descriptor is not None and descriptor["status"] == "reserved"


def test_reserve_presentation_defaults_title() -> None:
    registry = AiPresentationRegistry()
    payload = reserve_presentation(registry, display_title="\x00\x01")
    assert payload["display_title"] == "AI 任务"


# ---------------------------------------------------------------------------
# claim 与 root scope
# ---------------------------------------------------------------------------


def test_claim_scope_rejects_malformed_unknown_and_reused_ids() -> None:
    registry = AiPresentationRegistry()
    reserved = reserve_presentation(registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])

    assert claim_presentation_scope(registry, presentation_id="not-a-presentation") is None
    assert claim_presentation_scope(registry, presentation_id="presentation_zzzz") is None
    assert claim_presentation_scope(registry, presentation_id="") is None

    scope = claim_presentation_scope(registry, presentation_id=presentation_id)
    assert scope is not None
    assert scope.presentation_id == presentation_id
    assert scope.conversation_id == reserved["conversation_id"]
    assert scope.origin == "business.ui"
    assert scope.is_root_scope is True
    assert scope.root_run_id
    assert isinstance(scope.observer, RegistryAiPresentationObserver)

    # 同一 presentation 只能 claim 一次。
    assert claim_presentation_scope(registry, presentation_id=presentation_id) is None


# ---------------------------------------------------------------------------
# route unit：reserve / status / stream
# ---------------------------------------------------------------------------


class _FakeHandler:
    def __init__(
        self,
        body: dict[str, Any] | None = None,
        *,
        path: str = "/api/v1/ai-presentations",
    ) -> None:
        self._body = body or {}
        self.path = path
        self.status: int | None = None
        self.sse_headers: dict[str, str] = {}
        self.chunks: list[bytes] = []
        self.json_payloads: list[tuple[dict[str, Any], int]] = []

    def read_body(self) -> dict[str, Any]:
        return self._body

    def send_response(self, status: int, message: str | None = None) -> None:
        self.status = status

    def end_headers(self) -> None:
        pass

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.json_payloads.append((payload, status))

    def send_sse_headers(self, headers: dict[str, str], status: int = 200) -> None:
        self.sse_headers = dict(headers)

    def write_sse_chunk(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


@pytest.fixture
def isolated_registry(monkeypatch):
    registry = AiPresentationRegistry()
    monkeypatch.setattr(
        ai_presentation_routes,
        "get_context",
        lambda: SimpleNamespace(ai_presentations=registry),
    )
    return registry


def test_reserve_route_returns_descriptor(isolated_registry) -> None:
    handler = _FakeHandler({"display_title": "AI 填充属性"})
    assert (
        ai_presentation_routes.handle_post(
            handler, SimpleNamespace(path="/api/v1/ai-presentations")
        )
        is True
    )
    payload, status = handler.json_payloads[-1]
    assert status == 200
    assert payload["ok"] is True
    assert payload["status"] == "reserved"
    assert PRESENTATION_ID_PATTERN.fullmatch(payload["presentation_id"])

    assert (
        ai_presentation_routes.handle_post(
            _FakeHandler(), SimpleNamespace(path="/api/v1/other")
        )
        is False
    )


def test_status_route_is_metadata_only(isolated_registry) -> None:
    handler = _FakeHandler()
    parsed = SimpleNamespace(
        path="/api/v1/ai-presentations/presentation_missing", query=""
    )
    ai_presentation_routes.handle_get(handler, parsed)
    payload, status = handler.json_payloads[-1]
    assert status == 404
    assert payload["error_code"] == "AI_PRESENTATION_NOT_FOUND"

    reserved = reserve_presentation(isolated_registry, display_title="AI 填充属性")
    presentation_id = str(reserved["presentation_id"])
    handler = _FakeHandler()
    parsed = SimpleNamespace(
        path=f"/api/v1/ai-presentations/{presentation_id}", query=""
    )
    ai_presentation_routes.handle_get(handler, parsed)
    payload, status = handler.json_payloads[-1]
    assert status == 200
    assert payload["presentation_id"] == presentation_id
    assert payload["conversation_id"] == reserved["conversation_id"]
    assert payload["status"] == "reserved"
    assert payload["terminal"] is False
    assert payload["had_agent_run"] is False
    # 通用 status route 不拥有业务结果。
    assert "result" not in payload


def test_stream_route_replays_chunks_and_releases_lease(isolated_registry) -> None:
    reserved = reserve_presentation(isolated_registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])
    assert claim_presentation_scope(isolated_registry, presentation_id=presentation_id)
    isolated_registry.publish_chunk(presentation_id, b"data: one\n\n")
    isolated_registry.finish_request(presentation_id)

    handler = _FakeHandler()
    parsed = SimpleNamespace(
        path=f"/api/v1/ai-presentations/{presentation_id}/stream", query=""
    )
    assert ai_presentation_routes.handle_get(handler, parsed) is True
    assert handler.sse_headers.get("Content-Type")
    assert handler.chunks == [b"data: one\n\n"]

    # 流结束后 lease 释放：再次 attach 仍可 replay（observe-only）。
    second = _FakeHandler()
    assert ai_presentation_routes.handle_get(second, parsed) is True
    assert second.chunks == [b"data: one\n\n"]


def test_stream_route_returns_204_for_unknown_expired_or_busy(
    isolated_registry,
) -> None:
    handler = _FakeHandler()
    parsed = SimpleNamespace(
        path="/api/v1/ai-presentations/presentation_missing/stream", query=""
    )
    ai_presentation_routes.handle_get(handler, parsed)
    assert handler.status == 204

    reserved = reserve_presentation(isolated_registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])
    assert isolated_registry.acquire_lease(presentation_id) is True
    busy = _FakeHandler()
    ai_presentation_routes.handle_get(
        busy,
        SimpleNamespace(
            path=f"/api/v1/ai-presentations/{presentation_id}/stream", query=""
        ),
    )
    assert busy.status == 204
    isolated_registry.release_lease(presentation_id)


def test_stream_route_disconnect_only_releases_lease(isolated_registry) -> None:
    reserved = reserve_presentation(isolated_registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])
    isolated_registry.publish_chunk(presentation_id, b"data: one\n\n")

    class _DisconnectingHandler(_FakeHandler):
        def write_sse_chunk(self, chunk: bytes) -> None:
            raise OSError("client disconnected")

    handler = _DisconnectingHandler()
    parsed = SimpleNamespace(
        path=f"/api/v1/ai-presentations/{presentation_id}/stream", query=""
    )
    ai_presentation_routes.handle_get(handler, parsed)
    # 断连只释放 lease；presentation 生命周期仍由业务请求收尾。
    assert isolated_registry.acquire_lease(presentation_id) is True
    isolated_registry.release_lease(presentation_id)
    assert isolated_registry.is_terminal(presentation_id) is False


def test_routes_reject_traversal_and_query(isolated_registry) -> None:
    reserved = reserve_presentation(isolated_registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])
    for path in (
        "/api/v1/ai-presentations/a%2Fb",
        "/api/v1/ai-presentations/../x",
        "/api/v1/ai-presentations/a/stream/../x",
    ):
        handler = _FakeHandler()
        ai_presentation_routes.handle_get(handler, SimpleNamespace(path=path, query=""))
        assert handler.json_payloads[-1][1] == 404, path

    handler = _FakeHandler()
    ai_presentation_routes.handle_get(
        handler,
        SimpleNamespace(
            path=f"/api/v1/ai-presentations/{presentation_id}", query="token=1"
        ),
    )
    assert handler.json_payloads[-1][1] == 404

    handler = _FakeHandler()
    assert (
        ai_presentation_routes.handle_get(
            handler, SimpleNamespace(path="/api/state", query="")
        )
        is False
    )


# ---------------------------------------------------------------------------
# observer：官方编码发布（fake 官方事件流）
# ---------------------------------------------------------------------------


class _FakeEventStream:
    """模拟官方 VercelAIEventStream：transform 透传事件、异常转 error chunk。"""

    content_type = "text/event-stream"
    response_headers: dict[str, str] = {}

    def transform_stream(self, source, on_complete=None):
        async def _transformed():
            try:
                async for event in source:
                    yield ("ui", event)
            except Exception as exc:  # 官方逻辑把错误转换为 error/finish chunk
                yield ("error", str(exc))

        return _transformed()

    def encode_stream(self, stream):
        async def _encoded():
            async for kind, value in stream:
                yield f"data: {kind}:{value}\n\n"

        return _encoded()


@pytest.fixture
def fake_event_stream(monkeypatch):
    stream = _FakeEventStream()
    monkeypatch.setattr(
        ai_presentation_service, "new_event_stream", lambda conversation_id: stream
    )
    return stream


def _reserve_and_claim(registry: AiPresentationRegistry) -> str:
    reserved = reserve_presentation(registry, display_title="x")
    presentation_id = str(reserved["presentation_id"])
    assert claim_presentation_scope(registry, presentation_id=presentation_id)
    return presentation_id


def test_observer_publishes_encoded_chunks_and_passes_events_through(
    isolated_registry, fake_event_stream
) -> None:
    presentation_id = _reserve_and_claim(isolated_registry)
    observer = RegistryAiPresentationObserver(
        registry=isolated_registry,
        presentation_id=presentation_id,
        conversation_id="conversation_a",
    )

    async def native_events():
        yield "event-1"
        yield "event-2"

    async def run():
        published = observer.observe_native_events(native_events())
        return [event async for event in published]

    assert asyncio.run(run()) == ["event-1", "event-2"]
    chunks, _cursor, closed = isolated_registry.read_chunks(
        presentation_id, 0, wait_timeout=0.05
    )
    assert chunks == [b"data: ui:event-1\n\n", b"data: ui:event-2\n\n"]
    assert closed is False  # 终态由 HTTP 边界收尾，observer 不关闭流


def test_observer_publishes_error_chunks_for_source_failure(
    isolated_registry, fake_event_stream
) -> None:
    presentation_id = _reserve_and_claim(isolated_registry)
    observer = RegistryAiPresentationObserver(
        registry=isolated_registry,
        presentation_id=presentation_id,
        conversation_id="conversation_a",
    )

    async def failing_events():
        yield "event-1"
        raise RuntimeError("agent failed")

    async def run():
        published = observer.observe_native_events(failing_events())
        received = []
        with pytest.raises(RuntimeError):
            async for event in published:
                received.append(event)
        return received

    assert asyncio.run(run()) == ["event-1"]
    chunks, _cursor, _closed = isolated_registry.read_chunks(
        presentation_id, 0, wait_timeout=0.05
    )
    assert chunks == [b"data: ui:event-1\n\n", b"data: error:agent failed\n\n"]


def test_observer_stops_publishing_on_buffer_overflow_without_breaking_agent(
    fake_event_stream,
) -> None:
    registry = AiPresentationRegistry(max_buffered_chunks=1)
    presentation_id = _reserve_and_claim(registry)
    observer = RegistryAiPresentationObserver(
        registry=registry,
        presentation_id=presentation_id,
        conversation_id="conversation_a",
    )

    async def native_events():
        yield "event-1"
        yield "event-2"
        yield "event-3"

    async def run():
        published = observer.observe_native_events(native_events())
        return [event async for event in published]

    # 溢出只降级展示：Agent 事件仍完整透传。
    assert asyncio.run(run()) == ["event-1", "event-2", "event-3"]
    payload = registry.status_payload(presentation_id)
    assert payload["status"] == FAILED
    assert payload["error_code"] == "AI_PRESENTATION_BUFFER_OVERFLOW"


def test_observer_publish_error_chunks_before_stream_start(
    isolated_registry, fake_event_stream
) -> None:
    presentation_id = _reserve_and_claim(isolated_registry)
    observer = RegistryAiPresentationObserver(
        registry=isolated_registry,
        presentation_id=presentation_id,
        conversation_id="conversation_a",
    )

    asyncio.run(observer.publish_error_chunks(RuntimeError("assembly failed")))
    chunks, _cursor, _closed = isolated_registry.read_chunks(
        presentation_id, 0, wait_timeout=0.05
    )
    assert chunks == [b"data: error:assembly failed\n\n"]


def test_observer_lifecycle_updates_registry_status(
    isolated_registry, fake_event_stream
) -> None:
    presentation_id = _reserve_and_claim(isolated_registry)
    observer = RegistryAiPresentationObserver(
        registry=isolated_registry,
        presentation_id=presentation_id,
        conversation_id="conversation_a",
    )

    observer.run_started(
        run_id="attempt_1", parent_run_id="", use_case_id="u", label="l"
    )
    assert isolated_registry.had_agent_run(presentation_id) is True
    assert isolated_registry.status_payload(presentation_id)["status"] == "running"

    observer.running(run_id="attempt_1")
    observer.tool_activity(run_id="attempt_1", tool_name="search_category")
    observer.finalizing(run_id="attempt_1")
    assert (
        isolated_registry.status_payload(presentation_id)["status"] == "finalizing"
    )

    # observer 不标记终态：终态由 HTTP 边界 finish_request 收尾。
    observer.completed(run_id="attempt_1")
    assert isolated_registry.is_terminal(presentation_id) is False
    isolated_registry.finish_request(presentation_id)
    assert isolated_registry.status_payload(presentation_id)["status"] == COMPLETED


# ---------------------------------------------------------------------------
# HTTP 公共边界：X-AI-Presentation-ID claim / contextvar / request 收尾
# ---------------------------------------------------------------------------


def _headers(**values: str) -> Message:
    headers = Message()
    for name, value in values.items():
        headers[name.replace("_", "-")] = value
    return headers


class _BoundaryHandler:
    def __init__(self, headers: Message) -> None:
        self.path = "/api/test-business"
        self.headers = headers
        self.sent: list[tuple[dict[str, Any], int]] = []
        self.statuses: list[int] = []
        self.response_status: int | None = None

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.sent.append((payload, status))
        # 与真实 Handler 一致：send_json 经 send_response 记录响应状态。
        self.send_response(status)

    def send_response(self, status: int, message: str | None = None) -> None:
        self.statuses.append(status)
        self.response_status = status

    def end_headers(self) -> None:
        pass


@pytest.fixture
def boundary(monkeypatch):
    registry = AiPresentationRegistry()
    monkeypatch.setattr(
        http_routes, "get_context", lambda: SimpleNamespace(ai_presentations=registry)
    )
    return registry


def _loopback_headers(presentation_id: str | None = None) -> Message:
    values = {"Host": "127.0.0.1:5050"}
    if presentation_id:
        values["X_AI_Presentation_ID"] = presentation_id
    return _headers(**values)


def _register_business_route(monkeypatch, handler_fn) -> None:
    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=handler_fn),
    )


def test_boundary_rejects_invalid_or_unknown_presentation_with_stable_409(
    boundary, monkeypatch
) -> None:
    def business_handler(handler, parsed) -> bool:
        handler.send_json({"ok": True})
        return True

    _register_business_route(monkeypatch, business_handler)

    for bad in ("not-valid", "presentation_zzz", "presentation_" + "0" * 31):
        handler = _BoundaryHandler(_loopback_headers(bad))
        http_routes.handle_post(handler)  # type: ignore[arg-type]
        payload, status = handler.sent[-1]
        assert status == 409
        assert payload["error_code"] == CLAIM_REJECTED_CODE

    handler = _BoundaryHandler(
        _loopback_headers("presentation_" + "0" * 32)
    )
    http_routes.handle_post(handler)  # type: ignore[arg-type]
    payload, status = handler.sent[-1]
    assert status == 409
    assert payload["error_code"] == CLAIM_REJECTED_CODE


def test_boundary_binds_contextvar_and_finishes_request(boundary, monkeypatch) -> None:
    reserved = reserve_presentation(boundary, display_title="AI 填充属性")
    presentation_id = str(reserved["presentation_id"])

    observed: dict[str, Any] = {}

    def business_handler(handler, parsed) -> bool:
        context = current_presentation_context()
        observed["context"] = context
        handler.send_json({"ok": True})
        return True

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=business_handler),
    )

    handler = _BoundaryHandler(_loopback_headers(presentation_id))
    http_routes.handle_post(handler)  # type: ignore[arg-type]

    context = observed["context"]
    assert context is not None
    assert context.presentation_id == presentation_id
    assert context.origin == "business.ui"
    assert context.is_root_scope is True
    # handler 返回后 contextvar 已恢复。
    assert current_presentation_context() is None
    # 请求未产生 Agent run：空 presentation 确定关闭。
    payload = boundary.status_payload(presentation_id)
    assert payload["status"] == COMPLETED
    assert payload["terminal"] is True
    assert payload["had_agent_run"] is False

    # 已 claim 的 presentation 再次请求：稳定 409。
    replay = _BoundaryHandler(_loopback_headers(presentation_id))
    http_routes.handle_post(replay)  # type: ignore[arg-type]
    assert replay.sent[-1][1] == 409


def test_boundary_marks_failed_when_handler_raises(boundary, monkeypatch) -> None:
    reserved = reserve_presentation(boundary, display_title="x")
    presentation_id = str(reserved["presentation_id"])

    def failing_handler(handler, parsed) -> bool:
        context = current_presentation_context()
        assert context is not None
        context.observer.run_started(
            run_id="attempt_1", parent_run_id="", use_case_id="u", label="l"
        )
        raise RuntimeError("infra boom")

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=failing_handler),
    )

    handler = _BoundaryHandler(_loopback_headers(presentation_id))
    http_routes.handle_post(handler)  # type: ignore[arg-type]

    payload, status = handler.sent[-1]
    assert status == 500
    final = boundary.status_payload(presentation_id)
    assert final["status"] == FAILED
    assert final["had_agent_run"] is True


def test_boundary_business_failure_response_still_completes_presentation(
    boundary, monkeypatch
) -> None:
    reserved = reserve_presentation(boundary, display_title="x")
    presentation_id = str(reserved["presentation_id"])

    def business_failure_handler(handler, parsed) -> bool:
        context = current_presentation_context()
        assert context is not None
        context.observer.run_started(
            run_id="attempt_1", parent_run_id="", use_case_id="u", label="l"
        )
        # 业务判断失败：类型化结果 + 200，不属于基础设施失败。
        handler.send_json({"ok": False, "error_code": "NO_CATEGORY_MATCH"})
        return True

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=business_failure_handler),
    )

    handler = _BoundaryHandler(_loopback_headers(presentation_id))
    http_routes.handle_post(handler)  # type: ignore[arg-type]

    assert handler.sent[-1][1] == 200
    final = boundary.status_payload(presentation_id)
    assert final["status"] == COMPLETED
    assert final["had_agent_run"] is True


def test_boundary_marks_failed_when_business_response_is_http_error(
    boundary, monkeypatch
) -> None:
    reserved = reserve_presentation(boundary, display_title="x")
    presentation_id = str(reserved["presentation_id"])

    def subject_error_handler(handler, parsed) -> bool:
        # subject 错误（如商品/草稿不存在）：正常返回但响应 4xx。
        handler.send_json(
            {"ok": False, "error_code": "PRODUCT_NOT_FOUND"},
            404,
        )
        return True

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=subject_error_handler),
    )

    handler = _BoundaryHandler(_loopback_headers(presentation_id))
    http_routes.handle_post(handler)  # type: ignore[arg-type]

    assert handler.sent[-1][1] == 404
    final = boundary.status_payload(presentation_id)
    # 正常返回的 4xx 也属于请求失败，不得标记为 completed。
    assert final["status"] == FAILED
    assert final["terminal"] is True
    assert final["had_agent_run"] is False


def test_boundary_without_header_does_not_touch_presentations(
    boundary, monkeypatch
) -> None:
    dispatched: list[bool] = []

    def plain_handler(handler, parsed) -> bool:
        dispatched.append(True)
        assert current_presentation_context() is None
        handler.send_json({"ok": True})
        return True

    monkeypatch.setitem(
        http_routes.POST_ROUTE_UNITS_BY_PATH,
        "/api/test-business",
        SimpleNamespace(handle_post=plain_handler),
    )

    handler = _BoundaryHandler(_headers(Host="127.0.0.1:5050"))
    http_routes.handle_post(handler)  # type: ignore[arg-type]
    assert dispatched == [True]
    assert handler.sent[-1][1] == 200
    assert boundary.status_payload("anything") is None


def test_boundary_metadata_rejection_does_not_consume_reservation(
    boundary, monkeypatch
) -> None:
    def business_handler(handler, parsed) -> bool:
        handler.send_json({"ok": True})
        return True

    _register_business_route(monkeypatch, business_handler)

    reserved = reserve_presentation(boundary, display_title="x")
    presentation_id = str(reserved["presentation_id"])

    handler = _BoundaryHandler(
        _headers(
            Host="127.0.0.1:5050",
            Origin="https://attacker.example",
            X_AI_Presentation_ID=presentation_id,
        )
    )
    http_routes.handle_post(handler)  # type: ignore[arg-type]
    assert handler.sent[-1][1] == 403
    # 跨站请求被拒绝后 reservation 仍然可被合法请求 claim。
    assert boundary.status_payload(presentation_id)["status"] == "reserved"


def test_reserve_contract_registered_for_every_post_route() -> None:
    from erp_web.schemas.requests import REQUEST_CONTRACTS

    assert "/api/v1/ai-presentations" in REQUEST_CONTRACTS
    assert "/api/v1/ai-presentations" in http_routes.POST_API_ROUTES
