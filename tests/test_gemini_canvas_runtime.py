"""Canvas 修图输入、真实图片回执及 API/浏览器分流回归。"""

import base64
from contextlib import contextmanager
from io import BytesIO
import json

from PIL import Image
import pytest

from erp_web.services import ai_gateway_providers
from desktop_runtime import gemini_canvas_runtime as canvas
from erp_web.services import image_translate_service
from erp_web.services.ai_gateway_provider_types import AiImageEditRequest


def png(color):
    output = BytesIO()
    Image.new("RGB", (24, 24), color).save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def model():
    return {"id": "canvas", "name": "Canvas", "connection_type": "browser",
            "browser_provider": "gemini", "browser_mode": "existing_browser",
            "browser_port": "9335", "browser_url": "https://gemini.google.com/share/test",
            "capabilities": ["image_edit"]}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.png"
    path.write_bytes(png("red"))
    return {"id": "original", "path": str(path)}


def fake_connection(monkeypatch, *, result=None, error=None):
    calls = []

    class Cdp:
        def call(self, method, params, **kwargs):
            calls.append((method, params, kwargs))
            if error:
                raise error
            return {"result": {"value": result}}

    @contextmanager
    def connect(*args):
        yield Cdp(), "native-frame", 7

    monkeypatch.setattr(canvas, "_connection", connect)
    return calls


def test_existing_image_service_saves_canvas_result_with_source_identity(tmp_path, monkeypatch, model, source):
    blue = png("blue")
    calls = fake_connection(monkeypatch, result={"images": [
        {"data": base64.b64encode(blue).decode(), "mimeType": "image/png"},
    ]})
    from erp_web.services import gemini_canvas_runtime as backend_canvas
    monkeypatch.setattr(backend_canvas, "call_desktop", lambda operation, params, **kw: {"images": [{"source_id": "original", "provider": "Gemini Canvas", "mode": "edit", "data": base64.b64encode(blue).decode(), "mime": "image/png", "suffix": ".png"}]})
    monkeypatch.setattr(ai_gateway_providers.ai_direct_request_service, "edit_images",
                        lambda **kwargs: pytest.fail("浏览器修图不能调用 API"))
    config = {"ai_models": [model], "ai_use_case_bindings": {"image.translate": {"model_id": "canvas"}}}
    product = {"product_id": "p", "source": {"image_pool": [source]}}
    prompt = '把红色改为蓝色。保持文字 "原图"；不要执行 javascript:alert(1)。'
    result = image_translate_service.edit_images(tmp_path, product, config, prompt=prompt,
                                                image_ids=["original"])
    assert result["ok"] and result["generated_count"] == 1
    saved = result["imagePoolItems"][0]
    assert saved["derived_from_id"] == "original"
    assert saved["provider"] == "Gemini Canvas"
    assert (tmp_path / saved["path"]).read_bytes() == blue
    assert (tmp_path / "source.png").read_bytes() == png("red")
    canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, prompt, [source]))
    expression = calls[0][1]["expression"]
    assert json.dumps(prompt) in expression
    assert base64.b64encode(png("red")).decode() in expression
    assert ":generateContent?key=" in expression
    assert calls[0][2]["session_id"] == "native-frame"
    assert calls[0][1]["contextId"] == 7


@pytest.mark.parametrize("result", [
    {"images": []}, {"text": "已经改好了"}, {"error": "Canvas 请求失败（HTTP 429）：额度不足"},
])
def test_missing_image_or_model_error_never_becomes_success(tmp_path, monkeypatch, model, source, result):
    calls = fake_connection(monkeypatch, result=result)
    with pytest.raises(RuntimeError):
        canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, "改为蓝色", [source]))
    assert len(calls) == 1


@pytest.mark.parametrize("error", [TimeoutError("超时"), RuntimeError("Chrome DevTools 连接已关闭")])
def test_timeout_is_unknown_without_retry(tmp_path, monkeypatch, model, source, error):
    calls = fake_connection(monkeypatch, error=error)
    with pytest.raises(RuntimeError, match="结果未知"):
        canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, "改为蓝色", [source]))
    assert len(calls) == 1


