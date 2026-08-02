"""AI 网关保留的 JSON 与 CLI 文本解析函数。

API 协议响应和流式事件由 Pydantic Model 解析，本模块不再解释 Provider wire payload。
"""

from __future__ import annotations

import json
import re
from typing import Any


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
            if (
                row.get("title")
                or row.get("name")
                or row.get("source_url")
                or row.get("sourceUrl")
            ):
                items.append(row)
    return items


def _sanitize_cli_error(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = re.sub(
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer ***",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", cleaned)
    return cleaned[:1000]


__all__ = [
    "parse_json_text",
    "_last_embedded_json_object",
    "_parse_jsonl_items_text",
    "_sanitize_cli_error",
]
