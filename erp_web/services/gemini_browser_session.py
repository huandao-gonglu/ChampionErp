"""Gemini 专用浏览器的无窗口运行与交互登录，不发送模型请求。"""

from contextlib import contextmanager
import logging
import time
from urllib.parse import quote

from . import browser_ai_runtime
from .ai_run_cancellation import check_cancellation

logger = logging.getLogger(__name__)
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
    return {signedIn: location.hostname === 'gemini.google.com' && account,
        loginRequired: location.hostname === 'accounts.google.com' || signIn};
})()"""


def _auth_state(cdp) -> str:
    value = browser_ai_runtime._evaluate(cdp, _AUTH_SCRIPT, timeout=5)
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


def _interactive_login(app_dir, model, timeout: int) -> None:
    """只显示项目托管实例，登录成功或等待结束后恢复无窗口模式。"""
    url = browser_ai_runtime.browser_ai_url(model)
    pending_error = False
    try:
        # 首次配置直接显示登录入口，不先等待隐藏的 Canvas 页面完成加载。
        login_model = {
            **model,
            "browser_url": "https://accounts.google.com/ServiceLogin?continue=" + quote(url, safe=""),
        }
        with browser_ai_runtime.connect_browser_ai_page(app_dir, login_model, timeout, headless=False) as cdp:
            # 登录在同一个专用 Profile 内由用户完成，程序不读取或填写凭据。
            cdp.call("Page.bringToFront", timeout=5)
            logger.info("已显示 Gemini 专用登录窗口，等待登录完成后自动继续。")
            try:
                _wait_auth_state(cdp, LOGIN_WAIT_SECONDS, wait_for_login=True)
            except TimeoutError:
                raise
            except (OSError, RuntimeError) as exc:
                check_cancellation()
                raise RuntimeError("Google 登录窗口已关闭或连接中断，本次尚未发送修图请求。") from exc
    except BaseException:
        pending_error = True
        raise
    finally:
        try:
            browser_ai_runtime._ensure_browser_target(app_dir, model, url, timeout, headless=True)
        except Exception:
            if not pending_error:
                raise
            logger.exception("登录等待结束后恢复无窗口浏览器失败。")


@contextmanager
def connect_gemini_page(app_dir, model, timeout: int = 45, *, force_login: bool = False):
    managed = browser_ai_runtime.normalize_browser_mode(model.get("browser_mode")) == "managed_profile"
    if not managed:
        with browser_ai_runtime.connect_browser_ai_page(app_dir, model, timeout) as cdp:
            if force_login or _wait_auth_state(cdp, timeout) != "signed_in":
                raise RuntimeError("请在已连接的 Gemini 浏览器中完成 Google 登录后重试。")
            yield cdp
        return
    if not force_login:
        with browser_ai_runtime.connect_browser_ai_page(app_dir, model, timeout, headless=True) as cdp:
            if _wait_auth_state(cdp, timeout) == "signed_in":
                yield cdp
                return
    _interactive_login(app_dir, model, timeout)
    check_cancellation()
    with browser_ai_runtime.connect_browser_ai_page(app_dir, model, timeout, headless=True) as cdp:
        if _wait_auth_state(cdp, timeout) != "signed_in":
            raise RuntimeError("Google 登录状态未能保留，本次尚未发送修图请求。")
        yield cdp


__all__ = ["connect_gemini_page"]
