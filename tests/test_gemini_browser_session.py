"""登录等待、窗口切换、取消和图片请求是否真正提交的回归。"""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from threading import Event
from desktop_runtime.operation import bind_operation

from desktop_runtime import gemini_browser_session as session


MODEL = {"browser_provider": "gemini", "browser_mode": "managed_profile",
         "browser_url": "https://gemini.google.com/share/owned"}


def connections(monkeypatch, states):
    calls, actions = [], []
    models = []
    states = iter(states)

    class Cdp:
        def call(self, method, params=None, **kwargs):
            actions.append((method, params))
            return {}

    @contextmanager
    def connect(*args, **kwargs):
        calls.append(kwargs.get("headless"))
        models.append(args[1])
        yield Cdp()

    monkeypatch.setattr(session.browser_ai_runtime, "connect_browser_ai_page", connect)
    monkeypatch.setattr(session.browser_ai_runtime, "_ensure_browser_target", Mock())
    monkeypatch.setattr(session, "_auth_state", lambda _: next(states))
    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    return calls, actions, models


def test_valid_login_stays_hidden_without_interactive_login(monkeypatch, tmp_path):
    calls, actions, models = connections(monkeypatch, ["signed_in"])
    with session.connect_gemini_page(tmp_path, MODEL):
        assert calls == [True]
    assert not actions
    session.browser_ai_runtime._ensure_browser_target.assert_not_called()


def test_first_login_waits_then_returns_a_hidden_connection(monkeypatch, tmp_path):
    calls, actions, models = connections(monkeypatch, ["login_required", "login_required", "signed_in", "signed_in"])
    with session.connect_gemini_page(tmp_path, MODEL):
        assert calls == [True, False, True]
    assert any(method == "Page.bringToFront" for method, _ in actions)
    assert models[1]["browser_url"].startswith("https://accounts.google.com/ServiceLogin?")
    session.browser_ai_runtime._ensure_browser_target.assert_called_once_with(
        tmp_path, MODEL, MODEL["browser_url"], 45, headless=True,
    )


def test_login_timeout_hides_window_without_continuing_task(monkeypatch, tmp_path):
    calls, _, models = connections(monkeypatch, ["login_required", "login_required"])
    monkeypatch.setattr(session, "LOGIN_WAIT_SECONDS", 0)
    with pytest.raises(TimeoutError, match="尚未发送"):
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("登录未完成不能继续发送图片")
    assert calls == [True, False]
    assert session.browser_ai_runtime._ensure_browser_target.call_args.kwargs == {"headless": True}


def test_cancel_during_login_restores_hidden_mode_and_preserves_cancellation(monkeypatch, tmp_path):
    calls, _, models = connections(monkeypatch, ["login_required"])
    token = Event()
    checks = 0

    def state(_):
        nonlocal checks
        checks += 1
        if checks == 2:
            token.set()
        return "login_required"

    monkeypatch.setattr(session, "_auth_state", state)
    with bind_operation(token, 30), pytest.raises(RuntimeError, match="取消"):
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("取消后不能继续任务")
    assert calls == [True, False]
    assert session.browser_ai_runtime._ensure_browser_target.call_args.kwargs == {"headless": True}


def test_user_managed_browser_is_never_restarted_or_hidden(monkeypatch, tmp_path):
    calls, _, models = connections(monkeypatch, ["login_required"])
    with pytest.raises(RuntimeError, match="已连接"):
        with session.connect_gemini_page(tmp_path, {**MODEL, "browser_mode": "existing_browser"}):
            pytest.fail("应先由用户完成登录")
    assert calls == [None]
    session.browser_ai_runtime._ensure_browser_target.assert_not_called()


