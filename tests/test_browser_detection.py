from __future__ import annotations

from pathlib import Path

from desktop_runtime import browser_debug_service


def test_find_chrome_path_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    fake_browser = tmp_path / "Google Chrome"
    fake_browser.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ERP_CHROME_PATH", str(fake_browser))
    monkeypatch.setattr(browser_debug_service.shutil, "which", lambda _command: None)

    assert browser_debug_service.find_chrome_path() == str(fake_browser)


def test_find_chrome_path_detects_macos_chrome_candidate(monkeypatch) -> None:
    expected = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.delenv("ERP_CHROME_PATH", raising=False)
    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.delenv("BROWSER_PATH", raising=False)
    monkeypatch.setattr(browser_debug_service.sys, "platform", "darwin")
    monkeypatch.setattr(browser_debug_service.shutil, "which", lambda _command: None)
    monkeypatch.setattr(browser_debug_service.Path, "exists", lambda self: str(self) == expected)

    assert browser_debug_service.find_chrome_path() == expected
