"""Canvas 在同一个内置页面等待人工验证，已登录时保持隐藏。"""
from threading import Event
from unittest.mock import Mock

import pytest

from desktop_runtime import gemini_browser_session as session
from desktop_runtime.operation import bind_operation

MODEL = {"browser_provider": "gemini", "browser_profile": "canvas",
         "browser_url": "https://gemini.google.com/share/owned"}


@pytest.fixture
def bridge(monkeypatch):
    actions = []
    def request(action, **params):
        actions.append((action, params))
        return "same-page" if action == "open" else None
    from desktop_runtime import embedded_browser
    monkeypatch.setattr(embedded_browser, "request", request)
    monkeypatch.setattr(session, "request", request)
    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    monkeypatch.setattr(session.browser_ai_runtime, "_evaluate", lambda *_: MODEL["browser_url"])
    return actions


@pytest.mark.parametrize("mode", ["existing_browser", "managed_profile"])
def test_valid_login_stays_hidden_in_same_partition(monkeypatch, bridge, tmp_path, mode):
    monkeypatch.setattr(session, "_auth_state", lambda _: "signed_in")
    with session.connect_gemini_page(tmp_path, {**MODEL, "browser_mode": mode}) as page:
        assert page.id == "same-page"
    assert [name for name, _ in bridge] == ["open", "release_canvas"]
    assert bridge[0][1]["profile"] == "canvas"


def test_login_returns_to_original_canvas_in_same_page(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(session, "_auth_state", Mock(side_effect=["login_required", "login_required", "signed_in", "signed_in"]))
    monkeypatch.setattr(session.browser_ai_runtime, "_evaluate", lambda *_: "https://gemini.google.com/app")
    with session.connect_gemini_page(tmp_path, MODEL):
        assert [name for name, _ in bridge] == ["open", "show", "recover_login", "navigate", "hide"]
    assert all(data["page"] == "same-page" for _, data in bridge[1:])
    assert bridge[3][1]["url"] == MODEL["browser_url"]


def test_login_timeout_never_submits_or_reopens(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(session, "_auth_state", lambda _: "login_required")
    monkeypatch.setattr(session, "LOGIN_WAIT_SECONDS", 0)
    with pytest.raises(session.CanvasPreparationTimeout, match="尚未发送") as failure:
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("登录超时仍进入了任务")
    assert failure.value.outcome_unknown is False
    assert failure.value.code == "CANVAS_REQUEST_NOT_SENT"
    assert [name for name, _ in bridge] == ["open", "show"]


def test_cancel_during_login_never_restarts(monkeypatch, bridge, tmp_path):
    token = Event()
    def state(_):
        if len(bridge) > 1:
            token.set()
        return "login_required"
    monkeypatch.setattr(session, "_auth_state", state)
    with bind_operation(token, 30), pytest.raises(RuntimeError, match="取消"):
        with session.connect_gemini_page(tmp_path, MODEL):
            pytest.fail("取消后仍进入了任务")
    assert [name for name, _ in bridge] == ["open", "show"]


@pytest.mark.parametrize("probe", [False, True])
def test_capability_check_reuses_existing_login(monkeypatch, bridge, tmp_path, probe):
    from erp_web.services import ai_gateway_browser_provider as provider
    from desktop_runtime import gemini_canvas_runtime as canvas
    def check(app_dir, model, timeout, **kwargs):
        canvas.check_canvas_connection(app_dir, model, timeout, **kwargs)
        return {"profile_dir": "", "port": 0}
    monkeypatch.setattr(provider, "check_canvas_connection", check)
    monkeypatch.setattr(session, "_auth_state", lambda _: "signed_in")
    def canvas_context(_, *, expected_url):
        assert expected_url == "https://gemini.google.com/share/owned"
        return "", 1
    monkeypatch.setattr(canvas, "_canvas_context", canvas_context)
    monkeypatch.setattr(canvas, "_evaluate", lambda *_: MODEL["browser_url"])
    run_probe = Mock(return_value=provider.probe_runtime.empty_capability_probe_report())
    monkeypatch.setattr(provider, "probe_browser_model_capabilities", run_probe)
    model = {**MODEL, "id": "new_canvas", "connection_type": "browser", "capabilities": []}
    provider.BrowserAiProvider().test_model(tmp_path, model, {"probe_capabilities": probe})
    assert [name for name, _ in bridge] == ["open", "release_canvas"]
    assert run_probe.called == probe


def test_auth_state_extracts_google_email_and_non_interactive_check_raises(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(session.browser_ai_runtime, "_evaluate", lambda *_args, **_kw: {
        "signedIn": True, "loginRequired": False, "email": "seller@gmail.com", "name": "Seller",
    })
    with session.connect_gemini_page(tmp_path, MODEL, allow_interactive=False) as page:
        assert page.account_email == "seller@gmail.com"
        assert page.account_name == "Seller"
    assert [name for name, _ in bridge] == ["open", "release_canvas"]

    bridge.clear()
    monkeypatch.setattr(session.browser_ai_runtime, "_evaluate", lambda *_args, **_kw: {
        "signedIn": False, "loginRequired": True, "email": "", "name": "",
    })
    with pytest.raises(session.CanvasLoginRequired):
        with session.connect_gemini_page(tmp_path, MODEL, allow_interactive=False):
            pytest.fail("非交互检查不应进入已登录分支")
    assert [name for name, _ in bridge] == ["open"]


def test_downstream_model_error_still_releases_healthy_canvas_page(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(session, "_auth_state", lambda _: "signed_in")
    with pytest.raises(RuntimeError, match="429"):
        with session.connect_gemini_page(tmp_path, MODEL) as page:
            assert page.reusable is True
            raise RuntimeError("HTTP 429")
    assert [name for name, _ in bridge] == ["open", "release_canvas"]

    bridge.clear()
    with pytest.raises(RuntimeError, match="timeout"):
        with session.connect_gemini_page(tmp_path, MODEL) as page:
            page.reusable = False
            raise RuntimeError("timeout")
    assert [name for name, _ in bridge] == ["open"]


def test_force_login_navigates_to_service_login_with_continue(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(session, "_auth_state", Mock(side_effect=["signed_in"]))
    with session.connect_gemini_page(tmp_path, MODEL, force_login=True):
        pass
    nav = next(params["url"] for name, params in bridge if name == "navigate")
    assert nav == "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fgemini.google.com%2Fshare%2Fowned"
