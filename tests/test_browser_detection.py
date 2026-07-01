from __future__ import annotations

from pathlib import Path


def test_find_chrome_path_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    from erp_web import runtime as erp_web_app

    fake_browser = tmp_path / "Google Chrome"
    fake_browser.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ERP_CHROME_PATH", str(fake_browser))
    monkeypatch.setattr(erp_web_app.shutil, "which", lambda _command: None)

    assert erp_web_app.find_chrome_path() == str(fake_browser)


def test_find_chrome_path_detects_macos_chrome_candidate(monkeypatch) -> None:
    from erp_web import runtime as erp_web_app

    expected = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.delenv("ERP_CHROME_PATH", raising=False)
    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.delenv("BROWSER_PATH", raising=False)
    monkeypatch.setattr(erp_web_app.sys, "platform", "darwin")
    monkeypatch.setattr(erp_web_app.shutil, "which", lambda _command: None)
    monkeypatch.setattr(erp_web_app.Path, "exists", lambda self: str(self) == expected)

    assert erp_web_app.find_chrome_path() == expected


def test_fetch_browser_session_defaults_to_unified_debug_port(monkeypatch) -> None:
    from erp_web import runtime as erp_web_app

    opened: dict[str, object] = {}

    def fake_open_browser_debug_session(url: str, port: int, profile_name: str) -> None:
        opened["url"] = url
        opened["port"] = port
        opened["profile_name"] = profile_name

    monkeypatch.setattr(erp_web_app, "BROWSER_DEBUG_PORT", 9222)
    monkeypatch.setenv("ERP_1688_CDP_PORT", "9224")
    monkeypatch.setattr(erp_web_app, "open_browser_debug_session", fake_open_browser_debug_session)
    monkeypatch.setattr(erp_web_app, "cdp_target_for_url", lambda port, url: {"webSocketDebuggerUrl": "ws://fake"})

    class FakeCdp:
        def __init__(self, _url: str) -> None:
            pass

        def call(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
            if method == "Runtime.evaluate":
                expression = (params or {}).get("expression", "")
                if expression == "document.readyState":
                    return {"result": {"value": "complete"}}
                if expression == "document.documentElement.outerHTML":
                    return {"result": {"value": "<html><title>ok</title><body>ok</body></html>"}}
                if expression == "document.body ? document.body.innerText : ''":
                    return {"result": {"value": "ok"}}
                if expression == "document.title || ''":
                    return {"result": {"value": "ok"}}
                if expression == "location.href || ''":
                    return {"result": {"value": "https://detail.1688.com/offer/1.html"}}
                if "document.images" in expression:
                    return {"result": {"value": []}}
                return {"result": {"value": ""}}
            if method == "Page.captureScreenshot":
                return {"data": ""}
            return {}

        def close(self) -> None:
            pass

    monkeypatch.setattr(erp_web_app, "CdpWebSocket", FakeCdp)
    monkeypatch.setattr(erp_web_app, "save_collect_snapshot_artifacts", lambda *args, **kwargs: {"html_snapshot_path": "", "screenshot_path": ""})

    snapshot = erp_web_app.fetch_page_snapshot_with_browser_session("https://detail.1688.com/offer/1.html")

    assert opened["port"] == 9222
    assert snapshot


def test_open_1688_browser_uses_unified_debug_port(monkeypatch) -> None:
    from erp_web import runtime as erp_web_app

    opened: dict[str, object] = {}

    def fake_open_browser_debug_session(url: str, port: int, profile_name: str) -> None:
        opened["url"] = url
        opened["port"] = port
        opened["profile_name"] = profile_name

    monkeypatch.setattr(erp_web_app, "BROWSER_DEBUG_PORT", 9222)
    monkeypatch.setenv("ERP_1688_CDP_PORT", "9224")
    monkeypatch.setattr(erp_web_app, "open_browser_debug_session", fake_open_browser_debug_session)

    erp_web_app.open_browser_debug_session("https://www.1688.com/", erp_web_app.BROWSER_DEBUG_PORT, "1688")

    assert opened["port"] == 9222