def test_explicit_authentication_rejection_resumes_once_after_login(tmp_path, monkeypatch, model, source):
    states = []
    response = iter([
        {"authenticationRequired": True, "error": "请登录"},
        {"images": [{"data": base64.b64encode(png("blue")).decode(), "mimeType": "image/png"}]},
    ])

    class Cdp:
        def call(self, method, params, **kwargs):
            return {"result": {"value": next(response)}}

    @contextmanager
    def connect(app_dir, model, timeout, force_login=False):
        states.append(force_login)
        yield Cdp(), "frame", 1

    monkeypatch.setattr(canvas, "_connection", connect)
    results = canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, "改为蓝色", [source]))
    assert states == [False, True]
    assert results[0]["bytes"] == png("blue")


def test_invalid_source_rejected_before_browser_access(tmp_path, monkeypatch, model):
    calls = fake_connection(monkeypatch)
    with pytest.raises(ValueError, match="本地源图片"):
        canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(
            tmp_path, model, "改为蓝色", [{"id": "missing", "path": "/missing.png"}],
        ))
    assert calls == []


def test_combined_references_are_sent_in_one_canvas_request(tmp_path, monkeypatch, model, source):
    calls = fake_connection(monkeypatch, result={"images": [
        {"data": base64.b64encode(png("blue")).decode(), "mimeType": "image/png"},
    ]})
    result = canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(
        tmp_path, model, "参考全部素材，重新构图生成一张主图", [source, {**source, "id": "second"}],
        combine_references=True,
    ))
    assert len(result) == 1 and len(calls) == 1
    assert calls[0][1]["expression"].count('"inlineData"') == 2


def test_returned_image_must_be_decodable_and_match_mime():
    encoded = base64.b64encode(png("blue")).decode()
    with pytest.raises(RuntimeError, match="格式"):
        canvas._decode_results({"images": [{"data": encoded, "mimeType": "image/jpeg"}]}, "s", "edit")
    with pytest.raises(Exception):
        canvas._decode_results({"images": [{"data": "not an image", "mimeType": "image/png"}]}, "s", "edit")


@pytest.mark.parametrize("url", [
    "http://gemini.google.com/share/test", "https://gemini.google.com.evil.test/share/test",
    "https://other.test/share/test", "https://gemini.google.com/app",
])
def test_only_explicit_gemini_canvas_page_is_accepted(model, url):
    with pytest.raises(ValueError, match="Canvas"):
        canvas._canvas_url({**model, "browser_url": url})


def test_only_related_default_canvas_world_is_selected():
    evaluated = []

    class Cdp:
        def call(self, method, params=None, *, on_event=None, session_id="", **kwargs):
            if method == "Target.setAutoAttach":
                on_event({"method": "Target.attachedToTarget", "params": {
                    "sessionId": "canvas-session", "targetInfo": {"type": "iframe"},
                }})
            elif method == "Runtime.enable":
                for identifier, origin, default in [
                    (1, "https://untrusted.test", True),
                    (2, "https://a.scf.usercontent.goog", False),
                    (3, "https://a.scf.usercontent.goog", True),
                ]:
                    on_event({"method": "Runtime.executionContextCreated", "sessionId": session_id,
                              "params": {"context": {"id": identifier, "origin": origin,
                                                      "auxData": {"isDefault": default}}}})
            elif method == "Runtime.evaluate":
                evaluated.append((session_id, params["contextId"]))
                return {"result": {"value": session_id == "canvas-session"}}
            return {}

    assert canvas._canvas_context(Cdp()) == ("canvas-session", 3)
    assert evaluated == [("", 3), ("canvas-session", 3)]


def test_browser_image_capability_is_explicit_and_does_not_include_generation(model):
    provider = canvas.GeminiCanvasProvider()
    assert provider.supports(model, "image_edit")
    assert not provider.supports(model, "image_generate")
    assert not provider.supports({**model, "browser_provider": "claude"}, "image_edit")
    assert not provider.supports({**model, "connection_type": "api"}, "image_edit")


