"""浏览器采集只使用明确的目标，快照和探测失败不修改商品。"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import Mock

import pytest

from erp_web.context import get_context
from erp_web.runtime_units import source_collect_browser as browser
from erp_web.runtime_units import source_collect_workflows as workflows


@pytest.mark.parametrize("selector", ["tab_url", "product_url"])
def test_explicit_browser_target_does_not_fall_back(selector: str) -> None:
    tabs = [
        {"type": "page", "url": "https://detail.1688.com/offer/12.html"},
        {"type": "page", "url": "https://amazon.com/dp/ABC123"},
    ]
    assert browser.choose_browser_tab(tabs, **{selector: tabs[1]["url"]}) == tabs[1]
    assert browser.choose_browser_tab(tabs, **{selector: "https://detail.1688.com/offer/1"}) is None
    assert browser.choose_browser_tab([{"type": "page", "url": "chrome://settings"}]) is None


def test_new_browser_target_uses_put_without_reusing_another_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "http_json", lambda _: [{"type": "page", "url": "https://detail.1688.com/offer/old.html"}])
    opener = Mock(return_value=BytesIO(b'{"id":"new","webSocketDebuggerUrl":"ws://localhost/new"}'))
    monkeypatch.setattr(browser.urllib.request, "urlopen", opener)
    target = browser.cdp_target_for_url(9222, "https://detail.1688.com/offer/new.html")
    assert target["id"] == "new"
    request = opener.call_args.args[0]
    assert request.get_method() == "PUT"
    assert "new.html" in request.full_url


@pytest.mark.parametrize("save_only", [False, True])
def test_closed_target_does_not_modify_current_product(monkeypatch: pytest.MonkeyPatch, save_only: bool) -> None:
    context = get_context()
    before = context.products.save_product({"name": "原商品", "source": {"title": "原商品"}})
    monkeypatch.setattr(workflows, "browser_debug_status", lambda _: {"connected": True})
    monkeypatch.setattr(workflows, "http_json", lambda _: [{"type": "page", "url": "https://detail.1688.com/offer/other.html"}])
    snapshot = Mock()
    monkeypatch.setattr(workflows, "snapshot_from_cdp_target", snapshot)
    result = workflows.collect_from_browser_tab(tab_url="https://detail.1688.com/offer/closed.html", save_only=save_only)
    assert result["ok"] is False
    assert result["diagnostics"]["error_code"] == "NO_PRODUCT_TAB_FOUND"
    snapshot.assert_not_called()
    assert context.products.load_product() == before


def test_save_snapshot_skips_parsing_image_download_and_product_write(monkeypatch: pytest.MonkeyPatch) -> None:
    context = get_context()
    before = context.products.save_product({"name": "原商品", "source": {"title": "原商品"}})
    url = "https://detail.1688.com/offer/new.html"
    monkeypatch.setattr(workflows, "browser_debug_status", lambda _: {"connected": True})
    monkeypatch.setattr(workflows, "http_json", lambda _: [{"type": "page", "url": url}])
    monkeypatch.setattr(workflows, "snapshot_from_cdp_target", lambda *_: {"url": url, "html": "<html>安全验证</html>", "html_snapshot_path": "/tmp/debug.html"})
    normalize_images = Mock(side_effect=AssertionError("保存快照不得下载图片"))
    monkeypatch.setattr(workflows, "normalize_collect_source_images", normalize_images)
    result = workflows.collect_from_browser_tab(tab_url=url, save_only=True)
    assert result["ok"] is True
    assert result["saved_only"] is True
    assert result["diagnostics"]["html_snapshot_path"] == "/tmp/debug.html"
    normalize_images.assert_not_called()
    assert context.products.load_product() == before


def test_reuses_same_1688_offer_after_manual_verification_without_opening_another_tab(monkeypatch):
    target = {'type': 'page', 'url': 'https://detail.1688.com/offer/123.html?spm=verified', 'webSocketDebuggerUrl': 'ws://localhost/verified'}
    monkeypatch.setattr(browser, 'http_json', lambda _: [target])
    opener = Mock(side_effect=AssertionError('已打开的商品不应再建标签'))
    monkeypatch.setattr(browser.urllib.request, 'urlopen', opener)
    assert browser.cdp_target_for_url(9222, 'https://detail.1688.com/offer/123.html') is target
    opener.assert_not_called()


def test_opening_browser_returns_the_original_target_without_a_second_lookup(monkeypatch, tmp_path):
    from types import SimpleNamespace
    target = {'type': 'page', 'url': 'https://detail.1688.com/offer/123.html', 'webSocketDebuggerUrl': 'ws://localhost/original'}
    monkeypatch.setattr(browser, 'get_context', lambda: SimpleNamespace(paths=SimpleNamespace(browser_profile_dir=tmp_path / 'profile')))
    monkeypatch.setattr(browser, 'find_chrome_path', lambda: 'chrome')
    monkeypatch.setattr(browser, 'http_json', lambda _: {})
    lookup = Mock(return_value=target)
    monkeypatch.setattr(browser, 'cdp_target_for_url', lookup)
    assert browser.open_browser_debug_session(target['url'], 9222, '1688') is target
    lookup.assert_called_once_with(9222, target['url'])
