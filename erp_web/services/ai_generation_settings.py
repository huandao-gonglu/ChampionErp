"""功能绑定的统一生成配置与 HTTP Provider 参数映射。"""

from __future__ import annotations

import math
from typing import Any

from .ai_provider_catalog import (
    PROVIDER_FAMILY_ALIBABA,
    PROVIDER_FAMILY_GENERIC_OPENAI,
    PROVIDER_FAMILY_OPENAI,
    provider_family_for_model,
)

API_STYLE_OPENAI_COMPATIBLE = "openai_compatible"
API_STYLE_OPENAI_RESPONSES = "openai_responses"
CONNECTION_TYPE_API = "api"

FORBIDDEN_PYDANTIC_PROTOCOL_FIELDS = frozenset(
    {
        "input",
        "instructions",
        "messages",
        "model",
        "parallel_tool_calls",
        "response_format",
        "stream",
        "text",
        "tool_choice",
        "tools",
    }
)

REASONING_MODE_DISABLED = "disabled"
REASONING_MODE_ENABLED = "enabled"
REASONING_MODES = (REASONING_MODE_DISABLED, REASONING_MODE_ENABLED)
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")

def _optional_float(value: Any, *, field: str, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字。") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field} 必须在 {minimum:g} 到 {maximum:g} 之间。")
    return number


def _optional_positive_int(value: Any, *, field: str, maximum: int = 1_000_000) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数。")
    text = str(value).strip()
    try:
        number = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数。") from exc
    if str(number) != text and not (isinstance(value, int) and not isinstance(value, bool)):
        raise ValueError(f"{field} 必须是正整数。")
    if number <= 0 or number > maximum:
        raise ValueError(f"{field} 必须在 1 到 {maximum} 之间。")
    return number


def normalize_generation_settings(value: Any) -> dict[str, Any]:
    """归一化 provider-neutral 生成配置；空字段表示继承调用方默认值。"""

    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("AI 功能绑定 generation 必须是 JSON object。")
    unknown = sorted(set(value) - {"temperature", "max_output_tokens", "reasoning"})
    if unknown:
        raise ValueError("AI 功能绑定 generation 含有未知字段：" + ", ".join(unknown))

    normalized: dict[str, Any] = {}
    temperature = _optional_float(
        value.get("temperature"),
        field="AI 功能绑定 temperature",
        minimum=0,
        maximum=2,
    )
    if temperature is not None:
        normalized["temperature"] = temperature
    max_output_tokens = _optional_positive_int(
        value.get("max_output_tokens"),
        field="AI 功能绑定 max_output_tokens",
    )
    if max_output_tokens is not None:
        normalized["max_output_tokens"] = max_output_tokens

    raw_reasoning = value.get("reasoning")
    if raw_reasoning not in (None, "", {}):
        if not isinstance(raw_reasoning, dict):
            raise ValueError("AI 功能绑定 reasoning 必须是 JSON object。")
        unknown_reasoning = sorted(
            set(raw_reasoning) - {"mode", "effort", "budget_tokens"}
        )
        if unknown_reasoning:
            raise ValueError(
                "AI 功能绑定 reasoning 含有未知字段："
                + ", ".join(unknown_reasoning)
            )
        mode = str(raw_reasoning.get("mode") or "").strip().lower()
        effort = str(raw_reasoning.get("effort") or "").strip().lower()
        budget_tokens = _optional_positive_int(
            raw_reasoning.get("budget_tokens"),
            field="AI 功能绑定 reasoning.budget_tokens",
        )
        if mode and mode not in REASONING_MODES:
            raise ValueError("AI 功能绑定 reasoning.mode 只支持 disabled 或 enabled。")
        if effort and effort not in REASONING_EFFORTS:
            raise ValueError(
                "AI 功能绑定 reasoning.effort 只支持 "
                + ", ".join(REASONING_EFFORTS)
                + "。"
            )
        if (effort or budget_tokens is not None) and not mode:
            mode = REASONING_MODE_ENABLED
        if mode == REASONING_MODE_DISABLED and (effort or budget_tokens is not None):
            raise ValueError("关闭推理时不能同时设置推理强度或推理预算。")
        reasoning: dict[str, Any] = {}
        if mode:
            reasoning["mode"] = mode
        if effort:
            reasoning["effort"] = effort
        if budget_tokens is not None:
            reasoning["budget_tokens"] = budget_tokens
        if reasoning:
            normalized["reasoning"] = reasoning
    return normalized


