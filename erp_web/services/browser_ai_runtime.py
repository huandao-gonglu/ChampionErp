"""Browser-backed AI web session runtime.

This module owns browser process/profile/CDP details for Browser AI providers.
The higher-level AI gateway should only pass model config and prompts through
this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BROWSER_AI_MODE_MANAGED_PROFILE = "managed_profile"
BROWSER_AI_MODE_EXISTING_BROWSER = "existing_browser"
BROWSER_AI_MODES = (BROWSER_AI_MODE_MANAGED_PROFILE, BROWSER_AI_MODE_EXISTING_BROWSER)
BROWSER_AI_DEFAULT_PORT = int(os.environ.get("ERP_AI_BROWSER_DEBUG_PORT", "9333"))
BROWSER_AI_PROVIDER_URLS = {
    "chatgpt": "https://chatgpt.com/",
    "claude": "https://claude.ai/new",
    "gemini": "https://gemini.google.com/app",
    "perplexity": "https://www.perplexity.ai/",
}
BROWSER_AI_PROVIDER_PORT_OFFSETS = {
    "chatgpt": 0,
    "claude": 1,
    "gemini": 2,
    "perplexity": 3,
}


@dataclass(frozen=True)
class BrowserAiRunResult:
    text: str
    image_urls: list[str]
    provider: str
    browser_url: str
    profile_dir: str
    port: int
    ready: bool = True
    title: str = ""
    page_url: str = ""
    message: str = ""


def normalize_browser_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "openai": "chatgpt",
        "chat.openai": "chatgpt",
        "chat.openai.com": "chatgpt",
        "chatgpt.com": "chatgpt",
        "anthropic": "claude",
        "claude.ai": "claude",
        "google": "gemini",
        "bard": "gemini",
        "gemini.google.com": "gemini",
    }
    return aliases.get(text, text or "chatgpt")


def normalize_browser_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in BROWSER_AI_MODES else BROWSER_AI_MODE_MANAGED_PROFILE


def browser_ai_url(model: dict[str, Any]) -> str:
    explicit = str(model.get("browser_url") or model.get("url") or "").strip()
    if explicit.startswith(("http://", "https://")):
        return explicit
    provider = normalize_browser_provider(model.get("browser_provider"))
    url = BROWSER_AI_PROVIDER_URLS.get(provider, "")
    if not url:
        raise RuntimeError(f"浏览器 AI Provider {provider} 未配置网页入口，请填写 browser_url。")
    return url


def browser_ai_profile_dir(app_dir: Path | str, model: dict[str, Any]) -> Path:
    provider = _safe_path_segment(normalize_browser_provider(model.get("browser_provider")), "chatgpt")
    profile = _safe_path_segment(str(model.get("browser_profile") or "").strip() or "default", "default")
    return Path(app_dir) / "browser_profile" / "ai" / provider / profile


def browser_ai_port(model: dict[str, Any]) -> int:
    explicit = str(model.get("browser_port") or "").strip()
    if explicit.isdigit():
        port = int(explicit)
        if 1 <= port <= 65535:
            return port
    provider = normalize_browser_provider(model.get("browser_provider"))
    if provider in BROWSER_AI_PROVIDER_PORT_OFFSETS:
        return BROWSER_AI_DEFAULT_PORT + BROWSER_AI_PROVIDER_PORT_OFFSETS[provider]
    digest = int(hashlib.sha1(provider.encode("utf-8", errors="ignore")).hexdigest()[:4], 16)
    return BROWSER_AI_DEFAULT_PORT + 10 + digest % 100


def open_browser_ai_page(app_dir: Path | str, model: dict[str, Any], timeout: int = 30) -> BrowserAiRunResult:
    provider = normalize_browser_provider(model.get("browser_provider"))
    url = browser_ai_url(model)
    profile_dir = browser_ai_profile_dir(app_dir, model)
    port = browser_ai_port(model)
    target = _ensure_browser_target(app_dir, model, url, timeout)
    cdp = _cdp_websocket(str(target["webSocketDebuggerUrl"]))
    try:
        _prepare_page(cdp, url, timeout)
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
        _prepare_page(cdp, url, min(timeout, 45))
        readiness = _readiness(cdp)
        if not readiness.get("hasInput"):
            raise RuntimeError("浏览器 AI 页面没有发现可输入的对话框；请先在打开的页面中完成登录。")
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


def _safe_path_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return text[:80] or fallback


def _cdp_websocket(websocket_url: str):
    from erp_web.runtime_units.browser_debug import CdpWebSocket

    return CdpWebSocket(websocket_url)


def _find_chrome_path() -> str:
    from erp_web.runtime_units.browser_debug import find_chrome_path

    return find_chrome_path()


def _http_json(url: str, timeout: int = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def _cdp_connected(port: int) -> bool:
    try:
        payload = _http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return isinstance(payload, dict)
    except Exception:
        return False


def _wait_for_cdp(port: int, timeout: int) -> None:
    deadline = time.time() + max(3, timeout)
    while time.time() < deadline:
        if _cdp_connected(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"浏览器调试端口 {port} 启动超时。")


def _ensure_browser_target(app_dir: Path | str, model: dict[str, Any], url: str, timeout: int) -> dict[str, Any]:
    mode = normalize_browser_mode(model.get("browser_mode"))
    port = browser_ai_port(model)
    profile_dir = browser_ai_profile_dir(app_dir, model)
    if mode == BROWSER_AI_MODE_EXISTING_BROWSER:
        if not _cdp_connected(port):
            raise RuntimeError(f"未连接浏览器调试端口 {port}。请先启动带 remote debugging 的浏览器。")
    elif not _cdp_connected(port):
        profile_dir.mkdir(parents=True, exist_ok=True)
        chrome = _find_chrome_path()
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--disable-popup-blocking",
                "--start-maximized",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_cdp(port, min(max(10, timeout), 45))
    return _target_for_url(port, url)


def _target_for_url(port: int, url: str) -> dict[str, Any]:
    target = _find_target_for_url(port, url)
    if target:
        return target
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
            timeout=5,
        ):
            pass
    except Exception:
        pass
    time.sleep(1.5)
    target = _find_target_for_url(port, url)
    if target:
        return target
    pages = _page_targets(port)
    if pages:
        return pages[0]
    raise RuntimeError("没有找到可用的浏览器页面。")


def _find_target_for_url(port: int, url: str) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    expected_host = parsed.hostname or ""
    for page in _page_targets(port):
        page_url = str(page.get("url") or "")
        page_host = urllib.parse.urlparse(page_url).hostname or ""
        if expected_host and page_host.endswith(expected_host):
            return page
        if expected_host and expected_host.endswith(page_host):
            return page
    return None


def _page_targets(port: int) -> list[dict[str, Any]]:
    try:
        payload = _http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    except Exception:
        payload = []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and str(item.get("type") or "page") == "page" and item.get("webSocketDebuggerUrl")]


def _prepare_page(cdp, url: str, timeout: int) -> None:
    cdp.call("Page.enable", timeout=10)
    cdp.call("Runtime.enable", timeout=10)
    current = str(_evaluate(cdp, "location.href || ''", timeout=10) or "")
    if not current.startswith(("http://", "https://")) or urllib.parse.urlparse(current).hostname != urllib.parse.urlparse(url).hostname:
        cdp.call("Page.navigate", {"url": url}, timeout=20)
    deadline = time.time() + max(5, timeout)
    while time.time() < deadline:
        state = str(_evaluate(cdp, "document.readyState", timeout=10) or "")
        if state in {"interactive", "complete"}:
            return
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
    "normalize_browser_mode",
    "normalize_browser_provider",
    "open_browser_ai_page",
    "run_browser_ai_chat",
]