def test_display_names_do_not_select_the_browser_adapter(model):
    provider = canvas.GeminiCanvasProvider()
    assert provider.supports({**model, "name": "自己的图片助手", "provider": "自定义显示标签"}, "image_edit")
    assert not provider.supports({**model, "name": "Gemini Canvas", "provider": "Gemini Canvas",
                                  "browser_provider": "gimini"}, "image_edit")


def test_cold_start_waits_for_canvas_frame_without_sending_a_model_request(monkeypatch):
    methods = []
    monkeypatch.setattr(canvas.time, "sleep", lambda _: None)

    class Cdp:
        def call(self, method, params=None, *, on_event=None, session_id="", **kwargs):
            methods.append(method)
            if method == "Page.getFrameTree":
                on_event({"method": "Target.attachedToTarget", "params": {
                    "sessionId": "late-frame", "targetInfo": {"type": "iframe"},
                }})
            elif method == "Runtime.enable" and session_id:
                on_event({"method": "Runtime.executionContextCreated", "sessionId": session_id,
                          "params": {"context": {"id": 9, "origin": "https://a.scf.usercontent.goog",
                                                  "auxData": {"isDefault": True}}}})
            elif method == "Runtime.evaluate":
                assert "generateContent" not in params["expression"]
                return {"result": {"value": True}}
            return {}

    assert canvas._canvas_context(Cdp()) == ("late-frame", 9)
    assert methods.count("Page.getFrameTree") == 1


def test_absent_canvas_frame_reports_missing_preview():
    class Cdp:
        def call(self, *args, **kwargs):
            return {}

    with pytest.raises(RuntimeError, match="未检测到 Canvas 应用预览"):
        canvas._canvas_context(Cdp(), timeout=0)


def test_shared_app_confirmation_waits_for_dialog_and_clicks_once_before_preview(monkeypatch):
    monkeypatch.setattr(canvas.time, "sleep", lambda _: None)
    confirmations = []
    polls = []
    expected = "https://gemini.google.com/share/test"

    class Cdp:
        def call(self, method, params=None, *, on_event=None, **kwargs):
            if method == "Runtime.evaluate":
                if canvas._SHARED_APP_CONTINUE_SCRIPT in params["expression"]:
                    assert params["expression"].endswith(f"({json.dumps(expected)})")
                    assert "contextId" not in params
                    assert kwargs.get("session_id", "") == ""
                    confirmations.append(params["expression"])
                    return {"result": {"value": len(confirmations) == 2}}
                assert "generateContent" not in params["expression"]
                return {"result": {"value": True}}
            if method == "Page.getFrameTree":
                polls.append(method)
                if len(polls) == 3:
                    on_event({"method": "Runtime.executionContextCreated", "params": {"context": {
                        "id": 9, "origin": "https://a.scf.usercontent.goog", "auxData": {"isDefault": True}}}})
            return {}

    assert canvas._canvas_context(Cdp(), expected_url=expected) == ("", 9)
    assert len(confirmations) == 2
    assert len(polls) == 3


def test_cdp_frame_events_and_timeout_stay_scoped_to_one_call():
    from desktop_runtime.browser_debug_service import CdpWebSocket
    sent, events = [], []

    class Socket:
        value = 10

        def gettimeout(self):
            return self.value

        def settimeout(self, value):
            self.value = value

    connection = object.__new__(CdpWebSocket)
    connection.sock = Socket()
    connection.next_id = 1
    connection._send_frame = lambda raw: sent.append(json.loads(raw))
    frames = iter([{"method": "Runtime.executionContextCreated", "params": {}},
                   {"id": 1, "result": {"ready": True}}])
    connection._recv_frame = lambda: next(frames)
    assert connection.call("Runtime.enable", session_id="canvas", on_event=events.append,
                           timeout=180) == {"ready": True}
    assert sent[0]["sessionId"] == "canvas"
    assert events[0]["method"] == "Runtime.executionContextCreated"
    assert connection.sock.gettimeout() == 10


