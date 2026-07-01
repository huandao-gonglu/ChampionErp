"""Unified AI gateway for model-level AI configuration."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import ai_model_config, config_service

logger = logging.getLogger(__name__)


class AIHTTPError(RuntimeError):
    """HTTP error raised by the configured AI model endpoint."""

    def __init__(
        self,
        *,
        status_code: int,
        reason: str,
        detail: str,
        model_id: str,
        model_name: str,
        api_style: str,
        endpoint: str,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        self.model_id = model_id
        self.model_name = model_name
        self.api_style = api_style
        self.endpoint = endpoint
        model_label = model_id or model_name or "unknown"
        detail_text = f": {detail}" if detail else f": {reason}" if reason else ""
        super().__init__(
            f"AI 模型请求失败：{model_label} ({api_style}, {endpoint}) HTTP {status_code}{detail_text}"
        )


def parse_json_text(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    jsonl_items = _parse_jsonl_items_text(text)
    if jsonl_items:
        return {"items": jsonl_items}
    raise ValueError("AI response JSON must be an object.")


def _parse_jsonl_items_text(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line.endswith(","):
            line = line[:-1].rstrip()
        if "{" in line and "}" in line and not line.startswith("{"):
            line = line[line.find("{") : line.rfind("}") + 1]
        try:
            payload = json.loads(line)
        except Exception:
            continue
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("items"), list):
                items.extend(item for item in row["items"] if isinstance(item, dict))
                continue
            if isinstance(row.get("item"), dict):
                items.append(row["item"])
                continue
            if row.get("title") or row.get("name") or row.get("source_url") or row.get("sourceUrl"):
                items.append(row)
    return items


def _chat_completions_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/chat/completions"):
        return text
    return f"{text}/chat/completions"


def _responses_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/responses"):
        return text
    for suffix in ("/chat/completions", "/images/generations", "/images/edits"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return f"{text}/responses"


def _models_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    for suffix in ("/chat/completions", "/images/generations", "/images/edits"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return f"{text}/models"


def _image_generations_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/images/generations"):
        return text
    return f"{text}/images/generations"


def _image_edits_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/images/edits"):
        return text
    return f"{text}/images/edits"


def _chat_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item or ""))
            return "\n".join(part for part in parts if part)
        return str(content or first.get("text") or "")
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            record = item if isinstance(item, dict) else {}
            content = record.get("content")
            if isinstance(content, list):
                for part in content:
                    part_record = part if isinstance(part, dict) else {}
                    parts.append(str(part_record.get("text") or part_record.get("content") or ""))
        joined = "".join(part for part in parts if part)
        if joined:
            return joined
    return output_text if isinstance(output_text, str) else ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


def _chat_stream_delta_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("delta"), str):
        return payload["delta"]
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    choices = payload.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            text = _content_to_text(delta.get("content")) or _content_to_text(message.get("content")) or str(choice.get("text") or "")
            if text:
                parts.append(text)
        return "".join(parts)
    return ""


def _read_chat_stream_text(response: Any, token_callback: Callable[[str], None] | None = None) -> str:
    parts: list[str] = []
    fallback_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            fallback_lines.append(line)
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON AI stream event: %s", data[:200])
            continue
        delta = _chat_stream_delta_text(payload)
        if not delta:
            continue
        parts.append(delta)
        if token_callback:
            token_callback(delta)
    return "".join(parts) if parts else "\n".join(fallback_lines)


def _responses_stream_delta_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    event_type = str(payload.get("type") or "")
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        return str(payload.get("delta") or "")
    return _chat_stream_delta_text(payload)


def _read_responses_stream_text(response: Any, token_callback: Callable[[str], None] | None = None) -> str:
    parts: list[str] = []
    fallback_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            fallback_lines.append(line)
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON AI responses stream event: %s", data[:200])
            continue
        delta = _responses_stream_delta_text(payload)
        if not delta:
            continue
        parts.append(delta)
        if token_callback:
            token_callback(delta)
    return "".join(parts) if parts else "\n".join(fallback_lines)


def _parse_chat_json_text_or_payload(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw_text or ""))
        if isinstance(payload, dict) and ("choices" in payload or "output_text" in payload):
            return parse_json_text(_chat_response_text(payload))
    except Exception:
        pass
    return parse_json_text(raw_text)


def _responses_input(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
        }
        for message in messages
    ]


def _model_api_style(model: dict[str, Any]) -> str:
    return ai_model_config.normalize_api_style(model.get("api_style"))


def _web_search_body_for_model(model: dict[str, Any]) -> dict[str, Any]:
    extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
    if _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        return {"tools": extra.get("web_search_tools") or [{"type": "web_search"}]}
    return {"web_search_options": extra.get("web_search_options") or {"search_context_size": "medium"}}


def _safe_endpoint_label(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    if not parsed.netloc:
        return str(url or "").strip()
    return f"{parsed.netloc}{parsed.path}"


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except Exception:
        detail = ""
    text = re.sub(r"\s+", " ", str(detail or "").strip())
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", text, flags=re.IGNORECASE)
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", text)
    return text[:1000]


def _ai_http_error(
    exc: urllib.error.HTTPError,
    *,
    model: dict[str, Any],
    model_name: str,
    api_style: str,
    url: str,
) -> AIHTTPError:
    return AIHTTPError(
        status_code=int(exc.code or 0),
        reason=str(exc.reason or ""),
        detail=_http_error_detail(exc),
        model_id=str(model.get("id") or ""),
        model_name=model_name,
        api_style=api_style,
        endpoint=_safe_endpoint_label(url),
    )


def _resolved_model(app_dir: Path | str, app_config: dict[str, Any] | None, use_case_id: str, model_id: str = "") -> dict[str, Any]:
    config_service.load_env(app_dir)
    return ai_model_config.resolve_ai_model(app_config, use_case_id, model_id=model_id)


def resolve_model_for_use_case(app_dir: Path | str, app_config: dict[str, Any] | None, use_case_id: str, model_id: str = "") -> dict[str, Any]:
    return _resolved_model(app_dir, app_config, use_case_id, model_id)


def _model_options(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if data is None:
            data = payload.get("models")
    else:
        data = payload
    if not isinstance(data, list):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        record = item if isinstance(item, dict) else {}
        if record:
            raw_model_id = record.get("id") or record.get("name")
        else:
            raw_model_id = item
        model_id = str(raw_model_id or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            label = str(record.get("label") or record.get("name") or model_id).strip()
            options.append({"id": model_id, "label": label or model_id})
    return options


def list_remote_models(base_url: str, api_key: str, timeout: int = 60) -> list[dict[str, str]]:
    url = _models_url(base_url)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    return _model_options(payload)


def _post_json(url: str, api_key: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _probe_chat_capability(base_url: str, api_key: str, model_name: str, timeout: int) -> None:
    _post_json(
        _chat_completions_url(base_url),
        api_key,
        {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "Reply with ok."},
                {"role": "user", "content": "ok"},
            ],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        },
        timeout,
    )


def _probe_json_capability(base_url: str, api_key: str, model_name: str, timeout: int) -> None:
    payload = _post_json(
        _chat_completions_url(base_url),
        api_key,
        {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": 'Return {"ok":true}.'},
            ],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout,
    )
    parse_json_text(_chat_response_text(payload))


def _probe_tool_calling_capability(base_url: str, api_key: str, model_name: str, timeout: int) -> None:
    payload = _post_json(
        _chat_completions_url(base_url),
        api_key,
        {
            "model": model_name,
            "messages": [{"role": "user", "content": "Call the noop tool."}],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "description": "No-op test tool.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "noop"}},
        },
        timeout,
    )
    choices = payload.get("choices") if isinstance(payload, dict) else []
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        raise RuntimeError("Provider accepted tool parameters but did not return tool_calls.")


def _web_search_probe_prompt() -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are verifying whether this API call has live web search/browser access. "
                "Use live web access only. Do not answer from memory. Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Open or search this exact page now: https://www.amazon.com/Best-Sellers/zgbs . "
                "Return {\"can_access_web\": true, \"source_url\": \"...\", \"evidence\": \"...\"} only if you can verify it now. "
                "If no live web/search tool is available, or access fails, return "
                "{\"can_access_web\": false, \"reason\": \"...\"}."
            ),
        },
    ]


def _validate_web_search_probe(payload: dict[str, Any]) -> None:
    data = parse_json_text(_chat_response_text(payload))
    if data.get("can_access_web") is not True:
        raise RuntimeError(str(data.get("reason") or "Provider did not prove live web access."))
    source_url = str(data.get("source_url") or "").lower()
    evidence = str(data.get("evidence") or "").strip()
    if "amazon.com" not in source_url or not evidence:
        raise RuntimeError("Provider did not return traceable Amazon evidence for live web access.")


def _probe_web_search_capability(model: dict[str, Any], api_key: str, model_name: str, timeout: int) -> None:
    base_url = ai_model_config.model_base_url(model)
    if _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        payload = _post_json(
            _responses_url(base_url),
            api_key,
            {
                "model": model_name,
                "input": _responses_input(_web_search_probe_prompt()),
                "temperature": 0,
                "max_output_tokens": 600,
                **_web_search_body_for_model(model),
            },
            timeout,
        )
    else:
        payload = _post_json(
            _chat_completions_url(base_url),
            api_key,
            {
                "model": model_name,
                "messages": _web_search_probe_prompt(),
                "temperature": 0,
                "max_tokens": 600,
                "stream": False,
                "response_format": {"type": "json_object"},
                **_web_search_body_for_model(model),
            },
            timeout,
        )
    _validate_web_search_probe(payload)


def _probe_image_generate_capability(base_url: str, api_key: str, model_name: str, timeout: int) -> None:
    _post_json(
        _image_generations_url(base_url),
        api_key,
        {
            "model": model_name,
            "prompt": "single small blue square",
            "n": 1,
            "size": "1024x1024",
        },
        timeout,
    )


def _multipart_body(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----champion-erp-ai-probe"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, content, content_type) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _probe_image_edit_capability(base_url: str, api_key: str, model_name: str, timeout: int) -> None:
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    body, boundary = _multipart_body(
        {"model": model_name, "prompt": "turn the pixel blue", "size": "1024x1024", "n": "1"},
        {"image": ("probe.png", tiny_png, "image/png")},
    )
    request = urllib.request.Request(
        _image_edits_url(base_url),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _capability_error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = _http_error_detail(exc)
        return f"{exc.code} {detail or exc.reason}".strip()
    return str(exc)


def probe_model_capabilities(model: dict[str, Any], api_key: str, model_name: str, capabilities: list[str], timeout: int) -> dict[str, Any]:
    base_url = ai_model_config.model_base_url(model)
    probes = {
        ai_model_config.CAP_CHAT: _probe_chat_capability,
        ai_model_config.CAP_JSON: _probe_json_capability,
        ai_model_config.CAP_TOOL_CALLING: _probe_tool_calling_capability,
        ai_model_config.CAP_IMAGE_GENERATE: _probe_image_generate_capability,
        ai_model_config.CAP_IMAGE_EDIT: _probe_image_edit_capability,
    }
    results: dict[str, dict[str, str | bool]] = {}
    supported: list[str] = []
    unsupported: list[str] = []
    for capability in ai_model_config.normalize_capabilities(capabilities):
        probe = probes.get(capability)
        if probe is None:
            if capability == ai_model_config.CAP_WEB_SEARCH:
                try:
                    _probe_web_search_capability(model, api_key, model_name, timeout)
                    supported.append(capability)
                    results[capability] = {"ok": True, "error": ""}
                except Exception as exc:
                    unsupported.append(capability)
                    results[capability] = {"ok": False, "error": _capability_error_text(exc)}
            continue
        try:
            probe(base_url, api_key, model_name, timeout)
            supported.append(capability)
            results[capability] = {"ok": True, "error": ""}
        except Exception as exc:
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": _capability_error_text(exc)}
    return {"supported": supported, "unsupported": unsupported, "results": results}


def chat_json(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    messages: list[dict[str, str]],
    *,
    model_id: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    response_format: bool = True,
    extra_body: dict[str, Any] | None = None,
    stream: bool = False,
    token_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    model = _resolved_model(app_dir, app_config, use_case_id, model_id)
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 API Key。")
    model_name = ai_model_config.model_name(model)
    if not model_name:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置模型名。")
    url = _chat_completions_url(ai_model_config.model_base_url(model))
    if not url:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 Base URL。")
    capabilities = ai_model_config.model_effective_capabilities(model)
    api_style = _model_api_style(model)
    if api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        url = _responses_url(ai_model_config.model_base_url(model))
        body: dict[str, Any] = {
            "model": model_name,
            "input": _responses_input(messages),
            "temperature": temperature,
            "stream": bool(stream),
        }
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            body.update(_web_search_body_for_model(model))
    else:
        body = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": bool(stream),
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format and ai_model_config.CAP_JSON in capabilities:
            body["response_format"] = {"type": "json_object"}
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            body.update(_web_search_body_for_model(model))
    if extra_body:
        body.update(extra_body)
    body["stream"] = bool(stream)
    timeout = int(timeout_seconds or model.get("timeout_seconds") or 60)
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if stream:
                if api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
                    return _parse_chat_json_text_or_payload(_read_responses_stream_text(response, token_callback))
                return _parse_chat_json_text_or_payload(_read_chat_stream_text(response, token_callback))
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _ai_http_error(exc, model=model, model_name=model_name, api_style=api_style, url=url) from exc
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    return parse_json_text(_chat_response_text(payload))


def test_ai_model(app_dir: Path | str, model: dict[str, Any]) -> dict[str, Any]:
    config_service.load_env(app_dir)
    normalized = ai_model_config.normalize_ai_model(model)
    api_key = ai_model_config.model_api_key(normalized)
    if not api_key:
        raise RuntimeError("请先填写 API Key。")
    base_url = ai_model_config.model_base_url(normalized)
    if not base_url:
        raise RuntimeError("请先填写 Base URL。")
    timeout = int(normalized.get("timeout_seconds") or 60)
    available_models = list_remote_models(base_url, api_key, timeout)
    model_name = ai_model_config.model_name(normalized)
    probe_requested = model.get("probe_capabilities", True) is not False
    trigger = str(model.get("test_trigger") or "").strip()
    requested_capabilities = ai_model_config.normalize_capabilities(normalized.get("capabilities"))
    capability_probe = {"supported": [], "unsupported": [], "results": {}}
    if probe_requested:
        if not model_name:
            raise RuntimeError("请先选择模型。")
        capability_probe = probe_model_capabilities(
            normalized,
            api_key,
            model_name,
            requested_capabilities,
            timeout,
        )
    result = {
        "ok": True,
        "channel": "ai_model",
        "model_id": normalized.get("id"),
        "provider": normalized.get("provider"),
        "model": model_name,
        "available_models": available_models,
        "supported_capabilities": capability_probe["supported"],
        "unsupported_capabilities": capability_probe["unsupported"],
        "capability_results": capability_probe["results"],
        "masked_key": ai_model_config.mask_secret(api_key),
        "message": f"{normalized.get('name') or normalized.get('id')} 测试成功：接口可以连接。",
        "next_action": "可以保存配置并继续使用 AI 功能。",
    }
    logger.info(
        "AI model test result trigger=%s model_id=%s provider=%s model=%s probe=%s requested=%s supported=%s unsupported=%s capability_errors=%s available_models=%s",
        trigger,
        normalized.get("id"),
        normalized.get("provider"),
        model_name,
        probe_requested,
        requested_capabilities,
        capability_probe["supported"],
        capability_probe["unsupported"],
        {key: value.get("error") for key, value in capability_probe["results"].items() if isinstance(value, dict) and value.get("error")},
        len(available_models),
    )
    return result


__all__ = [
    "AIHTTPError",
    "chat_json",
    "list_remote_models",
    "parse_json_text",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
    "test_ai_model",
]
