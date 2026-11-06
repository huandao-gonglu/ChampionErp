"""共享浏览器启动、登录资料隔离和 Canvas 页面身份回归。"""

import json
from unittest.mock import Mock

import pytest

from erp_web.context import get_context
from desktop_runtime import browser_ai_runtime as ai
from desktop_runtime import browser_debug_service as browser
from erp_web.services import ai_model_config
from erp_shared.browser_connection_config import normalize_browser_proxy


def test_owned_browser_is_reused_without_launch_or_profile_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: True)
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/owned"})
    launch = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    profile = tmp_path / "existing"
    profile.mkdir()
    session = profile / browser._SESSION_FILE
    session.write_text(json.dumps({"websocket": "ws://localhost/owned"}))
    original = session.read_bytes()
    browser.ensure_browser_debug_session(9335, profile)
    launch.assert_not_called()
    assert session.read_bytes() == original


def test_browser_start_uses_persistent_profile_and_loopback_port(monkeypatch, tmp_path):
    connected = iter([False, False, True])
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: next(connected))
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/browser/test"})
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)
    launch = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    profile = tmp_path / "gemini"
    browser.ensure_browser_debug_session(9335, profile)
    arguments = launch.call_args.args[0]
    assert f"--user-data-dir={profile.resolve()}" in arguments
    assert "--remote-debugging-port=9335" in arguments
    assert "--remote-debugging-address=127.0.0.1" in arguments
    assert profile.is_dir()
    assert "--headless" not in arguments


def test_browser_start_failure_is_reported_without_repeated_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: False)
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/browser/test"})
    launch = Mock(return_value=Mock(poll=Mock(return_value=1)))
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="资料目录"):
        browser.ensure_browser_debug_session(9335, tmp_path / "gemini")
    launch.assert_called_once()


def test_gemini_managed_profile_is_separate_from_collection(monkeypatch, tmp_path):
    model = {"browser_provider": "gemini", "browser_profile": "canvas"}
    ensure = Mock()
    monkeypatch.setattr(ai, "ensure_browser_debug_session", ensure)
    target = {"id": "canvas", "webSocketDebuggerUrl": "ws://localhost/canvas"}
    monkeypatch.setattr(ai, "_target_for_url", lambda *_: target)
    assert ai._ensure_browser_target(tmp_path, model, "https://gemini.google.com/share/test", 30) == target
    profile = ai.browser_ai_profile_dir(tmp_path, model)
    ensure.assert_called_once_with(ai.browser_ai_port(model), profile, 30, proxy_server="", headless=None)
    assert profile == tmp_path / "browser_profile" / "ai" / "gemini" / "canvas"
    from desktop_runtime.config import LocalConfig
    local = LocalConfig(tmp_path)
    assert profile != local.browser_profile
    assert ai.browser_ai_port(model) != local.browser_port


def test_canvas_target_is_exact_and_new_tabs_use_put(monkeypatch):
    url = "https://gemini.google.com/share/wanted"
    wrong = [
        {"url": "about:blank"},
        {"url": "https://gemini.google.com/share/other"},
        {"url": "https://gemini.google.com.evil.test/share/wanted"},
    ]
    monkeypatch.setattr(ai, "_page_targets", lambda _: wrong)
    opener = Mock(return_value={"id": "wanted", "webSocketDebuggerUrl": "ws://localhost/wanted"})
    monkeypatch.setattr(ai, "browser_debug_json", opener)
    assert ai._target_for_url(9335, url)["id"] == "wanted"
    assert opener.call_args.args[0] == 9335
    assert opener.call_args.kwargs["method"] == "PUT"
    original = {"url": url, "webSocketDebuggerUrl": "ws://localhost/original"}
    monkeypatch.setattr(ai, "_page_targets", lambda _: [*wrong, original])
    assert ai._target_for_url(9335, url) is original
    assert opener.call_count == 1


def test_canvas_target_creation_failure_never_uses_unrelated_page(monkeypatch):
    monkeypatch.setattr(ai, "_page_targets", lambda _: [{"url": "https://gemini.google.com/share/other"}])
    monkeypatch.setattr(ai, "browser_debug_json", Mock(side_effect=OSError("浏览器已关闭")))
    with pytest.raises(OSError, match="已关闭"):
        ai._target_for_url(9335, "https://gemini.google.com/share/wanted")


def test_proxy_reaches_browser_launch_without_direct_connection_fallback(monkeypatch, tmp_path):
    connected = iter([False, True])
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: next(connected))
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/browser/test"})
    launch = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    browser.ensure_browser_debug_session(9335, tmp_path / "gemini", proxy_server="socks5://127.0.0.1:7890")
    assert "--proxy-server=socks5://127.0.0.1:7890" in launch.call_args.args[0]
    assert not any("direct://" in argument for argument in launch.call_args.args[0])


