"""Gemini 专用浏览器的无窗口运行与交互登录，不发送模型请求。"""

from contextlib import contextmanager
import time
from urllib.parse import quote, urlsplit

from erp_shared.browser_connection_config import normalize_browser_proxy
from .embedded_browser import EmbeddedPage, is_navigation_error, request

from . import browser_ai_runtime
from .operation import check_cancellation

LOGIN_WAIT_SECONDS = 300


class CanvasPreparationTimeout(TimeoutError):
    """等待登录或会话空闲超时，尚未提交本次修图。"""
    code = "CANVAS_REQUEST_NOT_SENT"
    outcome_unknown = False


class CanvasLoginRequired(RuntimeError):
    """非交互状态检查发现尚未登录 Google 账号。"""
    code = "CANVAS_LOGIN_REQUIRED"
    outcome_unknown = False


_AUTH_SCRIPT = r"""(() => {
    const links = [...document.querySelectorAll('a[href]')];
    // 分享页可能收起账号头像；登录判断不能依赖它的布局宽度。
    const accountLink = links.find(e => {
        try { const u = new URL(e.href); return u.hostname === 'accounts.google.com'
            && u.pathname === '/SignOutOptions'; } catch { return false; }
    });
    const signIn = links.some(e => {
        try { const u = new URL(e.href); return u.hostname === 'accounts.google.com'
            && (u.pathname.includes('ServiceLogin') || u.pathname.includes('/signin/')); }
        catch { return false; }
    }) || (
        location.hostname === 'gemini.google.com'
        && document.readyState === 'complete'
        && !accountLink
        && [...document.querySelectorAll('a,button,[role="button"]')].some(el => {
            const text = (el.textContent || el.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim().toLowerCase();
            return text === 'sign in' || text === '登录' || text === '登入';
        })
    );
    const challenge = location.pathname.startsWith('/sorry/') ||
        [...document.querySelectorAll('iframe[src*="recaptcha"]')].some(frame => {
            const rect = frame.getBoundingClientRect()
            return !frame.closest('.grecaptcha-badge') && frame.checkVisibility({opacityProperty: true, visibilityProperty: true})
                && rect.width > 100 && rect.height > 50 && rect.right > 0 && rect.bottom > 0
                && rect.left < innerWidth && rect.top < innerHeight
        });
    const signedIn = !challenge && location.hostname === 'gemini.google.com' && Boolean(accountLink);
    let email = '';
    let name = '';
    if (signedIn) {
        const emailPattern = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
        const labelCandidates = [];
        for (const el of [accountLink, accountLink?.parentElement, accountLink?.querySelector('img')]) {
            if (!el) continue;
            for (const attr of ['aria-label', 'title', 'alt']) {
                const text = (el.getAttribute?.(attr) || '').trim();
                if (text) labelCandidates.push(text);
            }
        }
        for (const label of labelCandidates) {
            const match = label.match(emailPattern);
            if (match) {
                email = match[0];
                const before = label.slice(0, match.index)
                    .replace(/^(?:Google\s*(?:账号|帳號|Account)\s*[:：]?\s*)/i, '')
                    .replace(/[\s()（）\n]+$/g, '')
                    .trim();
                if (before && before.length <= 100) name = before;
                break;
            }
        }
        if (!email) {
            const wizEmail = typeof window.WIZ_global_data?.oPEP7c === 'string' ? window.WIZ_global_data.oPEP7c.trim() : '';
            if (emailPattern.test(wizEmail)) email = wizEmail.match(emailPattern)[0];
        }
        if (!email) {
            const headerNode = document.querySelector('[role="banner"] [aria-label*="@"], header [aria-label*="@"], [data-email*="@"]');
            const raw = (headerNode?.getAttribute?.('data-email') || headerNode?.getAttribute?.('aria-label') || '').trim();
            const match = raw.match(emailPattern);
            if (match) email = match[0];
        }
    }
    const onGoogleAuth = location.hostname === 'accounts.google.com'
        || location.hostname === 'accounts.youtube.com'
        || location.hostname === 'gds.google.com'
        || /^accounts\.google\.[a-z.]+$/.test(location.hostname)
        || /^consent\.google\.[a-z.]+$/.test(location.hostname);
    return {
        signedIn,
        loginRequired: challenge || onGoogleAuth || signIn,
        email,
        name,
    };
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
            email = str(value.get("email") or "").strip()
            name = str(value.get("name") or "").strip()
            if email:
                cdp.account_email = email
            if name:
                cdp.account_name = name
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
        check_cancellation()
        if state == "signed_in" or (state == "login_required" and not wait_for_login):
            return state
        if time.monotonic() >= deadline:
            if wait_for_login:
                raise CanvasPreparationTimeout("等待 Google 登录超时，本次尚未发送修图请求；登录后可重新执行。")
            raise RuntimeError("Gemini 页面未能完成加载，请检查网络和代理；本次尚未发送修图请求。")
        if wait_for_login and state == "login_required":
            request('recover_login', page=cdp.id, expression=_AUTH_SCRIPT)
        time.sleep(0.5)


@contextmanager
def connect_gemini_page(
    app_dir, model, timeout: int = 45, *, force_login: bool = False, allow_interactive: bool = True,
):
    if force_login and not allow_interactive:
        raise CanvasLoginRequired("当前 Google 账号需要重新登录。")
    url = browser_ai_runtime.browser_ai_url(model)
    open_kwargs = {
        'kind': 'gemini',
        'url': url,
        'profile': model.get('browser_profile') or 'default',
        'proxy': normalize_browser_proxy(model.get('browser_proxy')),
        'timeout': timeout,
    }
    if force_login:
        open_kwargs['initial_url'] = 'https://accounts.google.com/ServiceLogin?continue=' + quote(url, safe='')
        open_kwargs['show'] = True
    page_id = request('open', **open_kwargs)
    page = EmbeddedPage(page_id)
    # 页面由本次管道调用持有，异常、取消和执行器退出均由桌面端释放。
    if force_login or _wait_auth_state(page, timeout) != 'signed_in':
        if not allow_interactive:
            raise CanvasLoginRequired("当前 Google 账号未登录或登录已过期，请重新登录。")
        if not force_login:
            page.show()
        _wait_auth_state(page, LOGIN_WAIT_SECONDS, wait_for_login=True)
        current = urlsplit(browser_ai_runtime._evaluate(page, 'location.href'))
        wanted = urlsplit(url)
        if (current.netloc, current.path) != (wanted.netloc, wanted.path):
            request('navigate', page=page_id, url=url)
            _wait_auth_state(page, LOGIN_WAIT_SECONDS, wait_for_login=True)
        page.hide()
    try:
        yield page
    finally:
        # 仅释放仍健康的页面回空闲池；超时/连接中断等已失活页面交由桌面 finish 关闭。
        if getattr(page, 'reusable', True):
            try:
                request('release_canvas', page=page_id)
            except Exception:
                pass


__all__ = ["CanvasLoginRequired", "CanvasPreparationTimeout", "connect_gemini_page"]
