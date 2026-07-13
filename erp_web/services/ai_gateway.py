"""Unified AI gateway for model-level AI configuration."""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable

from . import ai_model_config, browser_ai_runtime, config_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiChatRequest:
    app_dir: Path | str
    model: dict[str, Any]
    messages: list[dict[str, str]]
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    response_format: bool = True
    extra_body: dict[str, Any] | None = None
    stream: bool = False
    token_callback: Callable[[str], None] | None = None


class AiProvider(ABC):
    """统一 AI Provider 接口，隔离 API、CLI 等不同接入方式。"""

    provider_id: str

    @abstractmethod
    def supports(self, model: dict[str, Any]) -> bool:
        """Return whether this provider can handle the normalized model row."""

    @abstractmethod
    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        """Run a chat request and return a parsed JSON object."""

    @abstractmethod
    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate provider-specific connectivity and capability support."""


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


def _cli_command_parts(command: str) -> list[str]:
    parts = shlex.split(str(command or "").strip())
    if not parts:
        raise RuntimeError("请先填写 CLI 命令路径。")
    return parts


def _cli_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> str:
    return _conversation_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
        channel="cli",
    )


def _conversation_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool,
    allow_generated_artifacts: bool,
    channel: str,
) -> str:
    system_parts: list[str] = []
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = str(message.get("role") or "user").strip().lower()
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            assistant_parts.append(content)
        else:
            user_parts.append(content)

    sections: list[str] = ["请完成下面的任务。"]
    task_text = "\n\n".join(user_parts).strip()
    if task_text:
        sections.append(f"任务：\n{task_text}")

    requirements: list[str] = []
    if allow_generated_artifacts:
        if channel == "browser":
            requirements.append("可以使用当前网页会话可用的图像生成能力；不要返回 SVG、ASCII 图或文字描述来替代真实图片。")
        else:
            requirements.append("可以使用当前会话可用的图像生成工具，并可以把生成的图片保存到默认生成目录；不要修改项目文件或业务数据。")
    elif allow_external_read:
        if channel == "browser":
            requirements.append("可以使用当前网页会话可用的联网或搜索能力，只基于实时验证结果回答。")
        else:
            requirements.append("不要修改文件。允许为完成任务进行只读联网检索或读取公开网页；不要执行会改变外部状态的操作。")
    else:
        if channel == "browser":
            requirements.append("只生成最终答案，不要解释执行过程。")
        else:
            requirements.append("不要修改文件，不要执行外部操作；只生成最终答案。")
    if response_format:
        requirements.append("最终输出必须是一个合法 JSON 对象；不要输出 Markdown 代码块、解释文字或前后缀。")
    requirements.extend(system_parts)
    if requirements:
        sections.append("要求：\n" + "\n".join(f"- {item}" for item in requirements))
    if assistant_parts:
        sections.append("已有上下文：\n" + "\n\n".join(assistant_parts))
    return "\n".join(sections).strip()


def _browser_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> str:
    return _conversation_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
        channel="browser",
    )


def _sanitize_cli_error(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", cleaned)
    return cleaned[:1000]


def _codex_cli_args(app_dir: Path | str, model: dict[str, Any], output_path: str) -> list[str]:
    command = ai_model_config.model_cli_command(model)
    args = _cli_command_parts(command)
    sandbox = str(model.get("sandbox") or ai_model_config.CLI_DEFAULT_SANDBOX).strip() or ai_model_config.CLI_DEFAULT_SANDBOX
    profile = str(model.get("profile") or "").strip()
    model_name = ai_model_config.model_name(model)
    args.extend(["exec", "--color", "never", "--ephemeral", "--skip-git-repo-check", "-C", str(app_dir), "--sandbox", sandbox])
    if profile:
        args.extend(["-p", profile])
    if model_name:
        args.extend(["-m", model_name])
    args.extend(["-o", output_path, "-"])
    return args


def _run_codex_cli_text(
    app_dir: Path | str,
    model: dict[str, Any],
    prompt: str,
    timeout: int,
) -> str:
    command = ai_model_config.model_cli_command(model)
    executable = _cli_command_parts(command)[0]
    if not shutil.which(executable):
        raise RuntimeError(f"未找到本地 CLI 命令：{executable}。请先安装，或填写完整命令路径。")
    with tempfile.NamedTemporaryFile(prefix="champion_erp_codex_", suffix=".txt", delete=True) as output_file:
        args = _codex_cli_args(app_dir, model, output_file.name)
        try:
            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(app_dir),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex CLI 调用超时（{timeout} 秒）。") from exc
        if completed.returncode != 0:
            detail = _sanitize_cli_error(completed.stderr or completed.stdout)
            model_name = ai_model_config.model_name(model)
            if model_name and re.search(r"model .*not supported|model metadata .*not found", detail, flags=re.IGNORECASE):
                raise RuntimeError(
                    f"Codex CLI 模型 {model_name} 不可用。请清空 CLI 模型字段使用本机 Codex 默认模型，"
                    "或填写 Codex CLI 支持的模型名；不要沿用 DeepSeek/OpenAI-Compatible 的 API 模型名。"
                )
            hint = "请在终端运行 codex login 或 codex doctor 检查本机 Codex 状态。"
            raise RuntimeError(f"Codex CLI 调用失败：{detail or '命令返回非 0 状态'}。{hint}")
        output_file.seek(0)
        final_text = output_file.read().decode("utf-8", errors="replace").strip()
    return final_text or str(completed.stdout or "").strip()


def _chat_json_via_cli(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    timeout_seconds: int | None = None,
    response_format: bool = True,
    stream: bool = False,
    token_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    cli_tool = ai_model_config.model_cli_tool(model)
    if cli_tool != ai_model_config.CLI_TOOL_CODEX:
        raise RuntimeError(f"CLI 工具 {cli_tool} 已预留，但当前版本只支持 Codex CLI。")
    timeout = int(timeout_seconds or model.get("timeout_seconds") or 180)
    allow_external_read = ai_model_config.CAP_WEB_SEARCH in ai_model_config.normalize_capabilities(model.get("capabilities"))
    text = _run_codex_cli_text(app_dir, model, _cli_prompt(messages, response_format=response_format, allow_external_read=allow_external_read), timeout)
    if stream and token_callback and text:
        token_callback(text)
    return parse_json_text(text)


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


def _normalize_probe_messages(value: Any) -> list[dict[str, str]]:
    raw_items = value if isinstance(value, list) else []
    messages: list[dict[str, str]] = []
    for item in raw_items:
        record = item if isinstance(item, dict) else {}
        role = str(record.get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        content = str(record.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _probe_options(raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw_model if isinstance(raw_model, dict) else {}
    capability_value = raw.get("probe_only_capability") or raw.get("probe_capability")
    capabilities = ai_model_config.normalize_capabilities([capability_value] if capability_value else raw.get("probe_capabilities"))
    return {
        "capabilities": capabilities,
        "messages": _normalize_probe_messages(raw.get("probe_messages")),
        "image_prompt": str(raw.get("probe_image_prompt") or "").strip(),
    }


def _probe_messages(probe_options: dict[str, Any] | None, default: list[dict[str, str]]) -> list[dict[str, str]]:
    options = probe_options if isinstance(probe_options, dict) else {}
    messages = options.get("messages")
    return messages if isinstance(messages, list) and messages else default


def _probe_image_prompt(probe_options: dict[str, Any] | None, default: str) -> str:
    options = probe_options if isinstance(probe_options, dict) else {}
    return str(options.get("image_prompt") or "").strip() or default


def _probe_chat_capability(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    _post_json(
        _chat_completions_url(base_url),
        api_key,
        {
            "model": model_name,
            "messages": messages or [
                {"role": "system", "content": "Reply with ok."},
                {"role": "user", "content": "ok"},
            ],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        },
        timeout,
    )


def _probe_json_capability(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    payload = _post_json(
        _chat_completions_url(base_url),
        api_key,
        {
            "model": model_name,
            "messages": messages or [
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


def _web_search_probe_date_iso() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def _web_search_probe_prompt() -> list[dict[str, str]]:
    probe_date = _web_search_probe_date_iso()
    return [
        {
            "role": "system",
            "content": (
                "必须使用实时联网或搜索能力查询当前天气，不要凭记忆回答；只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请使用当前会话可用的实时联网或搜索能力，查询中国四川省成都市在 {probe_date} 的当前天气。"
                "只有在已经实时查询成功时，返回 "
                "{\"can_access_web\": true, \"source_url\": \"...\", \"location\": \"成都\", "
                f"\"date\": \"{probe_date}\", \"weather\": \"...\", \"temperature\": \"...\", \"evidence\": \"...\"}}。"
                "如果当前模型没有实时联网/搜索能力，或访问失败，返回 "
                "{\"can_access_web\": false, \"reason\": \"...\"}."
            ),
        },
    ]


def _validate_web_search_probe_data(data: dict[str, Any]) -> None:
    if data.get("can_access_web") is not True:
        raise RuntimeError(str(data.get("reason") or "Provider did not prove live web access."))
    source_url = str(data.get("source_url") or "").strip().lower()
    evidence = str(data.get("evidence") or "").strip()
    if not source_url.startswith(("http://", "https://")) or not evidence:
        raise RuntimeError("Provider did not return a traceable source URL and evidence for live web access.")
    location = str(data.get("location") or "").strip().lower()
    if "成都" not in location and "chengdu" not in location:
        raise RuntimeError("Provider did not return Chengdu as the verified weather location.")
    date_text = str(data.get("date") or "").strip()
    if date_text != _web_search_probe_date_iso():
        raise RuntimeError("Provider did not return today's China date for the weather probe.")
    weather = str(data.get("weather") or "").strip()
    temperature = str(data.get("temperature") or "").strip()
    if not weather or not temperature:
        raise RuntimeError("Provider did not return a weather condition and temperature.")


def _validate_web_search_probe(payload: dict[str, Any]) -> None:
    _validate_web_search_probe_data(parse_json_text(_chat_response_text(payload)))


def _probe_web_search_capability(
    model: dict[str, Any],
    api_key: str,
    model_name: str,
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    base_url = ai_model_config.model_base_url(model)
    probe_messages = messages or _web_search_probe_prompt()
    if _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        payload = _post_json(
            _responses_url(base_url),
            api_key,
            {
                "model": model_name,
                "input": _responses_input(probe_messages),
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
                "messages": probe_messages,
                "temperature": 0,
                "max_tokens": 600,
                "stream": False,
                "response_format": {"type": "json_object"},
                **_web_search_body_for_model(model),
            },
            timeout,
        )
    _validate_web_search_probe(payload)


def _probe_image_generate_capability(
    base_url: str,
    api_key: str,
    model_name: str,
    timeout: int,
    prompt: str = "",
) -> None:
    _post_json(
        _image_generations_url(base_url),
        api_key,
        {
            "model": model_name,
            "prompt": prompt or "single small blue square",
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


def _probe_image_edit_capability(base_url: str, api_key: str, model_name: str, timeout: int, prompt: str = "") -> None:
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    body, boundary = _multipart_body(
        {"model": model_name, "prompt": prompt or "turn the pixel blue", "size": "1024x1024", "n": "1"},
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


def probe_model_capabilities(
    model: dict[str, Any],
    api_key: str,
    model_name: str,
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = ai_model_config.model_base_url(model)
    results: dict[str, dict[str, str | bool]] = {}
    supported: list[str] = []
    unsupported: list[str] = []
    for capability in ai_model_config.normalize_capabilities(capabilities):
        try:
            if capability == ai_model_config.CAP_CHAT:
                _probe_chat_capability(
                    base_url,
                    api_key,
                    model_name,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Reply with ok."},
                            {"role": "user", "content": "ok"},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_JSON:
                _probe_json_capability(
                    base_url,
                    api_key,
                    model_name,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Return JSON only."},
                            {"role": "user", "content": 'Return {"ok":true}.'},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_WEB_SEARCH:
                _probe_web_search_capability(
                    model,
                    api_key,
                    model_name,
                    timeout,
                    _probe_messages(probe_options, _web_search_probe_prompt()),
                )
            elif capability == ai_model_config.CAP_IMAGE_GENERATE:
                _probe_image_generate_capability(base_url, api_key, model_name, timeout, _probe_image_prompt(probe_options, "single small blue square"))
            elif capability == ai_model_config.CAP_IMAGE_EDIT:
                _probe_image_edit_capability(base_url, api_key, model_name, timeout, _probe_image_prompt(probe_options, "turn the pixel blue"))
            elif capability == ai_model_config.CAP_TOOL_CALLING:
                _probe_tool_calling_capability(base_url, api_key, model_name, timeout)
            else:
                continue
            supported.append(capability)
            results[capability] = {"ok": True, "error": ""}
        except Exception as exc:
            if "Codex CLI 模型" in str(exc):
                raise
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": _capability_error_text(exc)}
    return {"supported": supported, "unsupported": unsupported, "results": results}


def _cli_json_probe(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    timeout: int,
    *,
    allow_external_read: bool = False,
) -> dict[str, Any]:
    text = _run_codex_cli_text(
        app_dir,
        model,
        _cli_prompt(messages, response_format=True, allow_external_read=allow_external_read),
        timeout,
    )
    return parse_json_text(text)


def _probe_cli_chat_capability(app_dir: Path | str, model: dict[str, Any], timeout: int, messages: list[dict[str, str]] | None = None) -> None:
    text = _run_codex_cli_text(
        app_dir,
        model,
        _cli_prompt(
            messages
            or [
                {"role": "system", "content": "Reply with ok."},
                {"role": "user", "content": "ok"},
            ],
            response_format=False,
        ),
        timeout,
    )
    if not text:
        raise RuntimeError("CLI did not return any text.")


def _probe_cli_json_capability(app_dir: Path | str, model: dict[str, Any], timeout: int, messages: list[dict[str, str]] | None = None) -> None:
    _cli_json_probe(
        app_dir,
        model,
        messages
        or [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": 'Return {"ok":true}.'},
        ],
        timeout,
    )


def _probe_cli_web_search_capability(
    app_dir: Path | str,
    model: dict[str, Any],
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    data = _cli_json_probe(
        app_dir,
        model,
        messages or _web_search_probe_prompt(),
        timeout,
        allow_external_read=True,
    )
    _validate_web_search_probe_data(data)


def _valid_base64_image(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("data:image/") and "," in text:
        text = text.split(",", 1)[1]
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(raw) >= 32 and (
        raw.startswith(b"\x89PNG\r\n\x1a\n")
        or raw.startswith(b"\xff\xd8\xff")
        or raw.startswith(b"RIFF")
    )


def _existing_image_path(value: str, app_dir: Path | str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("file://"):
        parsed = urllib.parse.urlparse(text)
        text = urllib.parse.unquote(parsed.path or "")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(app_dir) / candidate
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _cli_image_probe_data_from_text(text: str) -> dict[str, Any]:
    try:
        return parse_json_text(text)
    except Exception:
        pass
    match = re.search(r"file://[^\s)>\]\"']+", text)
    if match:
        return {"can_generate_image": True, "image_path": match.group(0)}
    match = re.search(r"https?://[^\s)>\]\"']+\.(?:png|jpe?g|webp)(?:\?[^\s)>\]\"']*)?", text, flags=re.IGNORECASE)
    if match:
        return {"can_generate_image": True, "image_url": match.group(0)}
    return {"can_generate_image": False, "reason": "CLI did not return JSON or a recognizable image URL/path."}


def _validate_cli_image_generate_probe(data: dict[str, Any], app_dir: Path | str) -> None:
    if data.get("can_generate_image") is not True:
        raise RuntimeError(str(data.get("reason") or "CLI did not prove image generation access."))
    image_url = str(data.get("image_url") or data.get("url") or "").strip()
    if image_url.startswith(("http://", "https://")):
        return
    if _existing_image_path(image_url, app_dir):
        return
    data_url = str(data.get("data_url") or data.get("dataUrl") or "").strip()
    if _valid_base64_image(data_url):
        return
    image_base64 = str(data.get("image_base64") or data.get("b64_json") or data.get("base64") or "").strip()
    if _valid_base64_image(image_base64):
        return
    image_path = str(data.get("image_path") or data.get("path") or data.get("local_path") or "").strip()
    if _existing_image_path(image_path, app_dir):
        return
    raise RuntimeError("CLI did not return a verifiable image URL, base64 image, or local image path.")


def _cli_image_generate_probe_prompt() -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Use an actual image generation tool if one is available. Do not return SVG, ASCII art, or a textual description as a substitute. "
                "Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Generate a small test image of a single blue square. "
                "Return {\"can_generate_image\": true, \"image_url\": \"https://...\"} or "
                "{\"can_generate_image\": true, \"image_base64\": \"...\", \"mime_type\": \"image/png\"} or "
                "{\"can_generate_image\": true, \"image_path\": \"...\"} only after the image exists. "
                "If no image generation tool is available, return {\"can_generate_image\": false, \"reason\": \"...\"}."
            ),
        },
    ]


def _probe_cli_image_generate_capability(
    app_dir: Path | str,
    model: dict[str, Any],
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    text = _run_codex_cli_text(
        app_dir,
        model,
        _cli_prompt(
            messages or _cli_image_generate_probe_prompt(),
            response_format=True,
            allow_generated_artifacts=True,
        ),
        timeout,
    )
    data = _cli_image_probe_data_from_text(text)
    _validate_cli_image_generate_probe(data, app_dir)


def probe_cli_model_capabilities(
    app_dir: Path | str,
    model: dict[str, Any],
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested = ai_model_config.normalize_capabilities(capabilities)
    cli_tool = ai_model_config.model_cli_tool(model)
    supported: list[str] = []
    unsupported: list[str] = []
    results: dict[str, dict[str, str | bool]] = {}
    if cli_tool != ai_model_config.CLI_TOOL_CODEX:
        for capability in requested:
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": f"CLI 工具 {cli_tool} 已预留，但当前版本只支持 Codex CLI。"}
        return {"supported": supported, "unsupported": unsupported, "results": results}
    for capability in requested:
        try:
            if capability == ai_model_config.CAP_CHAT:
                _probe_cli_chat_capability(
                    app_dir,
                    model,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Reply with ok."},
                            {"role": "user", "content": "ok"},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_JSON:
                _probe_cli_json_capability(
                    app_dir,
                    model,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Return JSON only."},
                            {"role": "user", "content": 'Return {"ok":true}.'},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_WEB_SEARCH:
                _probe_cli_web_search_capability(app_dir, model, timeout, _probe_messages(probe_options, _web_search_probe_prompt()))
            elif capability == ai_model_config.CAP_IMAGE_GENERATE:
                _probe_cli_image_generate_capability(app_dir, model, timeout, _probe_messages(probe_options, _cli_image_generate_probe_prompt()))
            else:
                raise RuntimeError("CLI Provider 当前仅支持 chat/json/web_search/image_generate 能力测试。")
            supported.append(capability)
            results[capability] = {"ok": True, "error": ""}
        except Exception as exc:
            if "Codex CLI 模型" in str(exc):
                raise
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": _capability_error_text(exc)}
    return {"supported": supported, "unsupported": unsupported, "results": results}


def _browser_chat_result(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    timeout: int,
    *,
    response_format: bool = True,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> browser_ai_runtime.BrowserAiRunResult:
    prompt = _browser_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
    )
    return browser_ai_runtime.run_browser_ai_chat(app_dir, model, prompt, timeout=timeout)


def _probe_browser_chat_capability(app_dir: Path | str, model: dict[str, Any], timeout: int, messages: list[dict[str, str]] | None = None) -> None:
    result = _browser_chat_result(
        app_dir,
        model,
        messages
        or [
            {"role": "system", "content": "Reply with ok."},
            {"role": "user", "content": "ok"},
        ],
        timeout,
        response_format=False,
    )
    if not result.text.strip():
        raise RuntimeError("浏览器 AI 没有返回文本。")


def _probe_browser_json_capability(app_dir: Path | str, model: dict[str, Any], timeout: int, messages: list[dict[str, str]] | None = None) -> None:
    result = _browser_chat_result(
        app_dir,
        model,
        messages
        or [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": 'Return {"ok":true}.'},
        ],
        timeout,
        response_format=True,
    )
    parse_json_text(result.text)


def _probe_browser_web_search_capability(
    app_dir: Path | str,
    model: dict[str, Any],
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    result = _browser_chat_result(
        app_dir,
        model,
        messages or _web_search_probe_prompt(),
        timeout,
        response_format=True,
        allow_external_read=True,
    )
    _validate_web_search_probe_data(parse_json_text(result.text))


def _browser_image_probe_data_from_result(result: browser_ai_runtime.BrowserAiRunResult) -> dict[str, Any]:
    for image_url in result.image_urls:
        if str(image_url or "").strip():
            return {"can_generate_image": True, "image_url": str(image_url).strip()}
    return _cli_image_probe_data_from_text(result.text)


def _validate_browser_image_generate_probe(data: dict[str, Any], result: browser_ai_runtime.BrowserAiRunResult, app_dir: Path | str) -> None:
    image_url = str(data.get("image_url") or data.get("url") or "").strip()
    if image_url.startswith(("http://", "https://", "blob:")):
        return
    _validate_cli_image_generate_probe(data, app_dir)


def _probe_browser_image_generate_capability(
    app_dir: Path | str,
    model: dict[str, Any],
    timeout: int,
    messages: list[dict[str, str]] | None = None,
) -> None:
    result = _browser_chat_result(
        app_dir,
        model,
        messages or _cli_image_generate_probe_prompt(),
        timeout,
        response_format=True,
        allow_generated_artifacts=True,
    )
    data = _browser_image_probe_data_from_result(result)
    _validate_browser_image_generate_probe(data, result, app_dir)


def probe_browser_model_capabilities(
    app_dir: Path | str,
    model: dict[str, Any],
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supported: list[str] = []
    unsupported: list[str] = []
    results: dict[str, dict[str, str | bool]] = {}
    for capability in ai_model_config.normalize_capabilities(capabilities):
        try:
            if capability == ai_model_config.CAP_CHAT:
                _probe_browser_chat_capability(
                    app_dir,
                    model,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Reply with ok."},
                            {"role": "user", "content": "ok"},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_JSON:
                _probe_browser_json_capability(
                    app_dir,
                    model,
                    timeout,
                    _probe_messages(
                        probe_options,
                        [
                            {"role": "system", "content": "Return JSON only."},
                            {"role": "user", "content": 'Return {"ok":true}.'},
                        ],
                    ),
                )
            elif capability == ai_model_config.CAP_WEB_SEARCH:
                _probe_browser_web_search_capability(app_dir, model, timeout, _probe_messages(probe_options, _web_search_probe_prompt()))
            elif capability == ai_model_config.CAP_IMAGE_GENERATE:
                _probe_browser_image_generate_capability(app_dir, model, timeout, _probe_messages(probe_options, _cli_image_generate_probe_prompt()))
            else:
                raise RuntimeError("浏览器 Provider 当前支持 chat/json/web_search/image_generate 能力测试；图片编辑和 Function Call 需要后续单独适配。")
            supported.append(capability)
            results[capability] = {"ok": True, "error": ""}
        except Exception as exc:
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": _capability_error_text(exc)}
    return {"supported": supported, "unsupported": unsupported, "results": results}


def _ensure_http_model_ready(model: dict[str, Any]) -> tuple[str, str, str]:
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 API Key。")
    model_name = ai_model_config.model_name(model)
    if not model_name:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置模型名。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 Base URL。")
    return api_key, model_name, base_url


def _test_http_model(app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw_model if isinstance(raw_model, dict) else {}
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError("请先填写 API Key。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise RuntimeError("请先填写 Base URL。")
    timeout = int(model.get("timeout_seconds") or 60)
    available_models = list_remote_models(base_url, api_key, timeout)
    model_name = ai_model_config.model_name(model)
    probe_requested = raw.get("probe_capabilities", True) is not False
    trigger = str(raw.get("test_trigger") or "").strip()
    probe_options = _probe_options(raw)
    requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
    capability_probe = {"supported": [], "unsupported": [], "results": {}}
    if probe_requested:
        if not model_name:
            raise RuntimeError("请先选择模型。")
        capability_probe = probe_model_capabilities(
            model,
            api_key,
            model_name,
            requested_capabilities,
            timeout,
            probe_options,
        )
    result = {
        "ok": True,
        "channel": "ai_model",
        "model_id": model.get("id"),
        "provider": model.get("provider"),
        "connection_type": ai_model_config.CONNECTION_TYPE_API,
        "api_style": _model_api_style(model),
        "model": model_name,
        "available_models": available_models,
        "supported_capabilities": capability_probe["supported"],
        "capability_results": capability_probe["results"],
        "tested_capabilities": requested_capabilities,
        "test_trigger": trigger,
        "masked_key": ai_model_config.mask_secret(api_key),
        "message": f"{model.get('name') or model.get('id')} 测试成功：接口可以连接。",
        "next_action": "可以保存配置并继续使用 AI 功能。",
    }
    logger.info(
        "AI model test result trigger=%s model_id=%s provider=%s model=%s probe=%s requested=%s supported=%s unsupported=%s capability_errors=%s available_models=%s",
        trigger,
        model.get("id"),
        model.get("provider"),
        model_name,
        probe_requested,
        requested_capabilities,
        capability_probe["supported"],
        capability_probe["unsupported"],
        {key: value.get("error") for key, value in capability_probe["results"].items() if isinstance(value, dict) and value.get("error")},
        len(available_models),
    )
    return result


class OpenAICompatibleProvider(AiProvider):
    provider_id = "openai_compatible"

    def supports(self, model: dict[str, Any]) -> bool:
        return (
            ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API
            and _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_COMPATIBLE
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        model = request.model
        api_key, model_name, base_url = _ensure_http_model_ready(model)
        url = _chat_completions_url(base_url)
        capabilities = ai_model_config.normalize_capabilities(model.get("capabilities"))
        body: dict[str, Any] = {
            "model": model_name,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": bool(request.stream),
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.response_format and ai_model_config.CAP_JSON in capabilities:
            body["response_format"] = {"type": "json_object"}
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            body.update(_web_search_body_for_model(model))
        if request.extra_body:
            body.update(request.extra_body)
        body["stream"] = bool(request.stream)
        timeout = int(request.timeout_seconds or model.get("timeout_seconds") or 60)
        payload = self._post_chat(model, api_key, model_name, url, body, timeout, request)
        return parse_json_text(_chat_response_text(payload))

    def _post_chat(
        self,
        model: dict[str, Any],
        api_key: str,
        model_name: str,
        url: str,
        body: dict[str, Any],
        timeout: int,
        request: AiChatRequest,
    ) -> dict[str, Any]:
        http_request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if request.stream else "application/json",
                "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
            },
            method="POST",
        )
        api_style = _model_api_style(model)
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                if request.stream:
                    return _parse_chat_json_text_or_payload(_read_chat_stream_text(response, request.token_callback))
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _ai_http_error(exc, model=model, model_name=model_name, api_style=api_style, url=url) from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        return _test_http_model(app_dir, model, raw_model)


class OpenAIResponsesProvider(AiProvider):
    provider_id = "openai_responses"

    def supports(self, model: dict[str, Any]) -> bool:
        return (
            ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API
            and _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        model = request.model
        api_key, model_name, base_url = _ensure_http_model_ready(model)
        url = _responses_url(base_url)
        capabilities = ai_model_config.normalize_capabilities(model.get("capabilities"))
        body: dict[str, Any] = {
            "model": model_name,
            "input": _responses_input(request.messages),
            "temperature": request.temperature,
            "stream": bool(request.stream),
        }
        if request.max_tokens is not None:
            body["max_output_tokens"] = request.max_tokens
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            body.update(_web_search_body_for_model(model))
        if request.extra_body:
            body.update(request.extra_body)
        body["stream"] = bool(request.stream)
        timeout = int(request.timeout_seconds or model.get("timeout_seconds") or 60)
        http_request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if request.stream else "application/json",
                "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                if request.stream:
                    return _parse_chat_json_text_or_payload(_read_responses_stream_text(response, request.token_callback))
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _ai_http_error(
                exc,
                model=model,
                model_name=model_name,
                api_style=ai_model_config.API_STYLE_OPENAI_RESPONSES,
                url=url,
            ) from exc
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        return parse_json_text(_chat_response_text(payload))

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        return _test_http_model(app_dir, model, raw_model)


class CodexCliProvider(AiProvider):
    provider_id = "codex_cli"

    def supports(self, model: dict[str, Any]) -> bool:
        return (
            ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_CLI
            and ai_model_config.model_cli_tool(model) == ai_model_config.CLI_TOOL_CODEX
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        return _chat_json_via_cli(
            request.app_dir,
            request.model,
            request.messages,
            timeout_seconds=request.timeout_seconds,
            response_format=request.response_format,
            stream=request.stream,
            token_callback=request.token_callback,
        )

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = raw_model if isinstance(raw_model, dict) else {}
        timeout = int(model.get("timeout_seconds") or 180)
        probe_options = _probe_options(raw)
        requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
        probe_requested = raw.get("probe_capabilities", True) is not False
        capability_probe = {"supported": [], "unsupported": [], "results": {}}
        if probe_requested:
            capability_probe = probe_cli_model_capabilities(app_dir, model, requested_capabilities, timeout, probe_options)
        command = ai_model_config.model_cli_command(model)
        executable = _cli_command_parts(command)[0] if command else ""
        installed_path = shutil.which(executable) if executable else ""
        if not installed_path:
            raise RuntimeError(f"未找到本地 CLI 命令：{executable or command}。请先安装，或填写完整命令路径。")
        return {
            "ok": True,
            "channel": "ai_model",
            "model_id": model.get("id"),
            "provider": model.get("provider"),
            "connection_type": ai_model_config.CONNECTION_TYPE_CLI,
            "cli_tool": ai_model_config.model_cli_tool(model),
            "command": command,
            "command_path": installed_path,
            "model": ai_model_config.model_name(model),
            "available_models": ([{"id": ai_model_config.model_name(model), "label": ai_model_config.model_name(model)}] if ai_model_config.model_name(model) else []),
            "supported_capabilities": capability_probe["supported"],
            "capability_results": capability_probe["results"],
            "tested_capabilities": requested_capabilities,
            "test_trigger": str(raw.get("test_trigger") or "").strip(),
            "message": f"{model.get('name') or model.get('id')} 测试成功：本地 CLI 可以调用。",
            "next_action": "可以保存配置并继续使用 AI 功能；登录和账号状态由本机 CLI 自己管理。",
        }


class BrowserAiProvider(AiProvider):
    provider_id = "browser"

    def supports(self, model: dict[str, Any]) -> bool:
        return ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_BROWSER

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        timeout = int(request.timeout_seconds or request.model.get("timeout_seconds") or 180)
        capabilities = ai_model_config.normalize_capabilities(request.model.get("capabilities"))
        result = _browser_chat_result(
            request.app_dir,
            request.model,
            request.messages,
            timeout,
            response_format=request.response_format,
            allow_external_read=ai_model_config.CAP_WEB_SEARCH in capabilities,
        )
        if request.stream and request.token_callback and result.text:
            request.token_callback(result.text)
        return parse_json_text(result.text)

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = raw_model if isinstance(raw_model, dict) else {}
        timeout = int(model.get("timeout_seconds") or 180)
        probe_options = _probe_options(raw)
        requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
        probe_requested = raw.get("probe_capabilities", True) is not False
        page = browser_ai_runtime.open_browser_ai_page(app_dir, model, timeout=min(timeout, 45))
        capability_probe = {"supported": [], "unsupported": [], "results": {}}
        if probe_requested:
            capability_probe = probe_browser_model_capabilities(app_dir, model, requested_capabilities, timeout, probe_options)
        return {
            "ok": True,
            "channel": "ai_model",
            "model_id": model.get("id"),
            "provider": model.get("provider"),
            "connection_type": ai_model_config.CONNECTION_TYPE_BROWSER,
            "browser_provider": browser_ai_runtime.normalize_browser_provider(model.get("browser_provider")),
            "browser_mode": browser_ai_runtime.normalize_browser_mode(model.get("browser_mode")),
            "browser_profile": str(model.get("browser_profile") or "default"),
            "browser_url": page.browser_url,
            "profile_dir": page.profile_dir,
            "port": page.port,
            "model": ai_model_config.model_name(model),
            "available_models": ([{"id": ai_model_config.model_name(model), "label": ai_model_config.model_name(model)}] if ai_model_config.model_name(model) else []),
            "supported_capabilities": capability_probe["supported"],
            "capability_results": capability_probe["results"],
            "tested_capabilities": requested_capabilities,
            "test_trigger": str(raw.get("test_trigger") or "").strip(),
            "ready": page.ready,
            "message": f"{model.get('name') or model.get('id')} 测试成功：浏览器网页已连接。" if page.ready else f"{model.get('name') or model.get('id')} 已打开浏览器网页，请先手动登录。",
            "next_action": "能力勾选会发送测试消息；测试成功后才会启用对应能力。" if page.ready else "在打开的浏览器窗口完成登录后，再勾选需要的能力并测试。",
        }


AI_PROVIDER_REGISTRY: tuple[AiProvider, ...] = (
    CodexCliProvider(),
    BrowserAiProvider(),
    OpenAIResponsesProvider(),
    OpenAICompatibleProvider(),
)


def _provider_for_model(model: dict[str, Any]) -> AiProvider:
    for provider in AI_PROVIDER_REGISTRY:
        if provider.supports(model):
            return provider
    if ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_CLI:
        raise RuntimeError(f"CLI 工具 {ai_model_config.model_cli_tool(model)} 已预留，但当前版本只支持 Codex CLI。")
    raise RuntimeError(f"不支持的 AI Provider 配置：connection_type={ai_model_config.model_connection_type(model)} api_style={_model_api_style(model)}")


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
    provider = _provider_for_model(model)
    return provider.chat_json(
        AiChatRequest(
            app_dir=app_dir,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            response_format=response_format,
            extra_body=extra_body,
            stream=stream,
            token_callback=token_callback,
        )
    )


def test_ai_model(app_dir: Path | str, model: dict[str, Any]) -> dict[str, Any]:
    config_service.load_env(app_dir)
    raw_model = model if isinstance(model, dict) else {}
    normalized = ai_model_config.normalize_ai_model(raw_model)
    provider = _provider_for_model(normalized)
    return provider.test_model(app_dir, normalized, raw_model)


__all__ = [
    "AI_PROVIDER_REGISTRY",
    "AIHTTPError",
    "AiChatRequest",
    "AiProvider",
    "BrowserAiProvider",
    "CodexCliProvider",
    "OpenAICompatibleProvider",
    "OpenAIResponsesProvider",
    "chat_json",
    "list_remote_models",
    "parse_json_text",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
    "test_ai_model",
]