@pytest.mark.parametrize("ready_after_show", [False, True])
def test_preview_recovery_on_original_page(monkeypatch, ready_after_show):
    from unittest.mock import Mock
    page = Mock()
    clock = [0.]
    monkeypatch.setattr(canvas.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(canvas.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))

    def call(method, params=None, *, on_event=None, **kwargs):
        if method == "Runtime.enable":
            on_event({"method": "Runtime.executionContextCreated", "params": {"context": {
                "id": 3, "origin": "https://a.scf.usercontent.goog", "auxData": {"isDefault": True}}}})
        if method == "Runtime.evaluate":
            assert "generateContent" not in params["expression"]
            return {"result": {"value": ready_after_show and page.show.called}}
        return {}

    page.call.side_effect = call
    if ready_after_show:
        assert canvas._canvas_context(page, timeout=10) == ("", 3)
        page.hide.assert_called_once()
    else:
        with pytest.raises(canvas.CanvasPreparationError, match="已检测到 Canvas 预览，但翻译连接未就绪"):
            canvas._canvas_context(page, timeout=10)
    page.show.assert_called_once_with("CANVAS_CONNECTION")


def test_preflight_disconnect_only_loses_the_check_receipt(monkeypatch):
    from erp_web.services import gemini_canvas_runtime as remote
    from erp_web.services.desktop_connection_service import DesktopUnavailable

    def disconnect(operation, params, timeout):
        assert operation == "canvas.check" and "images" not in params
        raise DesktopUnavailable("桌面连接中断", outcome_unknown=True)

    monkeypatch.setattr(remote, "call_desktop", disconnect)
    with pytest.raises(DesktopUnavailable, match="连接检查结果") as failure:
        remote.check_canvas_connection(".", {})
    assert not failure.value.outcome_unknown


def test_opening_missing_preview_resumes_in_same_window(monkeypatch):
    from unittest.mock import Mock
    page = Mock()
    monkeypatch.setattr(canvas, "PREVIEW_REVEAL_SECONDS", 0)
    monkeypatch.setattr(canvas.time, "sleep", lambda _: None)

    def call(method, params=None, *, on_event=None, **kwargs):
        if method == "Page.getFrameTree":
            page.show.assert_called_once_with("CANVAS_PREVIEW")
            on_event({"method": "Runtime.executionContextCreated", "params": {"context": {
                "id": 9, "origin": "https://a.scf.usercontent.goog", "auxData": {"isDefault": True}}}})
        if method == "Runtime.evaluate":
            assert "generateContent" not in params["expression"]
            return {"result": {"value": True}}
        return {}

    page.call.side_effect = call
    assert canvas._canvas_context(page) == ("", 9)
    page.hide.assert_called_once()


def test_thought_images_are_filtered_and_multiple_images_converge_to_final_image():
    draft_img = png("red")
    final_img = png("blue")
    thought_img = png("green")
    assert "p.thought !== true" in canvas._REQUEST_SCRIPT
    decoded = canvas._decode_results({
        "images": [
            {"data": base64.b64encode(thought_img).decode(), "mimeType": "image/png", "thought": True},
            {"data": base64.b64encode(draft_img).decode(), "mimeType": "image/png"},
            {"data": base64.b64encode(final_img).decode(), "mimeType": "image/png"},
        ]
    }, "src", "edit", count=1)
    assert len(decoded) == 1
    assert decoded[0]["bytes"] == final_img


def test_non_json_401_and_abort_timeout_and_english_consent_are_covered(tmp_path, monkeypatch, model, source):
    assert "response.status === 401" in canvas._REQUEST_SCRIPT
    assert "AbortSignal.timeout" in canvas._REQUEST_SCRIPT
    assert "created by another user" in canvas._SHARED_APP_CONTINUE_SCRIPT
    assert "continue" in canvas._SHARED_APP_CONTINUE_SCRIPT

    calls = fake_connection(monkeypatch, error=RuntimeError("websocket closed by peer"))
    with pytest.raises(RuntimeError, match=r"结果未知.*websocket closed by peer"):
        canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, "改为蓝色", [source]))
    assert len(calls) == 1


