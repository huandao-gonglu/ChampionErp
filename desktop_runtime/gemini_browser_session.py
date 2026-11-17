"""Gemini 专用浏览器的无窗口运行与交互登录，不发送模型请求。"""

from contextlib import contextmanager
import time
from urllib.parse import quote, urlsplit

from erp_shared.browser_connection_config import normalize_browser_proxy
from .embedded_browser import EmbeddedPage, is_navigation_error, request

from . import browser_ai_runtime
from .operation import check_cancellation

LOGIN_WAIT_SECONDS = 300

_AUTH_SCRIPT = """(() => {
    const links = [...document.querySelectorAll('a[href]')];
    // 分享页可能收起账号头像；登录判断不能依赖它的布局宽度。
    const account = links.some(e => {
        try { const u = new URL(e.href); return u.hostname === 'accounts.google.com'
            && u.pathname === '/SignOutOptions'; } catch { return false; }
    });
    const signIn = links.some(e => {
        try { const u = new URL(e.href); return u.hostname === 'accounts.google.com'
            && (u.pathname.includes('ServiceLogin') || u.pathname.includes('/signin/')); }
        catch { return false; }
    });
    const challenge = location.pathname.startsWith('/sorry/') ||
        [...document.querySelectorAll('iframe[src*="recaptcha"]')].some(frame => {
            const rect = frame.getBoundingClientRect()
            return !frame.closest('.grecaptcha-badge') && frame.checkVisibility({opacityProperty: true, visibilityProperty: true})
                && rect.width > 100 && rect.height > 50 && rect.right > 0 && rect.bottom > 0
                && rect.left < innerWidth && rect.top < innerHeight
        });
    return {signedIn: !challenge && location.hostname === 'gemini.google.com' && account,
        loginRequired: challenge || location.hostname === 'accounts.google.com' || signIn};
})()"""


def _auth_state(cdp) -> str:
    try:
        value = browser_ai_runtime._evaluate(cdp, _AUTH_SCRIPT, timeout=5)
    except RuntimeError as error:
        if is_navigation_error(error):
            return "loading"
        raise
    if isinstance(value, dict):
        if value.get("signedIn"):
            return "signed_in"
        if value.get("loginRequired"):
            return "login_required"
    return "loading"


def _wait_auth_state(
    cdp, timeout: float, *, wait_for_login: bool = False,
) -> str:
    deadline = time.monotonic() + timeout
    while True:
        check_cancellation()
        state = _auth_state(cdp)
        if state == "signed_in" or (state == "login_required" and not wait_for_login):
            return state
        if time.monotonic() >= deadline:
            if wait_for_login:
                raise TimeoutError("等待 Google 登录超时，本次尚未发送修图请求；登录后可重新执行。")
            raise RuntimeError("Gemini 页面未能完成加载，请检查网络和代理；本次尚未发送修图请求。")
        time.sleep(0.5)


@contextmanager
def connect_gemini_page(app_dir, model, timeout: int = 45, *, force_login: bool = False):
    url = browser_ai_runtime.browser_ai_url(model)
    page_id = request('open', kind='gemini', url=url, profile=model.get('browser_profile') or 'default',
                      proxy=normalize_browser_proxy(model.get('browser_proxy')), timeout=timeout)
    page = EmbeddedPage(page_id)
    # 页面由本次管道调用持有，异常、取消和执行器退出均由桌面端释放。
    if force_login:
        page.show()
        request('navigate', page=page_id, url='https://accounts.google.com/ServiceLogin?continue=' + quote(url, safe=''))
    if force_login or _wait_auth_state(page, timeout) != 'signed_in':
        if not force_login:
            page.show()
        _wait_auth_state(page, LOGIN_WAIT_SECONDS, wait_for_login=True)
        current = urlsplit(browser_ai_runtime._evaluate(page, 'location.href'))
        wanted = urlsplit(url)
        if (current.netloc, current.path) != (wanted.netloc, wanted.path):
            request('navigate', page=page_id, url=url)
            _wait_auth_state(page, LOGIN_WAIT_SECONDS, wait_for_login=True)
        page.hide()
    yield page
    request('close', page=page_id)


__all__ = ["connect_gemini_page"]
