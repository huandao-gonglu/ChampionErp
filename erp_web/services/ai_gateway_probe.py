"""AI gateway 能力探测层：探测素材、结果校验与统一探测循环。

原 ai_gateway 里 http/cli/browser 三个同构 for-capability 循环合并为
run_capability_probes；具体能力探测由各 Provider 的 probe_capability 实现。
本模块不依赖 Provider 类，只定义探测协议所需的上下文与工具函数。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import re
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from . import ai_model_config, browser_ai_runtime
from .ai_gateway_parsing import _chat_response_text, _http_error_detail, parse_json_text


class SkipCapabilityProbe(Exception):
    """Provider 声明当前能力不参与探测（既不算成功也不算失败）。"""


@dataclass(frozen=True)
class CapabilityProbeContext:
    """一次能力探测所需的全部输入；不同连接类型各取所需字段。"""

    model: dict[str, Any]
    timeout: int
    probe_options: dict[str, Any] | None = None
    app_dir: Path | str | None = None
    api_key: str = ""
    model_name: str = ""


class CapabilityProbeProvider(Protocol):
    """Provider 侧统一能力探测契约。"""

    probe_reraise_marker: str
    probe_web_search_meta: bool

    def probe_capability(
        self,
        capability: str,
        context: CapabilityProbeContext,
    ) -> dict[str, Any]:
        """执行单项探测并返回可持久化的能力配方。"""


def run_capability_probes(
    provider: CapabilityProbeProvider,
    context: CapabilityProbeContext,
    capabilities: list[str],
) -> dict[str, Any]:
    """统一的能力探测循环（原 http/cli/browser 三个同构循环的合并）。

    - Provider 抛 SkipCapabilityProbe：该能力跳过，不记录结果。
    - Provider 抛其它异常：记为 unsupported，错误文本经 _capability_error_text 归一。
    - provider.probe_reraise_marker 命中异常文本时原样上抛（配置类致命错误）。
    """
    supported: list[str] = []
    unsupported: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    for capability in ai_model_config.normalize_capabilities(capabilities):
        try:
            capability_profile = provider.probe_capability(capability, context)
        except SkipCapabilityProbe:
            continue
        except Exception as exc:
            marker = str(getattr(provider, "probe_reraise_marker", "") or "")
            if marker and marker in str(exc):
                raise
            unsupported.append(capability)
            results[capability] = {"ok": False, "error": _capability_error_text(exc)}
            continue
        supported.append(capability)
        result: dict[str, Any] = {
            "ok": True,
            "error": "",
            "capability_profile": capability_profile,
        }
        if capability == ai_model_config.CAP_WEB_SEARCH and getattr(provider, "probe_web_search_meta", False):
            result["request_mode"] = capability_profile.get("request_mode", "")
            result["api_style"] = capability_profile.get("api_style", "")
        results[capability] = result
    return {"supported": supported, "unsupported": unsupported, "results": results}


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


def _chat_probe_default_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Reply with ok."},
        {"role": "user", "content": "ok"},
    ]


def _json_probe_default_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": 'Return {"ok":true}.'},
    ]


def _web_search_probe_date_iso() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def _web_search_probe_prompt() -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "必须调用实时联网或搜索能力查询天气，不要凭记忆回答；只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请使用当前会话可用的实时联网或搜索能力，查询中国四川省成都市此刻的实时天气。"
                "不要把当前日期理解成未来天气预报；请从实时搜索结果中取得信息对应的中国日期。"
                "只有在已经实时查询成功时，返回 "
                "{\"can_access_web\": true, \"source_url\": \"...\", \"location\": \"成都\", "
                "\"date\": \"YYYY-MM-DD\", \"weather\": \"...\", \"temperature\": \"...\", \"evidence\": \"...\"}。"
                "如果当前模型没有实时联网/搜索能力，或访问失败，返回 "
                "{\"can_access_web\": false, \"reason\": \"...\"}."
            ),
        },
    ]


_CAPABILITY_LABELS = {
    ai_model_config.CAP_CHAT: "对话",
    ai_model_config.CAP_JSON: "JSON 输出",
    ai_model_config.CAP_WEB_SEARCH: "联网搜索",
    ai_model_config.CAP_IMAGE_GENERATE: "图片生成",
    ai_model_config.CAP_IMAGE_EDIT: "图片编辑",
    ai_model_config.CAP_TOOL_CALLING: "Function Call",
}


def _capability_test_outcome(
    capability_probe: dict[str, Any],
    probe_requested: bool,
    connection_ok: bool,
    connection_message: str,
    connection_next_action: str,
) -> dict[str, str | bool]:
    """区分连接性与请求能力的测试结论。"""
    unsupported = capability_probe.get("unsupported") if isinstance(capability_probe, dict) else []
    failed_capabilities = [str(item) for item in unsupported if str(item).strip()]
    if probe_requested and failed_capabilities:
        labels = "、".join(_CAPABILITY_LABELS.get(item, item) for item in failed_capabilities)
        next_action = (
            f"接口连接正常，但请不要启用 {labels}；请检查供应商是否支持对应工具参数、模型权限和联网配置后重试。"
            if connection_ok
            else connection_next_action
        )
        return {
            "ok": False,
            "connection_ok": connection_ok,
            "message": f"{connection_message.rstrip('。')}，但能力测试未通过：{labels}。",
            "next_action": next_action,
        }
    return {
        "ok": connection_ok,
        "connection_ok": connection_ok,
        "message": connection_message,
        "next_action": connection_next_action,
    }


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


def _browser_image_probe_data_from_result(result: browser_ai_runtime.BrowserAiRunResult) -> dict[str, Any]:
    for image_url in result.image_urls:
        if str(image_url or "").strip():
            return {"can_generate_image": True, "image_url": str(image_url).strip()}
    return _cli_image_probe_data_from_text(result.text)


def _validate_browser_image_generate_probe(
    data: dict[str, Any],
    result: browser_ai_runtime.BrowserAiRunResult,
    app_dir: Path | str,
) -> None:
    image_url = str(data.get("image_url") or data.get("url") or "").strip()
    if image_url.startswith(("http://", "https://", "blob:")):
        return
    _validate_cli_image_generate_probe(data, app_dir)


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


def _capability_error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = _http_error_detail(exc)
        return f"{exc.code} {detail or exc.reason}".strip()
    if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
        # 无终态事件的可观测性：超时探测明确标注错误码，而不是只留一句 timed out。
        detail = str(exc).strip()
        suffix = f"（{detail}）" if detail else ""
        return f"probe_timeout: Provider 在 timeout 内未返回终态事件{suffix}"
    return str(exc)


__all__ = [
    "CapabilityProbeContext",
    "CapabilityProbeProvider",
    "SkipCapabilityProbe",
    "run_capability_probes",
    "_capability_error_text",
    "_capability_test_outcome",
    "_chat_probe_default_messages",
    "_cli_image_generate_probe_prompt",
    "_cli_image_probe_data_from_text",
    "_existing_image_path",
    "_json_probe_default_messages",
    "_multipart_body",
    "_normalize_probe_messages",
    "_probe_image_prompt",
    "_probe_messages",
    "_probe_options",
    "_valid_base64_image",
    "_validate_browser_image_generate_probe",
    "_validate_cli_image_generate_probe",
    "_validate_web_search_probe",
    "_validate_web_search_probe_data",
    "_web_search_probe_date_iso",
    "_web_search_probe_prompt",
]
