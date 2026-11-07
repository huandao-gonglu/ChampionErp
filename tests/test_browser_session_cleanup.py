"""专用浏览器的任务结束、人工接管与持久化资料边界。"""
import json
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from desktop_runtime import browser_debug_service as browser, collection, dispatcher
from desktop_runtime.config import LocalConfig
from desktop_runtime.operation import bind_operation, check_cancellation

PORT = 19222
ENDPOINT = "ws://127.0.0.1:19222/devtools/browser/owned"


@pytest.fixture
def owned(monkeypatch, tmp_path):
    profile = tmp_path / "browser_profile" / "1688"
    profile.mkdir(parents=True)
    (profile / browser._SESSION_FILE).write_text(json.dumps({"websocket": ENDPOINT}))
    monkeypatch.setattr(browser, "_owned_sessions", {})
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": ENDPOINT})
    browser.ensure_browser_debug_session(PORT, profile)
    return profile


def test_cancelled_task_still_closes_then_clears_only_regenerable_caches(owned, monkeypatch):
    cache = owned / "Default/Cache"
    cache.mkdir(parents=True)
    (cache / "data").write_text("cache")
    keep = ["Default/Cookies", "Default/Network/Cookies", "Default/Local Storage/db",
            "Default/IndexedDB/db", "Default/Extensions/addon", "Local State"]
    for name in keep:
        path = owned / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserve")
    link = owned / "Default/Code Cache"
    link.symlink_to(owned / "Default/Local Storage", target_is_directory=True)
    def close(*_):
        check_cancellation()
        assert cache.is_dir()
    monkeypatch.setattr(browser, "_close_managed_browser", close)
    cancelled = Event()
    cancelled.set()
    with bind_operation(cancelled, 0):
        browser.finish_browser_session(PORT, owned)
    assert not cache.exists()
    assert all((owned / name).read_bytes() == b"preserve" for name in keep)
    assert link.is_symlink()
    assert not (owned / browser._SESSION_FILE).exists()
    assert not browser._owned_sessions


def test_replaced_instance_or_failed_close_never_clears_live_cache(owned, monkeypatch):
    cache = owned / "Default/Cache"
    cache.mkdir(parents=True)
    close = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(browser, "_close_managed_browser", close)
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": "ws://other"})
    browser.close_runtime_browsers()
    close.assert_not_called()
    monkeypatch.setattr(browser, "_browser_version", lambda _: {"webSocketDebuggerUrl": ENDPOINT})
    browser.close_runtime_browsers()
    close.assert_called_once()
    assert cache.is_dir()


def test_batch_keeps_other_tabs_then_exits_after_last_product(owned, monkeypatch):
    pages = [{"id": "first", "type": "page", "url": "https://detail.1688.com/offer/1.html"},
             {"id": "waiting", "type": "page", "url": "https://login.1688.com"},
             {"id": "home", "type": "page", "url": "https://www.1688.com/"},
             {"id": "blank", "type": "page", "url": "chrome://newtab/"}]
    close = Mock()
    control = Mock()
    monkeypatch.setattr(browser, "_close_managed_browser", close)
    monkeypatch.setattr(browser, "CdpWebSocket", lambda _: control)
    monkeypatch.setattr(browser, "browser_debug_json", lambda *args: pages)
    browser.finish_browser_session(PORT, owned, completed_page=("first", pages[0]["url"]),
                                   landing_page=("home", "https://www.1688.com/"))
    close.assert_not_called()
    control.call.assert_called_once_with("Target.closeTarget", {"targetId": "first"}, timeout=5)
    pages.pop(0)
    browser.finish_browser_session(PORT, owned, completed_page=("waiting", pages[0]["url"]),
                                   landing_page=("home", "https://www.1688.com/"))
    close.assert_called_once()
    assert not browser._owned_sessions


@pytest.mark.parametrize("verification, save_only, should_close", [
    (True, False, False), (False, True, False), (False, False, True),
])
def test_collection_keeps_intermediate_page_and_reports_final_status(
    tmp_path, monkeypatch, verification, save_only, should_close,
):
    config = LocalConfig(tmp_path)
    closed = Mock()
    monkeypatch.setattr(collection, "_select_target", lambda *_: {"id": "original"})
    monkeypatch.setattr(collection, "_read_target", lambda *_: ({"html": "页面"}, [], verification))
    monkeypatch.setattr(collection, "finish_browser_session", closed)
    monkeypatch.setattr(collection, "browser_status", lambda _: {"connected": not closed.called})
    result = collection.read_collection(config, browser_tab_id="original", save_only=save_only)
    assert closed.called is should_close
    assert result["browserStatus"]["connected"] is not should_close


@pytest.mark.parametrize("mode, fail, should_close", [
    ("managed_profile", False, True), ("managed_profile", True, True), ("existing_browser", False, False),
])
def test_canvas_dispatch_disposes_only_managed_sessions(tmp_path, monkeypatch, mode, fail, should_close):
    config = LocalConfig(tmp_path)
    config.update({"canvas": {"browser_mode": mode, "browser_profile": "canvas"}})
    close = Mock()
    monkeypatch.setattr(dispatcher, "finish_browser_session", close)
    monkeypatch.setattr(dispatcher, "check_canvas_connection", Mock(side_effect=TimeoutError() if fail else None))
    model = {"id": "canvas", "browser_provider": "gemini"}
    if fail:
        with pytest.raises(TimeoutError):
            dispatcher.dispatch(config, "canvas.check", {"model": model})
    else:
        assert dispatcher.dispatch(config, "canvas.check", {"model": model})["ok"]
    assert close.called is should_close


def test_user_navigated_away_from_completed_tab(owned, monkeypatch):
    pages = [{"id": "original", "type": "page", "url": "https://detail.1688.com/offer/2.html"}]
    monkeypatch.setattr(browser, "browser_debug_json", lambda *_: pages)
    close_tab, close_process = Mock(), Mock()
    monkeypatch.setattr(browser, "CdpWebSocket", close_tab)
    monkeypatch.setattr(browser, "_close_managed_browser", close_process)
    browser.finish_browser_session(PORT, owned, completed_page=("original", "https://detail.1688.com/offer/1.html"))
    close_tab.assert_not_called()
    close_process.assert_not_called()


def test_next_operation_cannot_enter_during_cleanup():
    waiting, entered = Event(), Event()
    def next_task():
        waiting.set()
        with browser.browser_operation(PORT):
            entered.set()
    with browser.browser_operation(PORT):
        thread = Thread(target=next_task)
        thread.start()
        assert waiting.wait(1)
        assert not entered.wait(0.05)
    thread.join(2)
    assert entered.is_set()


def test_cancelled_start_only_terminates_the_new_child(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "browser_debug_connected", Mock(side_effect=[False, RuntimeError("已取消")]))
    monkeypatch.setattr(browser, "find_chrome_path", lambda: "chrome")
    child = Mock()
    child.poll.return_value = None
    monkeypatch.setattr(browser.subprocess, "Popen", Mock(return_value=child))
    with pytest.raises(RuntimeError, match="已取消"):
        browser.ensure_browser_debug_session(PORT, tmp_path, headless=True)
    child.terminate.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=3)
    assert not (tmp_path / browser._SESSION_FILE).exists()
