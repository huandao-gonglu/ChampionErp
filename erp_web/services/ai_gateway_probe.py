"""AI gateway 能力探测层：探测素材、结果校验与统一探测循环。

原 ai_gateway 里 http/cli/browser 三个同构 for-capability 循环合并为
run_capability_probes；具体能力探测由各 Provider 的 probe_capability 实现。
本模块不依赖 Provider 类，只定义探测协议所需的上下文与工具函数。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic_ai.exceptions import ModelHTTPError

from . import ai_model_config, browser_ai_runtime
from .ai_gateway_parsing import parse_json_text
from .ai_model_errors import AIHTTPError, model_http_error_detail


logger = logging.getLogger(__name__)


PROBE_STATUS_SUPPORTED = "supported"
PROBE_STATUS_UNSUPPORTED = "unsupported"
PROBE_STATUS_UNAVAILABLE = "unavailable"
PROBE_STATUS_INCONCLUSIVE = "inconclusive"
PROBE_STATUSES = (
    PROBE_STATUS_SUPPORTED,
    PROBE_STATUS_UNSUPPORTED,
    PROBE_STATUS_UNAVAILABLE,
    PROBE_STATUS_INCONCLUSIVE,
)


class CapabilityProbeError(RuntimeError):
    """能力探测的可分类终态。"""

    status = PROBE_STATUS_UNSUPPORTED
    code = "CAPABILITY_PROBE_UNSUPPORTED"


class CapabilityProbeUnsupported(CapabilityProbeError):
    """请求已完成，但模型或 Provider 不满足能力契约。"""


class CapabilityProbeUnavailable(CapabilityProbeError):
    """当前 ERP transport 尚未接入该能力。"""

    status = PROBE_STATUS_UNAVAILABLE
    code = "CAPABILITY_PROBE_UNAVAILABLE"


class CapabilityProbeInconclusive(CapabilityProbeError):
    """鉴权、限流、网络或临时 Provider 故障导致无法下结论。"""

    status = PROBE_STATUS_INCONCLUSIVE
    code = "CAPABILITY_PROBE_INCONCLUSIVE"


@dataclass(frozen=True)
class CapabilityProbeContext:
    """一次能力探测所需的全部输入；不同连接类型各取所需字段。"""

    model: dict[str, Any]
    timeout: int
    probe_options: dict[str, Any] | None = None
    app_dir: Path | str | None = None
    api_key: str = ""
    model_name: str = ""
    probe_token: str = ""
    capability: str = ""


class CapabilityProbeProvider(Protocol):
    """Provider 侧统一能力探测契约。"""

    def probe_capability(
        self,
        capability: str,
        context: CapabilityProbeContext,
    ) -> dict[str, Any]:
        """执行单项探测并返回可持久化的能力配方。"""


def empty_capability_probe_report() -> dict[str, Any]:
    return {
        "supported": [],
        "unsupported": [],
        "unavailable": [],
        "inconclusive": [],
        "results": {},
    }


def run_capability_probes(
    provider: CapabilityProbeProvider,
    context: CapabilityProbeContext,
    capabilities: list[str],
) -> dict[str, Any]:
    """统一执行能力探测，并保留 supported/unavailable/inconclusive 语义。"""
    supported: list[str] = []
    unsupported: list[str] = []
    unavailable: list[str] = []
    inconclusive: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    for capability in ai_model_config.normalize_capabilities(capabilities):
        probe_context = replace(
            context,
            probe_token=secrets.token_hex(12),
            capability=capability,
        )
        try:
            capability_profile = provider.probe_capability(
                capability,
                probe_context,
            )
        except Exception as exc:
            status, code = _capability_error_status(exc)
            error_text = _capability_error_text(exc)
            bucket = {
                PROBE_STATUS_UNSUPPORTED: unsupported,
                PROBE_STATUS_UNAVAILABLE: unavailable,
                PROBE_STATUS_INCONCLUSIVE: inconclusive,
            }[status]
            bucket.append(capability)
            results[capability] = {
                "ok": False,
                "status": status,
                "error_code": code,
                "error": error_text,
                "retryable": status == PROBE_STATUS_INCONCLUSIVE,
            }
            if isinstance(exc, (AIHTTPError, ModelHTTPError)):
                logger.warning(
                    "AI 模型能力探测被 Provider 拒绝：model_id=%s "
                    "capability=%s status=%s error_code=%s detail=%s",
                    str(context.model.get("id") or "unknown"),
                    capability,
                    status,
                    code,
                    error_text,
                )
            continue
        supported.append(capability)
        result: dict[str, Any] = {
            "ok": True,
            "status": PROBE_STATUS_SUPPORTED,
            "error": "",
            "retryable": False,
            "capability_profile": capability_profile,
        }
        if capability == ai_model_config.CAP_WEB_SEARCH:
            result["request_mode"] = capability_profile.get("request_mode", "")
            result["api_style"] = capability_profile.get("api_style", "")
        results[capability] = result
    return {
        "supported": supported,
        "unsupported": unsupported,
        "unavailable": unavailable,
        "inconclusive": inconclusive,
        "results": results,
    }


def _capability_error_status(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, CapabilityProbeError):
        return exc.status, exc.code
    if isinstance(exc, AssertionError):
        return PROBE_STATUS_INCONCLUSIVE, "CAPABILITY_PROBE_INTERNAL_ERROR"
    status_code = None
    if isinstance(exc, (AIHTTPError, ModelHTTPError)):
        status_code = int(exc.status_code)
    if status_code is not None:
        if status_code in {401, 403, 404, 408, 409, 425, 429} or status_code >= 500:
            return PROBE_STATUS_INCONCLUSIVE, "CAPABILITY_PROBE_PROVIDER_ERROR"
        return PROBE_STATUS_UNSUPPORTED, "CAPABILITY_PROBE_PROTOCOL_REJECTED"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or isinstance(
        getattr(exc, "reason", None), TimeoutError
    ):
        return PROBE_STATUS_INCONCLUSIVE, "CAPABILITY_PROBE_TRANSPORT_ERROR"
    error_text = str(exc).lower()
    if "未找到本地 cli" in error_text:
        return PROBE_STATUS_UNAVAILABLE, "CAPABILITY_PROBE_TRANSPORT_UNAVAILABLE"
    if any(
        marker in error_text
        for marker in (
            "超时",
            "timed out",
            "调用失败",
            "未连接浏览器",
            "没有找到可输入",
            "请先在打开的页面中完成登录",
        )
    ):
        return PROBE_STATUS_INCONCLUSIVE, "CAPABILITY_PROBE_TRANSPORT_ERROR"
    return PROBE_STATUS_UNSUPPORTED, "CAPABILITY_PROBE_CONTRACT_FAILED"


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


_PROBE_OPERATIONS = {
    ai_model_config.CAP_CHAT: "model.request",
    ai_model_config.CAP_JSON: "model.request.structured",
    ai_model_config.CAP_WEB_SEARCH: "model.request.native_tool",
    ai_model_config.CAP_TOOL_CALLING: "model.request.function_tool",
    ai_model_config.CAP_IMAGE_GENERATE: "model.request.image_generate",
    ai_model_config.CAP_IMAGE_EDIT: "model.request.image_edit",
}

_PROBE_VERSIONS = {
    ai_model_config.CAP_JSON: 3,
}


def capability_configuration_fingerprint(model: dict[str, Any]) -> str:
    return ai_model_config.model_configuration_fingerprint(model)


def build_capability_profile(
    model: dict[str, Any],
    capability: str,
    *,
    strategy: str,
    request_mode: str = "",
) -> dict[str, Any]:
    connection_type = ai_model_config.model_connection_type(model)
    if connection_type == ai_model_config.CONNECTION_TYPE_API:
        provider_id = str(model.get("provider_id") or "").strip()
    elif connection_type == ai_model_config.CONNECTION_TYPE_CLI:
        provider_id = ai_model_config.model_cli_tool(model)
    else:
        provider_id = str(model.get("browser_provider") or "browser").strip()
    profile: dict[str, Any] = {
        "version": 2,
        "tested": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "probe_version": f"{capability}.v{_PROBE_VERSIONS.get(capability, 2)}",
        "configuration_fingerprint": capability_configuration_fingerprint(model),
        "connection_type": connection_type,
        "provider_id": provider_id,
        "model": ai_model_config.model_name(model),
        "strategy": strategy,
        "operation": _PROBE_OPERATIONS.get(capability, "model.request"),
    }
    if connection_type == ai_model_config.CONNECTION_TYPE_API:
        profile["api_style"] = ai_model_config.normalize_api_style(
            model.get("api_style")
        )
    if request_mode:
        profile["request_mode"] = request_mode
    return profile


def _chat_probe_default_messages(probe_token: str = "") -> list[dict[str, str]]:
    expected = probe_token or "ok"
    return [
        {"role": "system", "content": "只返回用户给出的探测标记，不要添加其它内容。"},
        {"role": "user", "content": expected},
    ]


def _json_probe_default_messages(probe_token: str = "") -> list[dict[str, str]]:
    challenge = _json_probe_challenge_payload(probe_token)
    return [
        {
            "role": "system",
            "content": (
                "你正在执行 JSON 结构能力探测。只返回一个合法 JSON 对象，"
                "不要使用 Markdown 代码块，也不要添加解释。读取用户提供的 JSON，"
                "严格按以下顺序处理 numbers：先移除原数组中的偶数，再把剩余数字"
                "乘以 rules.multiply，最后按 rules.sort 指定的 ascending 顺序排序。"
                "输出对象必须且只能包含 probe_token 和 result 两个字段："
                "probe_token 必须原样返回；result 必须是处理后的 JSON 数字数组。"
                "不要回显 numbers 或 rules，也不要返回 ok 字段。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(challenge, ensure_ascii=False),
        },
    ]


def _validate_chat_probe_text(text: str, probe_token: str) -> None:
    if str(text or "").strip() != probe_token:
        raise CapabilityProbeUnsupported("模型没有准确返回本次对话探测标记。")


def _json_probe_challenge_payload(probe_token: str = "") -> dict[str, Any]:
    """生成包含奇偶混合数组的确定性挑战，便于本地独立复算。"""

    token = probe_token or "ok"
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    odd_numbers = [2 * (digest[index] % 5) + 1 for index in range(3)]
    even_numbers = [2 * (digest[index] % 4 + 1) for index in range(3, 5)]
    numbers = [
        odd_numbers[0],
        even_numbers[0],
        odd_numbers[1],
        even_numbers[1],
        odd_numbers[2],
    ]
    rotation = digest[5] % len(numbers)
    numbers = numbers[rotation:] + numbers[:rotation]
    return {
        "numbers": numbers,
        "rules": {
            "sort": "ascending",
            "remove_even": True,
            "multiply": 2 + digest[6] % 4,
        },
        "probe_token": token,
    }


def _json_probe_expected_data(probe_token: str = "") -> dict[str, Any]:
    challenge = _json_probe_challenge_payload(probe_token)
    multiplier = challenge["rules"]["multiply"]
    result = sorted(
        number * multiplier
        for number in challenge["numbers"]
        if number % 2 != 0
    )
    return {
        "probe_token": challenge["probe_token"],
        "result": result,
    }


def _validate_json_probe_data(data: dict[str, Any], probe_token: str) -> None:
    expected = _json_probe_expected_data(probe_token)
    if (
        type(data) is not dict
        or set(data) != {"probe_token", "result"}
        or type(data.get("probe_token")) is not str
        or data.get("probe_token") != expected["probe_token"]
        or type(data.get("result")) is not list
        or any(type(item) is not int for item in data.get("result", []))
        or data.get("result") != expected["result"]
    ):
        raise CapabilityProbeUnsupported(
            "模型未正确执行 JSON 数组变换探测。"
        )


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
    results = (
        capability_probe.get("results")
        if isinstance(capability_probe, dict)
        else {}
    )
    result_map = results if isinstance(results, dict) else {}
    failed_capabilities = [
        str(capability)
        for capability, result in result_map.items()
        if isinstance(result, dict)
        and result.get("status") != PROBE_STATUS_SUPPORTED
    ]
    if probe_requested and failed_capabilities:
        labels = "、".join(_CAPABILITY_LABELS.get(item, item) for item in failed_capabilities)
        has_inconclusive = any(
            isinstance(result_map.get(item), dict)
            and result_map[item].get("status") == PROBE_STATUS_INCONCLUSIVE
            for item in failed_capabilities
        )
        has_unavailable = any(
            isinstance(result_map.get(item), dict)
            and result_map[item].get("status") == PROBE_STATUS_UNAVAILABLE
            for item in failed_capabilities
        )
        if not connection_ok:
            next_action = connection_next_action
        elif has_inconclusive:
            next_action = f"{labels} 的探测没有形成确定结论，请检查鉴权、限流或网络状态后重试。"
        elif has_unavailable:
            next_action = f"当前连接方式尚未接入 {labels}，请更换连接方式或等待对应 transport 支持。"
        else:
            next_action = f"接口连接正常，但请不要启用 {labels}；当前模型未满足对应能力契约。"
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


def _capability_error_text(exc: Exception) -> str:
    if isinstance(exc, CapabilityProbeError):
        return str(exc)
    if isinstance(exc, AIHTTPError):
        return f"HTTP {exc.status_code}: {exc.detail or exc.reason}".strip()
    if isinstance(exc, ModelHTTPError):
        detail = model_http_error_detail(exc)
        return f"HTTP {exc.status_code}: {detail}".strip()
    if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
        # 无终态事件的可观测性：超时探测明确标注错误码，而不是只留一句 timed out。
        detail = str(exc).strip()
        suffix = f"（{detail}）" if detail else ""
        return f"probe_timeout: Provider 在 timeout 内未返回终态事件{suffix}"
    return str(exc)


__all__ = [
    "CapabilityProbeError",
    "CapabilityProbeContext",
    "CapabilityProbeInconclusive",
    "CapabilityProbeProvider",
    "CapabilityProbeUnavailable",
    "CapabilityProbeUnsupported",
    "PROBE_STATUS_INCONCLUSIVE",
    "PROBE_STATUS_SUPPORTED",
    "PROBE_STATUS_UNAVAILABLE",
    "PROBE_STATUS_UNSUPPORTED",
    "build_capability_profile",
    "capability_configuration_fingerprint",
    "empty_capability_probe_report",
    "run_capability_probes",
    "_capability_error_text",
    "_capability_test_outcome",
    "_chat_probe_default_messages",
    "_cli_image_generate_probe_prompt",
    "_cli_image_probe_data_from_text",
    "_existing_image_path",
    "_json_probe_challenge_payload",
    "_json_probe_default_messages",
    "_json_probe_expected_data",
    "_normalize_probe_messages",
    "_probe_image_prompt",
    "_probe_messages",
    "_probe_options",
    "_valid_base64_image",
    "_validate_browser_image_generate_probe",
    "_validate_chat_probe_text",
    "_validate_cli_image_generate_probe",
    "_validate_json_probe_data",
    "_validate_web_search_probe_data",
    "_web_search_probe_date_iso",
    "_web_search_probe_prompt",
]
