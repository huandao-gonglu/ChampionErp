"""1688 的页面归属、交互等待和已验证标签的恢复。"""
from unittest.mock import Mock
import pytest

from desktop_runtime import collection, dispatcher, embedded_collection as embedded
from desktop_runtime.config import LocalConfig
from desktop_runtime.operation import LocalOperationError
from erp_shared.source_sites import source_site

PRODUCT = "https://detail.1688.com/offer/123.html"
SECOND = "https://detail.1688.com/offer/456.html"
TAB = "owned-page"


def test_guest_waits_and_resumes_same_offer(tmp_path, monkeypatch):
    tabs = []
    events = []

    def request(action, **data):
        events.append((action, data))
        if action == "list":
            return [dict(tab) for tab in tabs]
        if action == "open":
            tabs.append({"id": TAB, "source_url": PRODUCT, "type": "page",
                         "url": "https://login.taobao.com/?ticket=private", "title": "登录"})
            return TAB
        if action == "close":
            tabs.clear()

    monkeypatch.setattr(embedded, "request", request)
    from desktop_runtime import embedded_browser
    monkeypatch.setattr(embedded_browser, "request", request)
    guest = {"url": "https://login.taobao.com/?ticket=private", "html": "登录页面",
             "text": "", "title": "登录", "verification_reason": "LOGIN"}
    signed_in = {"url": PRODUCT, "html": '<script>{"buyerModel":{"userId":42}}</script>'}
    read = Mock(side_effect=[(guest, [], True), (signed_in, [], False)])
    monkeypatch.setattr(collection, "_read_connected_target", read)
    monkeypatch.setattr(collection, "_inspect_page", Mock(side_effect=[
        {"status": "waiting_verification"}, {"status": "ready"},
    ]))
    config = LocalConfig(tmp_path)
    result = dispatcher.dispatch(config, "collect.read", {"product_url": PRODUCT, "open_url": True})
    assert result["snapshot"] == {"url": "https://login.taobao.com/", "browser_tab_id": TAB, "verification_reason": "LOGIN"}
    assert "ticket" not in str(result)
    assert ("show", {"page": TAB}) in events
    assert not any(name == "close" for name, _ in events)
    verify = {"source_url": PRODUCT, "browser_tab_id": TAB}
    assert dispatcher.dispatch(config, "collect.verify", verify)["status"] == "waiting_verification"
    assert not any(name == "hide" for name, _ in events)
    assert dispatcher.dispatch(config, "collect.verify", verify)["status"] == "ready"
    assert ("hide", {"page": TAB}) in events
    with pytest.raises(LocalOperationError):
        dispatcher.dispatch(config, "collect.read", {"product_url": SECOND, "browser_tab_id": TAB})
    result = dispatcher.dispatch(config, "collect.read", {"product_url": PRODUCT, "browser_tab_id": TAB})
    assert result["snapshot"] == signed_in
    assert [name for name, _ in events].count("open") == 1
    assert ("close", {"page": TAB}) in events
    assert dispatcher.dispatch(config, "collect.verify", verify)["status"] == "unavailable"


@pytest.mark.parametrize("url,reason", [
    ("https://login.taobao.com/member/login.jhtml", "LOGIN"),
    ("https://detail.1688.com/_____tmd_____/punish", "CAPTCHA"),
])
def test_redirected_challenges_without_visible_text(url, reason):
    assert source_site("1688").diagnose(url, "", "", "", detect_slider=True,
                                       expected_product_url=PRODUCT)[1] == reason
