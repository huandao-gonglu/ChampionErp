"""AI gateway 解析层：响应文本提取、流式增量解析、JSON 兜底与端点 URL 构造。

从 ai_gateway 拆出的纯函数集合；解析逻辑逐字保留（含 reasoning 增量剔除、
JSONL 兜底、``` 围栏时末位完整对象优先），由 tests/test_ai_gateway_stream_parsing.py 锁定。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
    if "```" in text:
        # 思考模型的草稿常包在代码围栏里：此时以最后一个完整对象为准，
        # 避免草稿对象被 JSONL 兜底误收成候选列表。
        trailing = _last_embedded_json_object(text)
        if trailing is not None:
            return trailing
    jsonl_items = _parse_jsonl_items_text(text)
    if jsonl_items:
        return {"items": jsonl_items}
    trailing = _last_embedded_json_object(text)
    if trailing is not None:
        return trailing
    raise ValueError("AI response JSON must be an object.")


def _last_embedded_json_object(text: str) -> dict[str, Any] | None:
    """Return the last complete JSON object embedded in mixed prose.

    Thinking-model output may interleave chain-of-thought prose, fenced draft
    objects, and the final answer; the trailing complete object wins. Used as
    the last parse_json_text strategy, so earlier strategies keep their
    behavior for clean payloads.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return found
        try:
            payload, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(payload, dict) and payload:
            found = payload
        index = max(end, start + 1)


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
    for suffix in ("/chat/completions", "/responses", "/images/generations", "/images/edits"):
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
    if event_type.startswith("response."):
        # Typed Responses API events outside the allowlist are not assistant
        # text. Thinking models stream chain-of-thought as
        # response.reasoning_text.delta with the same string `delta` shape;
        # falling through to _chat_stream_delta_text would leak it into the
        # parsed message body (and break JSON parsing downstream). Reasoning
        # deltas are still streamed to the conversation for display via
        # _responses_stream_reasoning_delta_text.
        return ""
    return _chat_stream_delta_text(payload)


_RESPONSES_REASONING_DELTA_EVENTS = frozenset(
    {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}
)


def _responses_stream_reasoning_delta_text(payload: Any) -> str:
    """Display-only reasoning delta: streamed to the conversation, never parsed."""
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("type") or "") in _RESPONSES_REASONING_DELTA_EVENTS:
        return str(payload.get("delta") or "")
    return ""


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
            reasoning = _responses_stream_reasoning_delta_text(payload)
            if reasoning and token_callback:
                token_callback(reasoning)
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


def _sanitize_cli_error(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", cleaned)
    return cleaned[:1000]


__all__ = [
    "parse_json_text",
    "_chat_completions_url",
    "_chat_response_text",
    "_chat_stream_delta_text",
    "_content_to_text",
    "_http_error_detail",
    "_image_edits_url",
    "_image_generations_url",
    "_last_embedded_json_object",
    "_model_options",
    "_models_url",
    "_parse_chat_json_text_or_payload",
    "_parse_jsonl_items_text",
    "_read_chat_stream_text",
    "_read_responses_stream_text",
    "_responses_input",
    "_responses_stream_delta_text",
    "_responses_stream_reasoning_delta_text",
    "_responses_url",
    "_safe_endpoint_label",
    "_sanitize_cli_error",
]
