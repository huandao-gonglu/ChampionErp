"""Gemini Canvas 浏览器图片通道；仅使用页面原生注入的模型转发能力。"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from io import BytesIO
import json
import re
import time
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from . import ai_model_config, browser_ai_runtime, gemini_browser_session, image_service
from .ai_gateway_provider_types import AiImageEditRequest
from .ai_provider_contracts import CAPABILITY_IMAGE_EDIT, AiImageEditProvider
from .ai_run_cancellation import check_cancellation


DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_session_locks: dict[int, Lock] = {}
_locks_guard = Lock()


def is_canvas_model(model: dict[str, Any]) -> bool:
    return (
        ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_BROWSER
        and browser_ai_runtime.normalize_browser_provider(model.get("browser_provider")) == "gemini"
    )


def _canvas_url(model: dict[str, Any]) -> str:
    url = browser_ai_runtime.browser_ai_url(model)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or parsed.hostname != "gemini.google.com"
        or parsed.username or parsed.password or parsed.port not in (None, 443)
        or not parsed.path.startswith(("/share/", "/app/"))
    ):
        raise ValueError("请在浏览器模型的网页 URL 中填写已打开的 Gemini Canvas 分享页或对话地址。")
    return url


def _evaluate(cdp, expression: str, *, session_id: str = "", context_id: int | None = None,
              timeout: float = 20) -> Any:
    params: dict[str, Any] = {"expression": expression, "returnByValue": True, "awaitPromise": True}
    if context_id is not None:
        params["contextId"] = context_id
    result = cdp.call("Runtime.evaluate", params, timeout=timeout, session_id=session_id)
    if result.get("exceptionDetails"):
        raise RuntimeError("Canvas 页面执行失败，请检查页面是否仍已登录并保持打开。")
    return result.get("result", {}).get("value")


def _canvas_context(cdp, timeout: float = 15) -> tuple[str, int]:
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
    deadline = time.monotonic() + max(0, timeout)
    while True:
        for session in dict.fromkeys(sessions):
            if session not in enabled_sessions:
                cdp.call("Runtime.enable", session_id=session, on_event=receive)
                enabled_sessions.add(session)
        for session, context in contexts:
            ready = _evaluate(cdp, """(() => Boolean(
                document.querySelector('script[data-bard-client-injected="true"]') &&
                Function.prototype.toString.call(window.fetch).includes('requestFetch')
            ))()""", session_id=session, context_id=context["id"])
            if ready:
                return session, context["id"]
        if time.monotonic() >= deadline:
            break
        # 新启动的页面可能先完成主文档，再加载 Canvas；只等待通道，不发送模型请求。
        cdp.call("Page.getFrameTree", on_event=receive)
        time.sleep(0.25)
    raise RuntimeError("未找到 Gemini Canvas 原生模型通道，请先在已登录的 Gemini 页面打开 Canvas 应用预览。")


@contextmanager
def _connection(app_dir, model, timeout, force_login=False):
    check_cancellation()
    expected = _canvas_url(model)
    port = browser_ai_runtime.browser_ai_port(model)
    with _locks_guard:
        lock = _session_locks.setdefault(port, Lock())
    deadline = time.monotonic() + max(1, timeout) + gemini_browser_session.LOGIN_WAIT_SECONDS
    while not lock.acquire(timeout=0.5):
        check_cancellation()
        if time.monotonic() >= deadline:
            raise TimeoutError("Gemini 浏览器正在处理另一张图片，本次尚未发送。")
    try:
        check_cancellation()
        with gemini_browser_session.connect_gemini_page(
            app_dir, model, min(timeout, 45), force_login=force_login,
        ) as cdp:
            current = str(_evaluate(cdp, "location.href"))
            actual, wanted = urlsplit(current), urlsplit(expected)
            if (actual.scheme, actual.netloc, actual.path) != (wanted.scheme, wanted.netloc, wanted.path):
                raise RuntimeError("当前 Gemini 标签与配置的 Canvas 地址不一致，请先打开配置的页面。")
            session, context = _canvas_context(cdp)
            yield cdp, session, context
    finally:
        lock.release()


def check_canvas_connection(app_dir, model, timeout=30) -> None:
    with _connection(app_dir, model, timeout):
        pass


def _source_part(app_dir, item: dict[str, Any]) -> dict[str, Any]:
    path = None
    for key in ("path", "local_path"):
        candidate = image_service.resolve_local_path(str(item.get(key) or ""), app_dir)
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


def _decode_results(data: dict[str, Any], source_id: str, mode: str) -> list[dict[str, Any]]:
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:1000])
    results = []
    for item in data.get("images") or []:
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
    return results


_REQUEST_SCRIPT = """async (url, payload) => {
    try {
        const response = await window.fetch(url, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); }
        catch { return {error: `Canvas 返回非 JSON 响应（HTTP ${response.status}）`}; }
        if (!response.ok || data.error) return {
            authenticationRequired: response.status === 401 || data.error?.status === 'UNAUTHENTICATED',
            error: `Canvas 请求失败（HTTP ${response.status}）：${data.error?.message || '未提供原因'}`
        };
        const images = (data.candidates || []).flatMap(c =>
            (c.content?.parts || []).map(p => p.inlineData || p.inline_data).filter(p => p &&
                (p.mimeType || p.mime_type || '').startsWith('image/'))
        ).map(p => ({data: p.data, mimeType: p.mimeType || p.mime_type}));
        if (!images.length) return {error: `Canvas 未返回图片：${
            data.promptFeedback?.blockReason || data.candidates?.[0]?.finishReason || '未提供原因'}`};
        return {images};
    } catch (error) { return {error: `Canvas 请求未完成：${error.message}`}; }
}"""


class GeminiCanvasProvider(AiImageEditProvider):
    provider_id = "gemini_canvas"

    def supports(self, model, capability) -> bool:
        return capability == CAPABILITY_IMAGE_EDIT and is_canvas_model(model)

    def edit_images(self, request: AiImageEditRequest) -> list[dict[str, Any]]:
        if request.count != 1:
            raise ValueError("Canvas 每张原图只生成一张结果，请使用现有图片批次处理多张原图。")
        if not request.prompt.strip() or not request.images:
            raise ValueError("Canvas 修图需要源图片和修改说明。")
        name = ai_model_config.model_name(request.model) or DEFAULT_IMAGE_MODEL
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}", name):
            raise ValueError("Canvas 图片模型名称格式无效。")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent?key="
        results = []
        groups = [request.images] if request.combine_references else [[item] for item in request.images]
        for group in groups:
            payload = {"contents": [{"parts": [{"text": request.prompt}, *[_source_part(request.app_dir, item) for item in group]]}],
                       "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
            for attempt in range(2):
                with _connection(request.app_dir, request.model, request.timeout_seconds, bool(attempt)) as (cdp, session, context):
                    check_cancellation()
                    try:
                        data = _evaluate(cdp, f"({_REQUEST_SCRIPT})({json.dumps(url)}, {json.dumps(payload)})",
                                         session_id=session, context_id=context, timeout=request.timeout_seconds)
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        raise RuntimeError("Canvas 请求结果未知，请核对浏览器结果后再决定是否重试；不会自动重发。") from exc
                # 仅对明确未通过认证的拒绝回执重新登录；超时、断连和额度错误不重发。
                if not isinstance(data, dict) or not data.get("authenticationRequired") or attempt:
                    break
            if not isinstance(data, dict):
                raise RuntimeError("Canvas 返回无效结果。")
            results.extend(_decode_results(data, "" if request.combine_references else str(group[0].get("id") or ""), request.mode))
        return results


__all__ = ["GeminiCanvasProvider", "check_canvas_connection", "is_canvas_model"]
