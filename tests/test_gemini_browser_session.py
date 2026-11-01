"""登录等待、窗口切换、取消和图片请求是否真正提交的回归。"""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pydantic_ai import CancellationToken, RunCancelled

from erp_web.services import gemini_browser_session as session
from erp_web.services.ai_run_cancellation import bind_cancellation_token


MODEL = {"browser_provider": "gemini", "browser_mode": "managed_profile",
         "browser_url": "https://gemini.google.com/share/owned"}


def connections(monkeypatch, states):
    calls, actions = [], []
    states = iter(states)

    class Cdp:
        def call(self, method, params=None, **kwargs):
            actions.append((method, params))
            return {}

    @contextmanager
    def connect(*args, **kwargs):
        calls.append(kwargs.get("headless"))
        yield Cdp()

    monkeypatch.setattr(session.browser_ai_runtime, "connect_browser_ai_page", connect)
    monkeypatch.setattr(session.browser_ai_runtime, "_ensure_browser_target", Mock())
    monkeypatch.setattr(session, "_auth_state", lambda _: next(states))
    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    return calls, actions


def test_valid_login_stays_hidden_without_interactive_login(monkeypatch, tmp_path):
    calls, actions = connections(monkeypatch, ["signed_in"])
    with session.connect_gemini_page(tmp_path, MODEL):
        assert calls == [True]
    assert not actions
    session.browser_ai_runtime._ensure_browser_target.assert_not_called()


def test_first_login_waits_then_returns_a_hidden_connection(monkeypatch, tmp_path):
    calls, actions = connections(monkeypatch, ["login_required", "login_required", "signed_in", "signed_in"])
    with session.connect_gemini_page(tmp_path, MODEL):
        assert calls == [True, False, True]
    assert any(method == "Page.bringToFront" for method, _ in actions)
    navigation = next(params for method, params in actions if method == "Page.navigate")
    assert navigation["url"].startswith("https://accounts.google.com/ServiceLogin?")
    session.browser_ai_runtime._ensure_browser_target.assert_called_once_with(
        tmp_path, MODEL, MODEL["browser_url"], 45, headless=True,
    )


def test_login_timeout_hides_window_without_continuing_task(monkeypatch, tmp_path):
    calls, _ = connections(monkeypatch, ["login_required", "login_required"])
    monkeypatch.setattr(session, "LOGIN_WAIT_SECONDS", 0)
    with pytest.raises(TimeoutError, match="尚未发送"):
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("登录未完成不能继续发送图片")
    assert calls == [True, False]
    assert session.browser_ai_runtime._ensure_browser_target.call_args.kwargs == {"headless": True}


def test_cancel_during_login_restores_hidden_mode_and_preserves_cancellation(monkeypatch, tmp_path):
    calls, _ = connections(monkeypatch, ["login_required"])
    token = CancellationToken()
    checks = 0

    def state(_):
        nonlocal checks
        checks += 1
        if checks == 2:
            token.cancel()
        return "login_required"

    monkeypatch.setattr(session, "_auth_state", state)
    with bind_cancellation_token(token), pytest.raises(RunCancelled):
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("取消后不能继续任务")
    assert calls == [True, False]
    assert session.browser_ai_runtime._ensure_browser_target.call_args.kwargs == {"headless": True}


def test_user_managed_browser_is_never_restarted_or_hidden(monkeypatch, tmp_path):
    calls, _ = connections(monkeypatch, ["login_required"])
    with pytest.raises(RuntimeError, match="已连接"):
        with session.connect_gemini_page(tmp_path, {**MODEL, "browser_mode": "existing_browser"}):
            pytest.fail("应先由用户完成登录")
    assert calls == [None]
    session.browser_ai_runtime._ensure_browser_target.assert_not_called()


def test_old_signed_in_document_is_not_accepted_before_login_navigation_commits(monkeypatch):
    states = Mock(return_value="signed_in")
    monkeypatch.setattr(session, "_auth_state", states)
    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    cdp = Mock()
    cdp.call.side_effect = [
        {"frameTree": {"frame": {"loaderId": "old"}}},
        {"frameTree": {"frame": {"loaderId": "new"}}},
    ]
    assert session._wait_auth_state(cdp, 5, wait_for_login=True, previous_loader="old") == "signed_in"
    assert cdp.call.call_count == 2
    states.assert_called_once()