@pytest.mark.parametrize("states, expected_windows", [
    (["signed_in"], [True]),
    (["login_required", "signed_in", "signed_in"], [True, False, True]),
])
def test_new_canvas_without_capabilities_checks_login_without_model_request(
    monkeypatch, tmp_path, states, expected_windows,
):
    from erp_web.services import ai_gateway_browser_provider as provider
    from desktop_runtime import gemini_canvas_runtime as canvas
    from dataclasses import asdict
    def check(app_dir, model, timeout, **kwargs):
        canvas.check_canvas_connection(app_dir, model, timeout, **kwargs)
        return {"profile_dir": str(tmp_path), "port": 19335}
    monkeypatch.setattr(provider, "check_canvas_connection", check)

    calls, _, models = connections(monkeypatch, states)
    monkeypatch.setattr(canvas, "_canvas_context", lambda _: ("", 1))
    monkeypatch.setattr(canvas, "_evaluate", lambda *args, **kwargs: MODEL["browser_url"])
    probe = Mock(side_effect=AssertionError("登录检查不得发送模型请求"))
    monkeypatch.setattr(provider, "probe_browser_model_capabilities", probe)
    model = {**MODEL, "id": "new_canvas", "connection_type": "browser", "capabilities": []}
    result = provider.BrowserAiProvider().test_model(tmp_path, model, {"probe_capabilities": False})
    assert result["ok"]
    assert calls == expected_windows
    probe.assert_not_called()


def test_first_capability_test_opens_login_before_hidden_canvas(monkeypatch, tmp_path):
    from erp_web.services import ai_gateway_browser_provider as provider
    from desktop_runtime import gemini_canvas_runtime as canvas
    from dataclasses import asdict
    def check(app_dir, model, timeout, **kwargs):
        canvas.check_canvas_connection(app_dir, model, timeout, **kwargs)
        return {"profile_dir": str(tmp_path), "port": 19335}
    monkeypatch.setattr(provider, "check_canvas_connection", check)

    calls, _, models = connections(monkeypatch, ["signed_in", "signed_in"])
    monkeypatch.setattr(canvas, "_canvas_context", lambda _: ("", 1))
    monkeypatch.setattr(canvas, "_evaluate", lambda *args, **kwargs: MODEL["browser_url"])
    probe = Mock(return_value=provider.probe_runtime.empty_capability_probe_report())
    monkeypatch.setattr(provider, "probe_browser_model_capabilities", probe)
    model = {**MODEL, "id": "new_canvas", "connection_type": "browser", "capabilities": []}
    provider.BrowserAiProvider().test_model(tmp_path, model, {
        "probe_capabilities": True, "probe_only_capability": "image_edit",
    })
    assert calls == [False, True]
    assert models[0]["browser_url"].startswith("https://accounts.google.com/ServiceLogin?")
    probe.assert_called_once()


@pytest.mark.parametrize("mode, capabilities, expected_windows", [
    ("existing_browser", [], [None]),
    ("managed_profile", ["image_edit"], [True]),
])
def test_existing_browser_and_verified_model_keep_normal_login_check(
    monkeypatch, tmp_path, mode, capabilities, expected_windows,
):
    from erp_web.services import ai_gateway_browser_provider as provider
    from desktop_runtime import gemini_canvas_runtime as canvas
    from dataclasses import asdict
    def check(app_dir, model, timeout, **kwargs):
        canvas.check_canvas_connection(app_dir, model, timeout, **kwargs)
        return {"profile_dir": str(tmp_path), "port": 19335}
    monkeypatch.setattr(provider, "check_canvas_connection", check)

    calls, _, _ = connections(monkeypatch, ["signed_in"])
    monkeypatch.setattr(canvas, "_canvas_context", lambda _: ("", 1))
    monkeypatch.setattr(canvas, "_evaluate", lambda *args, **kwargs: MODEL["browser_url"])
    monkeypatch.setattr(provider, "probe_browser_model_capabilities", Mock(
        return_value=provider.probe_runtime.empty_capability_probe_report(),
    ))
    model = {**MODEL, "id": "canvas", "connection_type": "browser",
             "browser_mode": mode, "capabilities": capabilities}
    provider.BrowserAiProvider().test_model(tmp_path, model, {
        "probe_capabilities": True, "probe_only_capability": "image_edit",
    })
    assert calls == expected_windows