@pytest.mark.parametrize("value", [
    "http://user:do-not-expose@proxy.test:8080", "socks5://user@proxy.test:1080",
    "http://proxy.test:70000", "http://proxy.test:0", "ftp://proxy.test:21",
    "http://proxy.test", "http://proxy.test:80/path", "http://proxy.test:80?secret=value",
    "http://proxy.test:80,direct://", "http://proxy.test:80;http=direct://",
])
def test_proxy_rejects_unsupported_or_credential_bearing_addresses(value):
    with pytest.raises(ValueError) as error:
        normalize_browser_proxy(value)
    assert "do-not-expose" not in str(error.value)
    assert "secret=value" not in str(error.value)


def test_proxy_survives_config_save_and_changes_capability_fingerprint():
    model = {"id": "canvas", "connection_type": "browser", "browser_provider": "gemini",
             "browser_proxy": "http://LOCALHOST:7890/", "capabilities": ["image_edit"]}
    normalized = ai_model_config.normalize_ai_model(model)
    assert normalized["browser_proxy"] == "http://localhost:7890"
    fingerprint = ai_model_config.model_configuration_fingerprint(normalized)
    assert fingerprint != ai_model_config.model_configuration_fingerprint({**normalized, "browser_proxy": ""})
    context = get_context()
    config = context.config.load_app_config()
    config["ai_models"] = [normalized]
    context.config.save_app_config(config)
    assert context.config.load_app_config()["ai_models"][0]["browser_proxy"] == "http://localhost:7890"


def test_ai_managed_profile_passes_its_proxy_to_shared_browser_manager(monkeypatch, tmp_path):
    ensure = Mock()
    monkeypatch.setattr(ai, "ensure_browser_debug_session", ensure)
    monkeypatch.setattr(ai, "_target_for_url", lambda *_: {})
    model = {"browser_provider": "gemini", "browser_profile": "canvas", "browser_proxy": "http://localhost:7890"}
    ai._ensure_browser_target(tmp_path, model, "https://gemini.google.com/share/test", 30)
    ensure.assert_called_once_with(ai.browser_ai_port(model), ai.browser_ai_profile_dir(tmp_path, model), 30, proxy_server="http://localhost:7890", headless=None)


def test_hidden_browser_launch_records_its_instance_identity(monkeypatch, tmp_path):
    connected = iter([False, True])
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: next(connected))
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/new"})
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    launch = Mock()
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    browser.ensure_browser_debug_session(9335, tmp_path, headless=True)
    assert "--headless" in launch.call_args.args[0]
    assert "--start-maximized" not in launch.call_args.args[0]
    assert json.loads((tmp_path / browser._SESSION_FILE).read_text()) == {
        "websocket": "ws://localhost/new", "headless": True, "proxy_server": "",
    }


def test_switching_to_hidden_only_closes_the_recorded_managed_instance(monkeypatch, tmp_path):
    version = {"webSocketDebuggerUrl": "ws://localhost/owned"}
    (tmp_path / browser._SESSION_FILE).write_text(json.dumps({
        "websocket": version["webSocketDebuggerUrl"], "headless": False, "proxy_server": "",
    }))
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: True)
    monkeypatch.setattr(browser, "_browser_version", lambda _: version)
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    close = Mock()
    launch = Mock()
    monkeypatch.setattr(browser, "_close_managed_browser", close)
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    browser.ensure_browser_debug_session(9335, tmp_path, headless=True)
    close.assert_called_once_with(9335, tmp_path, version, 15)
    assert "--headless" in launch.call_args.args[0]


def test_unknown_browser_instance_is_not_closed_or_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "browser_debug_connected", lambda _: True)
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://localhost/not-ours"})
    close = Mock()
    launch = Mock()
    monkeypatch.setattr(browser, "_close_managed_browser", close)
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="其他会话"):
        browser.ensure_browser_debug_session(9335, tmp_path, headless=True)
    close.assert_not_called()
    launch.assert_not_called()


@pytest.mark.parametrize("destination", [
    "https://accounts.google.com/v3/signin/identifier",
    "https://gemini.google.com/share/owned",
])
def test_page_preparation_waits_for_initial_load_without_restarting_redirect(monkeypatch, destination):
    values = iter(["about:blank", "complete", destination, "interactive"])
    monkeypatch.setattr(ai, "_evaluate", lambda *args, **kwargs: next(values))
    monkeypatch.setattr(ai.time, "sleep", lambda _: None)
    cdp = Mock()
    ai._prepare_page(cdp, 5)
    assert [call.args[0] for call in cdp.call.call_args_list] == ["Page.enable", "Runtime.enable"]


def test_page_preparation_reports_unloaded_page_without_navigating(monkeypatch):
    monkeypatch.setattr(ai, "_evaluate", lambda cdp, expression, **kwargs:
                        "about:blank" if expression.startswith("location") else "complete")
    cdp = Mock()
    with pytest.raises(TimeoutError, match="页面加载超时"):
        ai._prepare_page(cdp, 0)
    assert [call.args[0] for call in cdp.call.call_args_list] == ["Page.enable", "Runtime.enable"]
