"""Gemini Canvas 浏览器图片通道；仅使用页面原生注入的模型转发能力。"""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
import re
import time
from threading import Lock
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import Image

from . import browser_ai_runtime, embedded_browser, gemini_browser_session
from erp_shared.desktop import LocalImageEditRequest as AiImageEditRequest
from pathlib import Path
CAPABILITY_IMAGE_EDIT = "image_edit"
from .embedded_browser import is_browser_bound, is_navigation_error
from .operation import check_cancellation


DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
PREVIEW_REVEAL_SECONDS = 5
RATE_LIMIT_COOLDOWN_SECONDS = 120.0
_session_locks: dict[str, Lock] = {}
_cooldown_until: dict[str, float] = {}
_rr_cursor = 0
_locks_guard = Lock()

# 只处理当前已配置的 Gemini 共享应用确认框，兼容中英文提示，按钮限定在匹配的可见弹窗内。
_SHARED_APP_CONTINUE_SCRIPT = r"""(expected => {
    if (location.origin !== 'https://gemini.google.com' ||
        location.origin + location.pathname !== expected) return false;
    const visible = element => element.checkVisibility({opacityProperty: true, visibilityProperty: true});
    const text = element => (element.textContent || '').replace(/\s+/g, ' ').trim();
    const isSharedAppDialog = dialog => {
        const content = text(dialog);
        const lower = content.toLowerCase();
        const zhMatch = (content.includes('此应用是由其他用户创建的') && content.includes('不要公开个人信息'))
            || (content.includes('应用可能不安全') && content.includes('不要公开个人信息'));
        const enMatch = (lower.includes('created by another user') || lower.includes('may not be safe') || lower.includes('shared app'))
            && (lower.includes('personal information') || lower.includes('continue'));
        return visible(dialog) && (zhMatch || enMatch);
    };
    const dialogs = [...document.querySelectorAll('dialog,[role="dialog"],[aria-modal="true"]')].filter(isSharedAppDialog);
    if (dialogs.length !== 1) return false;
    const allowedButtons = new Set(['继续', 'continue', 'proceed', 'open', '打开']);
    const buttons = [...dialogs[0].querySelectorAll('button,[role="button"]')]
        .filter(button => allowedButtons.has(text(button).toLowerCase()) && visible(button) &&
            !button.closest('[inert],[aria-disabled="true"],:disabled'));
    if (buttons.length !== 1) return false;
    buttons[0].click();
    return true;
})"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decorate_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.monotonic()
    result = []
    with _locks_guard:
        cooldowns = dict(_cooldown_until)
    for item in accounts or []:
        row = dict(item)
        profile = str(row.get("profile") or row.get("id") or "")
        until = cooldowns.get(profile, 0.0)
        if until > now:
            row["status"] = "rate_limited"
            row["cooldown_seconds"] = max(1, int(until - now))
        else:
            row["cooldown_seconds"] = 0
        result.append(row)
    return result


def list_canvas_accounts() -> list[dict[str, Any]]:
    if not is_browser_bound():
        return []
    response = embedded_browser.request("canvas_accounts_list", timeout=10)
    accounts = response.get("accounts") if isinstance(response, dict) else []
    return _decorate_accounts(accounts if isinstance(accounts, list) else [])


def _sync_account_status(
    profile: str,
    *,
    email: str = "",
    name: str = "",
    status: str = "signed_in",
    last_error: str = "",
    only_if_exists: bool = False,
) -> dict[str, Any] | None:
    if not is_browser_bound() or not profile:
        return None
    try:
        if only_if_exists:
            existing = list_canvas_accounts()
            if not any(item.get("profile") == profile for item in existing):
                return None
        payload: dict[str, Any] = {
            "profile": profile,
            "status": status,
            "last_checked_at": _utc_now(),
            "last_error": str(last_error or "")[:500],
        }
        if email:
            payload["email"] = email
        if name:
            payload["name"] = name
        return embedded_browser.request("canvas_account_upsert", timeout=10, **payload)
    except Exception:
        return None


def _mark_rate_limited(profile: str, error_text: str) -> None:
    with _locks_guard:
        _cooldown_until[profile] = time.monotonic() + RATE_LIMIT_COOLDOWN_SECONDS
    _sync_account_status(profile, status="rate_limited", last_error=error_text, only_if_exists=True)


def _clear_rate_limit(profile: str) -> None:
    with _locks_guard:
        _cooldown_until.pop(profile, None)


from erp_shared.browser_ai import CANVAS_PREVIEW_WAIT_SECONDS, is_canvas_model, normalize_canvas_url


def _canvas_url(model: dict[str, Any]) -> str:
    return normalize_canvas_url(browser_ai_runtime.browser_ai_url(model))


def _evaluate(cdp, expression: str, *, session_id: str = "", context_id: int | None = None,
              timeout: float = 20) -> Any:
    params: dict[str, Any] = {"expression": expression, "returnByValue": True, "awaitPromise": True}
    if context_id is not None:
        params["contextId"] = context_id
    result = cdp.call("Runtime.evaluate", params, timeout=timeout, session_id=session_id)
    if result.get("exceptionDetails"):
        raise RuntimeError("Canvas 页面执行失败，请检查页面是否仍已登录并保持打开。")
    return result.get("result", {}).get("value")


class CanvasPreparationError(RuntimeError):
    code = "CANVAS_REQUEST_NOT_SENT"
    outcome_unknown = False


def _canvas_context(cdp, timeout: float = CANVAS_PREVIEW_WAIT_SECONDS, *, expected_url: str = "") -> tuple[str, int]:
    """从当前标签关联的 iframe 中选择原生执行环境，禁止使用隔离世界替代。"""
    sessions: list[str] = []
    contexts: list[tuple[str, dict[str, Any]]] = []

    def receive(event: dict[str, Any]) -> None:
        params = event.get("params") or {}
        if event.get("method") == "Target.attachedToTarget":
            if (params.get("targetInfo") or {}).get("type") == "iframe":
                sessions.append(params["sessionId"])
        elif event.get("method") == "Runtime.executionContextCreated":
            context = params.get("context") or {}
            origin = urlsplit(str(context.get("origin") or ""))
            if (
                (context.get("auxData") or {}).get("isDefault")
                and origin.scheme == "https"
                and (origin.hostname or "").endswith(".scf.usercontent.goog")
            ):
                contexts.append((event.get("sessionId", ""), context))

    cdp.call("Target.setAutoAttach", {
        "autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True,
        "filter": [{"type": "iframe"}, {"exclude": True}],
    }, on_event=receive)
    cdp.call("Runtime.enable", on_event=receive)
    enabled_sessions: set[str] = set()
    start = time.monotonic()
    deadline = start + max(0, timeout)
    shown_reason = ""
    consent_clicked = False
    while True:
        check_cancellation()
        if expected_url and not consent_clicked:
            try:
                consent_clicked = _evaluate(
                    cdp, f"{_SHARED_APP_CONTINUE_SCRIPT}({json.dumps(expected_url)})",
                ) is True
            except RuntimeError as exc:
                if not is_navigation_error(exc):
                    raise
        for session in dict.fromkeys(sessions):
            if session not in enabled_sessions:
                cdp.call("Runtime.enable", session_id=session, on_event=receive)
                enabled_sessions.add(session)
        for session, context in contexts.copy():
            try:
                ready = _evaluate(cdp, """(() => Boolean(
                    document.querySelector('script[data-bard-client-injected="true"]') &&
                    Function.prototype.toString.call(window.fetch).includes('requestFetch')
                ))()""", session_id=session, context_id=context["id"])
            except RuntimeError as exc:
                if not is_navigation_error(exc):
                    raise
                contexts.remove((session, context))
                continue
            if ready:
                if shown_reason:
                    cdp.hide()
                return session, context["id"]
        if time.monotonic() >= deadline:
            break
        if time.monotonic() - start >= PREVIEW_REVEAL_SECONDS:
            reason = "CANVAS_CONNECTION" if contexts else "CANVAS_PREVIEW"
            if reason != shown_reason:
                cdp.show(reason)
                shown_reason = reason
        # 新启动的页面可能先完成主文档，再加载 Canvas；只等待通道，不发送模型请求。
        cdp.call("Page.getFrameTree", on_event=receive)
        time.sleep(0.25)
    if contexts:
        raise CanvasPreparationError("已检测到 Canvas 预览，但翻译连接未就绪。")
    raise CanvasPreparationError("未检测到 Canvas 应用预览。重试时请在 ERP 弹出的 Gemini 窗口中打开预览。")


def _verify_page(cdp, wanted):
    actual = urlsplit(str(_evaluate(cdp, "location.href")))
    if (actual.scheme, actual.netloc, actual.path) != (wanted.scheme, wanted.netloc, wanted.path):
        raise CanvasPreparationError("当前 Gemini 页面与模型配置的 Canvas 地址不一致。")


@contextmanager
def _connection(app_dir, model, timeout, force_login=False, *, allow_interactive: bool = True):
    check_cancellation()
    expected = urlsplit(_canvas_url(model))
    profile = str(model.get("browser_profile") or "default")
    with _locks_guard:
        lock = _session_locks.setdefault(profile, Lock())
    deadline = time.monotonic() + max(1, timeout) + gemini_browser_session.LOGIN_WAIT_SECONDS
    while not lock.acquire(timeout=0.5):
        check_cancellation()
        if time.monotonic() >= deadline:
            raise gemini_browser_session.CanvasPreparationTimeout("Gemini 浏览器正在处理另一张图片，本次尚未发送。")
    try:
        check_cancellation()
        with ExitStack() as stack:
            cdp = None
            try:
                cdp = stack.enter_context(gemini_browser_session.connect_gemini_page(
                    app_dir, model, min(timeout, 45), force_login=force_login, allow_interactive=allow_interactive,
                ))
                _verify_page(cdp, expected)
                session, context = _canvas_context(
                    cdp, expected_url=f"{expected.scheme}://{expected.netloc}{expected.path}",
                )
                # 人工展开预览时可能导航到其他分享页；提交前再次核对归属。
                _verify_page(cdp, expected)
            except (
                CanvasPreparationError,
                gemini_browser_session.CanvasPreparationTimeout,
                gemini_browser_session.CanvasLoginRequired,
            ):
                if cdp is not None:
                    cdp.reusable = False
                raise
            except Exception as exc:
                if cdp is not None:
                    cdp.reusable = False
                raise CanvasPreparationError(f"Canvas 准备失败，图片尚未发送。{exc}") from exc
            _sync_account_status(
                profile,
                email=getattr(cdp, "account_email", ""),
                name=getattr(cdp, "account_name", ""),
                status="signed_in",
                last_error="",
            )
            yield cdp, session, context
    finally:
        lock.release()


def _select_candidate_profiles(model: dict[str, Any]) -> list[str]:
    global _rr_cursor
    fallback = [str(model.get("browser_profile") or "canvas")]
    if not is_browser_bound():
        return fallback
    accounts = list_canvas_accounts()
    if not accounts:
        return fallback
    enabled = [item for item in accounts if item.get("enabled", True)]
    if not enabled:
        raise CanvasPreparationError("所有已登录的 Google 账号均已停用，请在设置中至少启用一个账号。")
    now = time.monotonic()
    with _locks_guard:
        ready = [
            str(item["profile"]) for item in enabled
            if item.get("status") != "signed_out" and _cooldown_until.get(str(item["profile"]), 0.0) <= now
        ]
        if not ready:
            cooling = sorted(
                (str(item["profile"]) for item in enabled if item.get("status") != "signed_out"),
                key=lambda p: _cooldown_until.get(p, 0.0),
            )
            if not cooling:
                raise CanvasPreparationError("所有已启用的 Google 账号均未登录，请前往设置重新登录。")
            earliest = max(1, int(_cooldown_until.get(cooling[0], now) - now))
            raise RuntimeError(f"所有已启用的 Google 账号均处于限流冷却中（约 {earliest} 秒后恢复），请稍后重试或添加新的 Google 账号。")
        start = _rr_cursor % len(ready)
        _rr_cursor += 1
        rotated = ready[start:] + ready[:start]
        free = []
        busy = []
        for profile in rotated:
            lock = _session_locks.get(profile)
            if lock is not None and lock.locked():
                busy.append(profile)
            else:
                free.append(profile)
        return free + busy


def check_canvas_connection(app_dir, model, timeout=30, *, force_login: bool = False) -> dict[str, Any]:
    if is_browser_bound() and not force_login:
        accounts = list_canvas_accounts()
        if accounts:
            candidates = _select_candidate_profiles(model)
            last_error: Exception | None = None
            for profile in candidates:
                effective = {**model, "browser_profile": profile}
                try:
                    with _connection(app_dir, effective, timeout, force_login=False):
                        pass
                    return {"ready_accounts": min(3, max(1, len(candidates)))}
                except Exception as exc:
                    last_error = exc
                    continue
            if last_error is not None:
                raise last_error
    with _connection(app_dir, model, timeout, force_login=force_login):
        pass
    return {"ready_accounts": 1}


def login_canvas_account(
    app_dir,
    model: dict[str, Any],
    *,
    profile: str = "",
    force_login: bool = False,
    check_only: bool = False,
    timeout: int = 45,
) -> dict[str, Any]:
    target_profile = str(profile or "").strip() or f"acct_{uuid4().hex[:8]}"
    effective_model = {**model, "browser_profile": target_profile}
    try:
        with _connection(
            app_dir, effective_model, timeout,
            force_login=force_login, allow_interactive=not check_only,
        ) as (cdp, _session, _context):
            email = getattr(cdp, "account_email", "")
            name = getattr(cdp, "account_name", "")
        _clear_rate_limit(target_profile)
        saved = _sync_account_status(
            target_profile, email=email, name=name, status="signed_in", last_error="",
        ) or {}
        accounts = _decorate_accounts(saved.get("accounts") or list_canvas_accounts())
        account = next((item for item in accounts if item.get("profile") == target_profile), saved.get("account") or {
            "id": target_profile, "profile": target_profile, "email": email, "name": name,
            "enabled": True, "status": "signed_in", "last_checked_at": _utc_now(), "last_error": "",
        })
        return {"ok": True, "account": account, "accounts": accounts}
    except gemini_browser_session.CanvasLoginRequired as exc:
        _sync_account_status(target_profile, status="login_required", last_error=str(exc), only_if_exists=True)
        return {
            "ok": False,
            "login_required": True,
            "error": str(exc),
            "accounts": list_canvas_accounts(),
        }


def logout_canvas_account(profile: str) -> dict[str, Any]:
    target = str(profile or "").strip()
    if not target:
        raise ValueError("缺少要取消登录的 Google 账号标识。")
    with _locks_guard:
        lock = _session_locks.setdefault(target, Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("该 Google 账号正在执行修图任务，请等待完成后再取消登录。")
    try:
        _clear_rate_limit(target)
        result = embedded_browser.request("canvas_account_logout", timeout=15, profile=target)
        accounts = result.get("accounts") if isinstance(result, dict) else []
        return {"ok": True, "accounts": _decorate_accounts(accounts)}
    finally:
        lock.release()


def toggle_canvas_account(profile: str, enabled: bool) -> dict[str, Any]:
    target = str(profile or "").strip()
    if not target:
        raise ValueError("缺少 Google 账号标识。")
    result = embedded_browser.request("canvas_account_set_enabled", timeout=10, profile=target, enabled=bool(enabled))
    accounts = result.get("accounts") if isinstance(result, dict) else []
    return {"ok": True, "accounts": _decorate_accounts(accounts)}


def _source_part(app_dir, item: dict[str, Any]) -> dict[str, Any]:
    path = None
    for key in ("path", "local_path"):
        candidate = Path(str(item[key])) if item.get(key) else None
        if candidate and candidate.is_file():
            path = candidate
            break
    if path is None:
        raise ValueError("Canvas 修图需要可读取的本地源图片。")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Canvas 单张源图片不能超过 20 MB。")
    raw = path.read_bytes()
    with Image.open(BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format, "")
        image.verify()
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Canvas 修图仅支持 PNG、JPEG 和 WebP 图片。")
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode("ascii")}}


def _decode_results(data: dict[str, Any], source_id: str, mode: str, *, count: int = 1) -> list[dict[str, Any]]:
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:1000])
    results = []
    for item in data.get("images") or []:
        if item.get("thought") is True:
            continue
        encoded = item.get("data") or ""
        if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise RuntimeError("Canvas 返回图片超过 20 MB，未保存。")
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(raw)) as image:
            mime = Image.MIME.get(image.format, "")
            image.verify()
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime)
        if not suffix or item.get("mimeType") != mime:
            raise RuntimeError("Canvas 返回的图片格式与实际内容不一致。")
        results.append({"provider": "Gemini Canvas", "mode": mode, "source_id": source_id,
                        "bytes": raw, "suffix": suffix, "mime": mime})
    if not results:
        raise RuntimeError("Gemini Canvas 未返回真实图片；本次没有生成资产。")
    if count == 1 and len(results) > 1:
        # 多图/草稿中间图场景下，单张修图取最后一张完整终图。
        return [results[-1]]
    return results


_REQUEST_SCRIPT = """async (url, payload, timeoutMs) => {
    try {
        const signal = (timeoutMs > 0 && typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function')
            ? AbortSignal.timeout(timeoutMs) : undefined;
        const response = await window.fetch(url, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
            ...(signal ? {signal} : {}),
        });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); }
        catch {
            const redirectedToLogin = Boolean(response.redirected && /^https:\\/\\/accounts\\.google\\.com\\//.test(response.url || ''));
            return {
                authenticationRequired: response.status === 401 || redirectedToLogin,
                rateLimited: response.status === 429,
                status: response.status,
                error: `Canvas 返回非 JSON 响应（HTTP ${response.status}）`,
            };
        }
        if (!response.ok || data.error) return {
            authenticationRequired: response.status === 401 || data.error?.status === 'UNAUTHENTICATED',
            rateLimited: response.status === 429 || data.error?.status === 'RESOURCE_EXHAUSTED',
            status: response.status,
            error: `Canvas 请求失败（HTTP ${response.status}）：${data.error?.message || '未提供原因'}`,
        };
        const images = (data.candidates || []).flatMap(c =>
            (c.content?.parts || [])
                .filter(p => p && p.thought !== true)
                .map(p => p.inlineData || p.inline_data)
                .filter(p => p && p.data && (p.mimeType || p.mime_type || '').startsWith('image/'))
        ).map(p => ({data: p.data, mimeType: p.mimeType || p.mime_type}));
        if (!images.length) return {error: `Canvas 未返回图片：${
            data.promptFeedback?.blockReason || data.candidates?.[0]?.finishReason || '未提供原因'}`};
        return {images};
    } catch (error) {
        const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError';
        return {
            timedOut,
            error: timedOut
                ? 'Canvas 请求超时，结果未知，请核对浏览器结果后再决定是否重试；不会自动重发。'
                : `Canvas 请求未完成：${error?.message || String(error)}`,
        };
    }
}"""


class GeminiCanvasProvider:
    provider_id = "gemini_canvas"

    def supports(self, model, capability) -> bool:
        return capability == CAPABILITY_IMAGE_EDIT and is_canvas_model(model)

    def _run_once(self, request: AiImageEditRequest, model: dict[str, Any], url: str, payload: dict[str, Any], force_login: bool) -> dict[str, Any]:
        timeout_ms = max(1000, int(request.timeout_seconds * 1000))
        with _connection(request.app_dir, model, request.timeout_seconds, force_login) as (cdp, session, context):
            check_cancellation()
            try:
                data = _evaluate(
                    cdp,
                    f"({_REQUEST_SCRIPT})({json.dumps(url)}, {json.dumps(payload)}, {timeout_ms})",
                    session_id=session, context_id=context, timeout=request.timeout_seconds,
                )
            except (TimeoutError, OSError, RuntimeError) as exc:
                if hasattr(cdp, "reusable"):
                    cdp.reusable = False
                detail = str(exc).strip()
                suffix = f"（{detail}）" if detail else ""
                raise RuntimeError(f"Canvas 请求结果未知，请核对浏览器结果后再决定是否重试；不会自动重发。{suffix}") from exc
            if isinstance(data, dict) and data.get("timedOut"):
                if hasattr(cdp, "reusable"):
                    cdp.reusable = False
                raise RuntimeError(str(data.get("error") or "Canvas 请求超时，结果未知，请核对浏览器结果后再决定是否重试；不会自动重发。"))
        if not isinstance(data, dict):
            raise RuntimeError("Canvas 返回无效结果。")
        return data

    def edit_images(self, request: AiImageEditRequest) -> list[dict[str, Any]]:
        if request.count != 1:
            raise ValueError("Canvas 每张原图只生成一张结果，请使用现有图片批次处理多张原图。")
        if not request.prompt.strip() or not request.images:
            raise ValueError("Canvas 修图需要源图片和修改说明。")
        name = str(request.model.get("model") or "") or DEFAULT_IMAGE_MODEL
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}", name):
            raise ValueError("Canvas 图片模型名称格式无效。")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent?key="
        results = []
        groups = [request.images] if request.combine_references else [[item] for item in request.images]
        for group in groups:
            payload = {"contents": [{"parts": [{"text": request.prompt}, *[_source_part(request.app_dir, item) for item in group]]}],
                       "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
            candidates = _select_candidate_profiles(request.model)
            data: dict[str, Any] = {}
            for idx, profile in enumerate(candidates):
                has_next = idx + 1 < len(candidates)
                effective_model = request.model if not is_browser_bound() else {**request.model, "browser_profile": profile}
                data = self._run_once(request, effective_model, url, payload, False)
                if data.get("rateLimited"):
                    _mark_rate_limited(profile, str(data.get("error") or ""))
                    if has_next:
                        continue
                    break
                if data.get("authenticationRequired"):
                    _sync_account_status(profile, status="login_required", last_error=str(data.get("error") or ""), only_if_exists=True)
                    if has_next:
                        continue
                    # 仅对明确未通过认证且没有其他可用账号的拒绝回执重新登录；超时、断连和额度错误不重发。
                    data = self._run_once(request, effective_model, url, payload, True)
                    break
                _clear_rate_limit(profile)
                break
            results.extend(_decode_results(
                data, "" if request.combine_references else str(group[0].get("id") or ""), request.mode, count=request.count,
            ))
        return results


__all__ = [
    "GeminiCanvasProvider",
    "check_canvas_connection",
    "is_canvas_model",
    "list_canvas_accounts",
    "login_canvas_account",
    "logout_canvas_account",
    "toggle_canvas_account",
]
