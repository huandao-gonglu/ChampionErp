"""Browser-backed AI web session runtime.

This module owns browser process/profile/CDP details for Browser AI providers.
The higher-level AI gateway should only pass model config and prompts through
this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
import re
import time
import urllib.parse
from desktop_runtime.browser_debug_http import browser_debug_json
from desktop_runtime.browser_debug_service import browser_debug_connected, ensure_browser_debug_session
from pathlib import Path
from typing import Any

from erp_shared.browser_ai import (
    BrowserAiRunResult,
    BROWSER_AI_MODE_MANAGED_PROFILE,
    BROWSER_AI_MODE_EXISTING_BROWSER,
    normalize_browser_provider,
    normalize_browser_mode,
    browser_ai_url,
    browser_ai_profile_dir,
    browser_ai_port,
)


class BrowserLoginRequired(RuntimeError):
    """保留可见网页，由用户登录后再发起下一次操作。"""


def open_browser_ai_page(app_dir: Path | str, model: dict[str, Any], timeout: int = 30) -> BrowserAiRunResult:
    provider = normalize_browser_provider(model.get("browser_provider"))
    url = browser_ai_url(model)
    profile_dir = browser_ai_profile_dir(app_dir, model)
    port = browser_ai_port(model)
    target = _ensure_browser_target(app_dir, model, url, timeout)
    cdp = _cdp_websocket(str(target["webSocketDebuggerUrl"]))
    try:
        _prepare_page(cdp, timeout)
        readiness = _readiness(cdp)
        ready = bool(readiness.get("hasInput"))
        message = "浏览器 AI 页面已连接。" if ready else "浏览器已打开，请在页面中手动登录后再测试能力。"
        return BrowserAiRunResult(
            text=str(readiness.get("textSample") or ""),
            image_urls=[],
            provider=provider,
            browser_url=url,
            profile_dir=str(profile_dir),
            port=port,
            ready=ready,
            title=str(readiness.get("title") or ""),
            page_url=str(readiness.get("url") or target.get("url") or url),
            message=message,
        )
    finally:
        cdp.close()


def run_browser_ai_chat(
    app_dir: Path | str,
    model: dict[str, Any],
    prompt: str,
    *,
    timeout: int = 180,
) -> BrowserAiRunResult:
    provider = normalize_browser_provider(model.get("browser_provider"))
    url = browser_ai_url(model)
    profile_dir = browser_ai_profile_dir(app_dir, model)
    port = browser_ai_port(model)
    target = _ensure_browser_target(app_dir, model, url, timeout)
    cdp = _cdp_websocket(str(target["webSocketDebuggerUrl"]))
    try:
        _prepare_page(cdp, min(timeout, 45))
        readiness = _readiness(cdp)
        if not readiness.get("hasInput"):
            raise BrowserLoginRequired("浏览器 AI 页面没有发现可输入的对话框；请先在打开的页面中完成登录。")
        before = _assistant_messages(cdp)
        _send_prompt(cdp, prompt)
        item = _wait_for_new_answer(cdp, len(before), timeout)
        return BrowserAiRunResult(
            text=str(item.get("text") or "").strip(),
            image_urls=[str(src) for src in item.get("images") or [] if str(src or "").strip()],
            provider=provider,
            browser_url=url,
            profile_dir=str(profile_dir),
            port=port,
            ready=True,
            title=str(_evaluate(cdp, "document.title || ''") or ""),
            page_url=str(_evaluate(cdp, "location.href || ''") or target.get("url") or url),
            message="浏览器 AI 已返回结果。",
        )
    finally:
        cdp.close()


@contextmanager
def connect_browser_ai_page(
    app_dir: Path | str, model: dict[str, Any], timeout: int = 30, *, headless: bool | None = None,
):
    """复用浏览器进程和登录资料，为独立网页能力提供连接。"""
    url = browser_ai_url(model)
    target = _ensure_browser_target(app_dir, model, url, timeout, headless=headless)
    cdp = _cdp_websocket(str(target["webSocketDebuggerUrl"]))
    try:
        _prepare_page(cdp, timeout)
        yield cdp
    finally:
        cdp.close()


def _safe_path_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return text[:80] or fallback


def _cdp_websocket(websocket_url: str):
    from desktop_runtime.browser_debug_service import CdpWebSocket

    return CdpWebSocket(websocket_url)


def _ensure_browser_target(
    app_dir: Path | str, model: dict[str, Any], url: str, timeout: int,
    *, headless: bool | None = None,
) -> dict[str, Any]:
    mode = normalize_browser_mode(model.get("browser_mode"))
    port = browser_ai_port(model)
    profile_dir = browser_ai_profile_dir(app_dir, model)
    if mode == BROWSER_AI_MODE_EXISTING_BROWSER:
        if not browser_debug_connected(port):
            raise RuntimeError(f"未连接浏览器调试端口 {port}。请先启动带 remote debugging 的浏览器。")
    else:
        ensure_browser_debug_session(
            port, profile_dir, min(max(10, timeout), 45),
            proxy_server=str(model.get("browser_proxy") or ""),
            headless=headless,
        )
    return _target_for_url(port, url)


def _target_for_url(port: int, url: str) -> dict[str, Any]:
    target = _find_target_for_url(port, url)
    if target:
        return target
    target = browser_debug_json(
        port, f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT", timeout=5,
    )
    if not isinstance(target, dict) or not target.get("webSocketDebuggerUrl"):
        raise RuntimeError("浏览器未返回目标页面，请检查浏览器连接后重试。")
    return target


def _find_target_for_url(port: int, url: str) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    expected_host = parsed.hostname or ""
    for page in _page_targets(port):
        page_url = str(page.get("url") or "")
        current = urllib.parse.urlparse(page_url)
        if not expected_host or (current.scheme, current.netloc) != (parsed.scheme, parsed.netloc):
            continue
        # Canvas 地址绑定具体应用，不能借用同域的其他对话或空白标签。
        if parsed.path.startswith(("/share/", "/app/")) and current.path != parsed.path:
            continue
        return page
    return None


def _page_targets(port: int) -> list[dict[str, Any]]:
    try:
        payload = browser_debug_json(port, "/json/list", timeout=5)
    except Exception:
        payload = []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and str(item.get("type") or "page") == "page" and item.get("webSocketDebuggerUrl")]


def _prepare_page(cdp, timeout: int) -> None:
    cdp.call("Page.enable", timeout=10)
    cdp.call("Runtime.enable", timeout=10)
    # 目标已由 _target_for_url 选中或创建；不能打断首次加载和登录重定向。
    deadline = time.monotonic() + max(0, timeout)
    while True:
        current = str(_evaluate(cdp, "location.href || ''", timeout=10) or "")
        state = str(_evaluate(cdp, "document.readyState", timeout=10) or "")
        if current.startswith(("http://", "https://")) and state in {"interactive", "complete"}:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("浏览器页面加载超时，请检查网络和浏览器代理后重试。")
        time.sleep(0.5)


def _evaluate(cdp, expression: str, timeout: float = 20) -> Any:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=timeout,
    )
    return result.get("result", {}).get("value")


def _readiness(cdp) -> dict[str, Any]:
    value = _evaluate(cdp, _READINESS_SCRIPT, timeout=20)
    return value if isinstance(value, dict) else {}


def _assistant_messages(cdp) -> list[dict[str, Any]]:
    value = _evaluate(cdp, _ASSISTANT_MESSAGES_SCRIPT, timeout=20)
    return value if isinstance(value, list) else []


def _send_prompt(cdp, prompt: str) -> None:
    focused = _evaluate(cdp, _FOCUS_INPUT_SCRIPT, timeout=20)
    if not isinstance(focused, dict) or not focused.get("ok"):
        raise RuntimeError(str((focused or {}).get("error") if isinstance(focused, dict) else "") or "没有找到可输入的对话框。")
    cdp.call("Input.insertText", {"text": str(prompt or "")}, timeout=20)
    time.sleep(0.4)
    clicked = _evaluate(cdp, _CLICK_SEND_SCRIPT, timeout=10)
    if isinstance(clicked, dict) and clicked.get("ok"):
        return
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, timeout=10)
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, timeout=10)


def _wait_for_new_answer(cdp, previous_count: int, timeout: int) -> dict[str, Any]:
    deadline = time.time() + max(15, timeout)
    last_key = ""
    stable_since = 0.0
    last_item: dict[str, Any] = {}
    while time.time() < deadline:
        messages = _assistant_messages(cdp)
        candidates = messages[previous_count:] if len(messages) > previous_count else messages[-1:]
        item = candidates[-1] if candidates else {}
        text = str(item.get("text") or "").strip()
        images = [str(src) for src in item.get("images") or [] if str(src or "").strip()]
        key = text + "\n" + "\n".join(images)
        if key:
            if key != last_key:
                last_key = key
                stable_since = time.time()
                last_item = {"text": text, "images": images}
            elif time.time() - stable_since >= 2.5 and not _page_busy(cdp):
                return last_item
        time.sleep(1.0)
    if last_item:
        return last_item
    raise RuntimeError("等待浏览器 AI 回复超时。")


def _page_busy(cdp) -> bool:
    try:
        return bool(_evaluate(cdp, _BUSY_SCRIPT, timeout=5))
    except Exception:
        return False


_READINESS_SCRIPT = r"""
(() => {
  const clean = (text) => String(text || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const selectors = ['textarea', '[contenteditable="true"]', '[role="textbox"]', 'div.ProseMirror'];
  const inputs = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector))).filter((el) => visible(el) && !el.disabled);
  const bodyText = clean(document.body ? document.body.innerText : '');
  return {
    title: document.title || '',
    url: location.href || '',
    hasInput: inputs.length > 0,
    loginLikely: !inputs.length && /(log in|sign in|登录|登入|continue|继续)/i.test(bodyText),
    textSample: bodyText.slice(0, 800),
  };
})()
"""


_FOCUS_INPUT_SCRIPT = r"""
(() => {
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const selectors = ['textarea', '[contenteditable="true"]', '[role="textbox"]', 'div.ProseMirror'];
  const inputs = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector))).filter((el) => visible(el) && !el.disabled);
  const target = inputs[inputs.length - 1];
  if (!target) return { ok: false, error: 'NO_INPUT' };
  target.scrollIntoView({ block: 'center', inline: 'nearest' });
  target.focus();
  if ('value' in target && String(target.value || '').trim()) {
    target.value = '';
    target.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return { ok: true };
})()
"""


_CLICK_SEND_SCRIPT = r"""
(() => {
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const buttons = Array.from(document.querySelectorAll('button')).filter((button) => visible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true');
  const sendButton = buttons.find((button) => {
    const label = [
      button.getAttribute('aria-label'),
      button.getAttribute('title'),
      button.getAttribute('data-testid'),
      button.textContent,
    ].join(' ').toLowerCase();
    return /send|submit|发送|提交/.test(label);
  });
  if (sendButton) {
    sendButton.click();
    return { ok: true, method: 'button' };
  }
  const form = document.activeElement && document.activeElement.closest ? document.activeElement.closest('form') : null;
  if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return { ok: true, method: 'form' };
  }
  return { ok: false };
})()
"""


_ASSISTANT_MESSAGES_SCRIPT = r"""
(() => {
  const clean = (text) => String(text || '').replace(/\u00a0/g, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const selectors = [
    '[data-message-author-role="assistant"]',
    '[data-testid*="assistant" i]',
    '[data-testid*="bot" i]',
    '[class*="assistant" i]',
    'message-content',
    '.model-response-text',
    '.markdown',
    'article',
  ];
  const items = [];
  const seen = new Set();
  const add = (el) => {
    const text = clean(el.innerText || el.textContent || '');
    const images = Array.from(el.querySelectorAll('img'))
      .map((img) => img.currentSrc || img.src || '')
      .filter((src) => src && !src.startsWith('data:image/svg'));
    const key = text + '|' + images.join('|');
    if ((text || images.length) && !seen.has(key)) {
      seen.add(key);
      items.push({ text, images });
    }
  };
  for (const selector of selectors) {
    for (const el of Array.from(document.querySelectorAll(selector))) add(el);
    if (items.length) break;
  }
  if (!items.length && document.body) {
    const images = Array.from(document.images).map((img) => img.currentSrc || img.src || '').filter(Boolean);
    items.push({ text: clean(document.body.innerText || ''), images });
  }
  return items.slice(-20);
})()
"""


_BUSY_SCRIPT = r"""
(() => {
  const text = document.body ? document.body.innerText || '' : '';
  if (/stop generating|停止生成|正在生成|generating/i.test(text)) return true;
  return Array.from(document.querySelectorAll('button')).some((button) => {
    const label = [button.getAttribute('aria-label'), button.getAttribute('title'), button.textContent].join(' ');
    return /stop|停止/.test(label);
  });
})()
"""


__all__ = [
    "BROWSER_AI_MODE_EXISTING_BROWSER",
    "BROWSER_AI_MODE_MANAGED_PROFILE",
    "BROWSER_AI_MODES",
    "BrowserAiRunResult",
    "browser_ai_port",
    "browser_ai_profile_dir",
    "browser_ai_url",
    "connect_browser_ai_page",
    "normalize_browser_mode",
    "normalize_browser_provider",
    "open_browser_ai_page",
    "BrowserLoginRequired",
    "run_browser_ai_chat",
]