def generation_capabilities(model: dict[str, Any]) -> dict[str, Any]:
    """描述当前连接能够确定映射的统一生成字段。"""

    connection_type = str(model.get("connection_type") or "api").strip().lower()
    api_style = str(model.get("api_style") or API_STYLE_OPENAI_COMPATIBLE).strip().lower()
    family = provider_family_for_model(model)
    provider_id = str(model.get("provider_id") or "").strip().lower()
    if connection_type != CONNECTION_TYPE_API:
        return {
            "status": "unsupported",
            "provider_id": provider_id,
            "temperature": {"status": "unsupported"},
            "max_output_tokens": {"status": "unsupported"},
            "reasoning": {
                "status": "unsupported",
                "modes": [],
                "efforts": [],
                "supports_budget_tokens": False,
                "note": "当前 CLI / 浏览器适配器没有可验证的统一生成参数通道。",
            },
        }

    base: dict[str, Any] = {
        "status": "supported",
        "provider_id": provider_id,
        "api_style": api_style,
        "temperature": {"status": "supported", "minimum": 0, "maximum": 2},
        "max_output_tokens": {"status": "supported", "minimum": 1},
    }
    if family == PROVIDER_FAMILY_GENERIC_OPENAI:
        base["reasoning"] = {
            "status": "unknown",
            "modes": [],
            "efforts": [],
            "supports_budget_tokens": False,
            "note": "通用兼容接口没有统一的推理字段；请选择准确厂商后才能配置。",
        }
        return base
    if family == PROVIDER_FAMILY_OPENAI:
        base["reasoning"] = {
            "status": "supported",
            "modes": list(REASONING_MODES),
            "efforts": ["minimal", "low", "medium", "high", "xhigh"],
            "supports_budget_tokens": False,
            "note": "参数会按当前 API 协议转换；具体模型是否接受该强度仍由 OpenAI 校验。",
        }
        return base
    if api_style == API_STYLE_OPENAI_RESPONSES:
        base["reasoning"] = {
            "status": "supported",
            "modes": list(REASONING_MODES),
            "efforts": list(REASONING_EFFORTS),
            "supports_budget_tokens": False,
            "note": "Responses 使用 reasoning.effort；关闭推理转换为 effort=none。",
        }
    else:
        base["reasoning"] = {
            "status": "supported",
            "modes": list(REASONING_MODES),
            "efforts": [],
            "supports_budget_tokens": True,
            "note": "Chat Completions 使用 enable_thinking 和 thinking_budget。",
        }
    return base


def _validate_reasoning_mapping(
    model: dict[str, Any], reasoning: dict[str, Any]
) -> tuple[str, str]:
    family = provider_family_for_model(model)
    api_style = str(model.get("api_style") or API_STYLE_OPENAI_COMPATIBLE).strip().lower()
    if family == PROVIDER_FAMILY_GENERIC_OPENAI:
        raise ValueError(
            "当前服务商尚未定义统一推理参数映射，无法安全转换推理参数。"
        )
    effort = str(reasoning.get("effort") or "").strip().lower()
    budget_tokens = reasoning.get("budget_tokens")
    if family == PROVIDER_FAMILY_OPENAI:
        if budget_tokens is not None:
            raise ValueError("OpenAI 映射不支持统一的 reasoning.budget_tokens。")
        if effort == "max":
            raise ValueError("OpenAI 映射不支持 max 推理强度。")
    elif api_style == API_STYLE_OPENAI_RESPONSES:
        if budget_tokens is not None:
            raise ValueError("阿里云 Responses 映射不支持 reasoning.budget_tokens。")
    elif effort:
        raise ValueError("阿里云 Chat Completions 映射不支持 reasoning.effort，请使用推理预算。")
    return family, api_style


def validate_generation_settings_for_model(
    model: dict[str, Any], settings: dict[str, Any] | None
) -> dict[str, Any]:
    """在保存和执行边界验证统一配置能够被选中模型明确转换。"""

    normalized = normalize_generation_settings(settings)
    if not normalized:
        return {}
    if str(model.get("connection_type") or "api").strip().lower() != CONNECTION_TYPE_API:
        raise ValueError("当前连接类型不支持功能绑定生成参数。")
    reasoning = normalized.get("reasoning")
    if isinstance(reasoning, dict):
        _validate_reasoning_mapping(model, reasoning)
    return normalized