def test_multi_account_rotation_and_rate_limit_failover(tmp_path, monkeypatch, model, source):
    blue = png("blue")
    monkeypatch.setattr(canvas, "is_browser_bound", lambda: True)
    monkeypatch.setattr(canvas, "list_canvas_accounts", lambda: [
        {"id": "acct_1", "profile": "acct_1", "email": "one@gmail.com", "enabled": True, "status": "signed_in"},
        {"id": "acct_2", "profile": "acct_2", "email": "two@gmail.com", "enabled": True, "status": "signed_in"},
    ])
    monkeypatch.setattr(canvas, "_sync_account_status", lambda *a, **kw: None)
    monkeypatch.setattr(canvas, "_cooldown_until", {})
    monkeypatch.setattr(canvas, "_rr_cursor", 0)

    seen_profiles = []
    responses = iter([
        {"rateLimited": True, "status": 429, "error": "HTTP 429 quota"},
        {"images": [{"data": base64.b64encode(blue).decode(), "mimeType": "image/png"}]},
    ])

    class Cdp:
        def call(self, method, params, **kwargs):
            return {"result": {"value": next(responses)}}

    @contextmanager
    def connect(app_dir, target_model, timeout, force_login=False):
        seen_profiles.append(target_model.get("browser_profile"))
        yield Cdp(), "frame", 1

    monkeypatch.setattr(canvas, "_connection", connect)
    results = canvas.GeminiCanvasProvider().edit_images(AiImageEditRequest(tmp_path, model, "改为蓝色", [source]))
    assert seen_profiles == ["acct_1", "acct_2"]
    assert results[0]["bytes"] == blue
    assert "acct_1" in canvas._cooldown_until

    checked = canvas.check_canvas_connection(tmp_path, model)
    assert checked["ready_accounts"] == 1
    canvas._cooldown_until.clear()
    checked_both = canvas.check_canvas_connection(tmp_path, model)
    assert checked_both["ready_accounts"] == 2


def test_http_keepalive_json_streams_whitespace_and_preserves_contextvars():
    import contextvars
    import time
    from erp_web.http_handler import Handler

    marker = contextvars.ContextVar("canvas_login_marker", default="unset")
    written = BytesIO()
    sent: list[tuple[int, list[tuple[str, str]]]] = []

    class FakeHandler(Handler):
        def __init__(self):
            self.path = "/api/ai-config/canvas/account-login"
            self.wfile = written
            self._current_headers: list[tuple[str, str]] = []
            self._status = 0

        def send_response(self, code, message=None):
            self._status = code

        def send_header(self, keyword, value):
            self._current_headers.append((keyword, value))

        def end_headers(self):
            sent.append((self._status, list(self._current_headers)))

    # 1. Slow path: emits leading whitespace heartbeats while preserving contextvars, then valid JSON.
    token = marker.set("desktop-conn-1")
    try:
        handler = FakeHandler()

        def slow_login():
            time.sleep(0.06)
            return {"ok": True, "conn": marker.get()}, 200

        handler.send_json_with_keepalive(slow_login, keepalive_interval=0.02)
    finally:
        marker.reset(token)

    raw = written.getvalue()
    assert raw.startswith(b" \n")
    assert json.loads(raw.decode("utf-8")) == {"ok": True, "conn": "desktop-conn-1"}
    assert sent[0][0] == 200
    assert ("Content-Type", "application/json; charset=utf-8") in sent[0][1]
    assert not any(k.lower() == "content-length" for k, _ in sent[0][1])

    # 2. Fast path: preserves original non-200 status code and Content-Length without heartbeats.
    fast_written = BytesIO()
    sent.clear()
    fast_handler = FakeHandler()
    fast_handler.wfile = fast_written
    fast_handler.send_json_with_keepalive(
        lambda: ({"ok": False, "error": "桌面端离线"}, 409),
        keepalive_interval=5.0,
    )
    assert sent[0][0] == 409
    assert any(k.lower() == "content-length" for k, _ in sent[0][1])
    assert json.loads(fast_written.getvalue().decode("utf-8")) == {"ok": False, "error": "桌面端离线"}