def pydantic_model_settings_payload(
    model: dict[str, Any],
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """把产品生成配置转换为 Pydantic AI 的公开 ModelSettings shape。

    本函数保持依赖无关，只返回普通字典；Pydantic 类型的构造仍由
    ``ai_model_factory`` 唯一负责。模型级 ``extra.request_body`` 中已被统一
    generation 接管的字段会被移除，确保功能绑定配置拥有最终优先级。
    """

    normalized = validate_generation_settings_for_model(model, settings)
    extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
    if "provider_max_retries" in extra:
        raise ValueError(
            "extra.provider_max_retries 已退役；Provider client 生命周期和重试策略"
            "由 Pydantic AI 统一持有。"
        )
    request_body = extra.get("request_body")
    extra_body = dict(request_body) if isinstance(request_body, dict) else {}
    forbidden = sorted(FORBIDDEN_PYDANTIC_PROTOCOL_FIELDS & extra_body.keys())
    if forbidden:
        raise ValueError(
            "模型级 extra.request_body 不得覆盖 Pydantic 请求协议字段："
            + ", ".join(forbidden)
        )
    payload: dict[str, Any] = {}

    if "temperature" in normalized:
        payload["temperature"] = normalized["temperature"]
        extra_body.pop("temperature", None)
    if "max_output_tokens" in normalized:
        payload["max_tokens"] = normalized["max_output_tokens"]
        for alias in {"max_tokens", "max_completion_tokens", "max_output_tokens"}:
            extra_body.pop(alias, None)

    reasoning = normalized.get("reasoning")
    if isinstance(reasoning, dict):
        family, api_style = _validate_reasoning_mapping(model, reasoning)
        mode = str(reasoning.get("mode") or REASONING_MODE_ENABLED)
        effort = str(reasoning.get("effort") or "medium")
        budget_tokens = reasoning.get("budget_tokens")
        for field in (
            "reasoning",
            "reasoning_effort",
            "enable_thinking",
            "thinking_budget",
        ):
            extra_body.pop(field, None)
        if family == PROVIDER_FAMILY_ALIBABA:
            if api_style == API_STYLE_OPENAI_COMPATIBLE:
                extra_body["enable_thinking"] = mode == REASONING_MODE_ENABLED
                if mode == REASONING_MODE_ENABLED and budget_tokens is not None:
                    extra_body["thinking_budget"] = budget_tokens
            else:
                extra_body["reasoning"] = {
                    "effort": (
                        "none"
                        if mode == REASONING_MODE_DISABLED
                        else effort
                    )
                }
        elif mode == REASONING_MODE_DISABLED:
            payload["thinking"] = False
        elif reasoning.get("effort"):
            payload["thinking"] = effort
        else:
            payload["thinking"] = True

    if extra_body:
        payload["extra_body"] = extra_body
    return payload


def pydantic_openai_chat_profile_payload(
    provider_family: str,
) -> dict[str, Any]:
    """返回 Chat Model 的公开 profile 覆盖，同时集中厂商 wire 字段语义。"""

    family = str(provider_family or "").strip().lower()
    if family == PROVIDER_FAMILY_OPENAI:
        return {}
    return {"openai_chat_supports_max_completion_tokens": False}


def pydantic_openai_responses_profile_payload(
    provider_family: str,
    model_name: str,
) -> dict[str, Any]:
    """返回 Responses Model 的厂商能力覆盖。"""

    family = str(provider_family or "").strip().lower()
    normalized_model = str(model_name or "").strip().lower()
    if family == PROVIDER_FAMILY_ALIBABA and normalized_model.startswith("qwen"):
        # 百炼 Qwen 思考模式只接受 tool_choice=auto/none。声明不支持强制
        # tool choice 后，Pydantic AI 会把严格工具输出所需的 required 降级为 auto。
        return {"openai_supports_tool_choice_required": False}
    return {}


__all__ = [
    "FORBIDDEN_PYDANTIC_PROTOCOL_FIELDS",
    "generation_capabilities",
    "normalize_generation_settings",
    "pydantic_model_settings_payload",
    "pydantic_openai_chat_profile_payload",
    "pydantic_openai_responses_profile_payload",
    "validate_generation_settings_for_model",
]
