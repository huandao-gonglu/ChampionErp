"""Model-level AI configuration and use-case registry."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from erp_shared.desktop import LOCAL_MODEL_FIELDS, local_configuration_fingerprint
from erp_shared.browser_ai import (
    DEFAULT_GEMINI_CANVAS_URL,
    canvas_connection_defaults,
    is_canvas_model,
    normalize_canvas_url,
)
from . import ai_generation_settings, ai_provider_catalog
from erp_shared.browser_connection_config import normalize_browser_proxy
from erp_web.schemas.config import AiBrowserPreset

CANVAS_FIXED_MODEL_ID = "gemini_canvas_browser"

CONNECTION_TYPE_API = "api"
CONNECTION_TYPE_CLI = "cli"
CONNECTION_TYPE_BROWSER = "browser"
AI_CONNECTION_TYPES = (CONNECTION_TYPE_API, CONNECTION_TYPE_CLI, CONNECTION_TYPE_BROWSER)

CLI_TOOL_CODEX = "codex"
CLI_TOOL_CLAUDE = "claude"
CLI_TOOL_GEMINI = "gemini"
CLI_TOOL_GLM = "glm"
CLI_TOOL_CUSTOM = "custom"
AI_CLI_TOOL_DEFAULT_COMMANDS = {
    CLI_TOOL_CODEX: "codex",
    CLI_TOOL_CLAUDE: "claude",
    CLI_TOOL_GEMINI: "gemini",
    CLI_TOOL_GLM: "glm",
    CLI_TOOL_CUSTOM: "",
}
AI_CLI_TOOL_LABELS = {
    CLI_TOOL_CODEX: "Codex CLI",
    CLI_TOOL_CLAUDE: "Claude CLI",
    CLI_TOOL_GEMINI: "Gemini CLI",
    CLI_TOOL_GLM: "GLM CLI",
    CLI_TOOL_CUSTOM: "自定义 CLI",
}
AI_CLI_TOOLS = tuple(AI_CLI_TOOL_DEFAULT_COMMANDS)
CLI_DEFAULT_SANDBOX = "read-only"
BROWSER_MODE_MANAGED_PROFILE = "managed_profile"
BROWSER_MODE_EXISTING_BROWSER = "existing_browser"
AI_BROWSER_MODES = (BROWSER_MODE_MANAGED_PROFILE, BROWSER_MODE_EXISTING_BROWSER)

CAP_CHAT = "chat"
CAP_JSON = "json"
CAP_WEB_SEARCH = "web_search"
CAP_IMAGE_UNDERSTANDING = "image_understanding"
CAP_IMAGE_GENERATE = "image_generate"
CAP_IMAGE_EDIT = "image_edit"
CAP_TOOL_CALLING = "tool_calling"

API_STYLE_OPENAI_COMPATIBLE = "openai_compatible"
API_STYLE_OPENAI_RESPONSES = "openai_responses"
API_STYLE_ANTHROPIC_MESSAGES = "anthropic_messages"
API_STYLE_GOOGLE_GEMINI = "google_gemini"
AI_API_STYLES = (
    API_STYLE_OPENAI_COMPATIBLE,
    API_STYLE_OPENAI_RESPONSES,
    API_STYLE_ANTHROPIC_MESSAGES,
    API_STYLE_GOOGLE_GEMINI,
)
AI_IMAGE_QUALITY_OPTIONS = ("auto", "low", "medium", "high")

AI_MODEL_CAPABILITIES = (
    CAP_CHAT,
    CAP_JSON,
    CAP_WEB_SEARCH,
    CAP_IMAGE_UNDERSTANDING,
    CAP_IMAGE_GENERATE,
    CAP_IMAGE_EDIT,
    CAP_TOOL_CALLING,
)
AI_CAPABILITY_LABELS = {
    CAP_CHAT: "文本",
    CAP_JSON: "JSON 模式",
    CAP_WEB_SEARCH: "联网搜索",
    CAP_IMAGE_UNDERSTANDING: "图片理解",
    CAP_IMAGE_GENERATE: "图像生成",
    CAP_IMAGE_EDIT: "图像编辑",
    CAP_TOOL_CALLING: "Function Call",
}
AI_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 ChampionERP/1.0"
)

AI_USE_CASES: dict[str, dict[str, Any]] = {
    "global.chat": {
        "id": "global.chat",
        "label": "全局 AI",
        "required_capabilities": [CAP_CHAT, CAP_TOOL_CALLING],
        "toolset_id": "global.chat",
        "budget_profile": "global.chat.default",
        "result_schema": "global_chat.v1",
    },
    "copy.generate": {
        "id": "copy.generate",
        "label": "商品 AI 文案",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
    "copy.preview": {
        "id": "copy.preview",
        "label": "文案预览精修",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
        "global_binding": False,
    },
    "image.translate": {
        "id": "image.translate",
        "desktop_capability": CAP_IMAGE_EDIT,
        "label": "图片翻译/重绘",
        "required_capabilities": [CAP_IMAGE_EDIT],
    },
    "image.plan": {
        "id": "image.plan",
        "label": "套图规划（需支持识图）",
        "required_capabilities": [CAP_CHAT, CAP_JSON, CAP_IMAGE_UNDERSTANDING],
    },
    "category.product_match": {
        "id": "category.product_match",
        "label": "商品类目候选匹配",
        "required_capabilities": [CAP_CHAT, CAP_JSON, CAP_TOOL_CALLING],
        "toolset_id": "category.search",
        "budget_profile": "category.match.default",
        "result_schema": "category_match.v2",
    },
    "text.translate": {
        "id": "text.translate",
        "desktop_capability": "chat_json",
        "label": "翻译",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
}


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def normalize_capabilities(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        raw_items = [str(item or "").strip() for item in value]
    else:
        raw_items = []
    seen: set[str] = set()
    result: list[str] = []
    allowed = set(AI_MODEL_CAPABILITIES)
    for item in raw_items:
        key = item.lower()
        if key in allowed and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def normalize_capability_profiles(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize tested model capability activation recipes.

    Profiles belong to a concrete model connection. Business use cases only
    name capabilities and never carry provider-specific request fields.
    """
    raw_profiles = value if isinstance(value, dict) else {}
    allowed = set(AI_MODEL_CAPABILITIES)
    result: dict[str, dict[str, Any]] = {}
    for raw_capability, raw_profile in raw_profiles.items():
        capability = str(raw_capability or "").strip().lower()
        if capability not in allowed or not isinstance(raw_profile, dict):
            continue
        try:
            version = max(1, int(raw_profile.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        profile: dict[str, Any] = {
            "version": version,
            "tested": bool(raw_profile.get("tested", True)),
        }
        for key in (
            "connection_type",
            "api_style",
            "model",
            "base_url",
            "request_mode",
            "operation",
            "strategy",
            "tested_at",
            "probe_version",
            "configuration_fingerprint",
        ):
            text = str(raw_profile.get(key) or "").strip()
            if text:
                profile[key] = text
        raw_provider_id = str(raw_profile.get("provider_id") or "").strip()
        if raw_provider_id:
            if str(profile.get("connection_type") or "").strip().lower() == CONNECTION_TYPE_API:
                profile["provider_id"] = ai_provider_catalog.normalize_provider_id(
                    raw_provider_id
                )
            else:
                profile["provider_id"] = raw_provider_id
        for key in ("supports_tool_choice_required", "observed_thinking"):
            if isinstance(raw_profile.get(key), bool):
                profile[key] = raw_profile[key]
        result[capability] = profile
    return result


def normalize_connection_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_CONNECTION_TYPES else CONNECTION_TYPE_API


def normalize_cli_tool(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_CLI_TOOLS else CLI_TOOL_CODEX


def default_cli_command(cli_tool: str) -> str:
    return AI_CLI_TOOL_DEFAULT_COMMANDS.get(normalize_cli_tool(cli_tool), "")


def normalize_api_style(
    value: Any,
    *,
    default: str = API_STYLE_OPENAI_COMPATIBLE,
) -> str:
    text = str(value or "").strip().lower()
    if text in AI_API_STYLES:
        return text
    return default if default in AI_API_STYLES else API_STYLE_OPENAI_COMPATIBLE


def normalize_browser_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_BROWSER_MODES else BROWSER_MODE_MANAGED_PROFILE


def model_has_image_capability(model: dict[str, Any]) -> bool:
    capabilities = set(normalize_capabilities(model.get("capabilities")))
    return bool({CAP_IMAGE_GENERATE, CAP_IMAGE_EDIT} & capabilities)


def default_ai_models() -> list[dict[str, Any]]:
    return [
        {
            "id": "default_text",
            "name": "默认文本模型",
            "connection_type": CONNECTION_TYPE_API,
            "provider_id": ai_provider_catalog.PROVIDER_ID_OPENAI,
            "api_style": API_STYLE_OPENAI_COMPATIBLE,
            "base_url": "https://api.deepseek.com",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "api_key": "",
            "api_key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
            "model_env": "DEEPSEEK_MODEL",
            "capabilities": [CAP_CHAT, CAP_JSON],
            "enabled": True,
        },
        {
            "id": "default_image",
            "name": "默认图片模型",
            "connection_type": CONNECTION_TYPE_API,
            "provider_id": ai_provider_catalog.PROVIDER_ID_OPENAI,
            "api_style": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "base_url_env": "OPENAI_BASE_URL",
            "api_key": "",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-image-1",
            "model_env": "OPENAI_IMAGE_MODEL",
            "capabilities": [CAP_IMAGE_GENERATE, CAP_IMAGE_EDIT],
            "quality": "medium",
            "enabled": True,
        },
    ]


MANAGED_KEY_SOURCE = "ERP_ACCOUNT_NEWAPI"


SYSTEM_CONNECTION_FIELDS = (
    "id", "connection_type", "provider_id", "api_style", "base_url", "base_url_env",
    "api_key", "api_key_env", "model", "model_env",
)


class SystemModelError(ValueError):
    """系统模型被移除，或请求试图覆盖受保护的连接配置。"""


def system_model_input(raw: dict, system: dict) -> dict:
    """浏览器只拿到公开字段；实际地址、模型标识与凭据来源始终以服务端为准。"""
    for field in SYSTEM_CONNECTION_FIELDS:
        if field in raw and raw[field] != system[field]:
            raise SystemModelError("系统模型的 ID、接入方式、模型和凭据由服务器管理。")
    return {**raw, **{field: system[field] for field in SYSTEM_CONNECTION_FIELDS}}


def managed_newapi_models(internal_url: str, saved_models=()) -> list[dict[str, Any]]:
    common = {
        "connection_type": CONNECTION_TYPE_API,
        "provider_id": ai_provider_catalog.PROVIDER_ID_OPENAI,
        "api_style": API_STYLE_OPENAI_COMPATIBLE,
        "base_url": internal_url + "/v1",
        "api_key_env": MANAGED_KEY_SOURCE,
    }
    models = normalize_ai_models([
        {**common, "id": "erp_newapi_text", "name": "dsflash", "model": "deepseek-flash",
         "capabilities": [CAP_CHAT, CAP_JSON, CAP_TOOL_CALLING]},
        {**common, "id": "erp_newapi_image", "name": "qwenflash", "model": "qwen-image-3.0",
         "capabilities": [CAP_IMAGE_GENERATE, CAP_IMAGE_EDIT]},
    ])
    # 保留已有同名个人条目的 ID 和功能绑定；系统条目一旦保存就复用其 ID。
    occupied = {m["id"] for m in saved_models if m.get("api_key_env") != MANAGED_KEY_SOURCE}
    for model in models:
        saved = next((m for m in saved_models if m.get("api_key_env") == MANAGED_KEY_SOURCE
                      and m["model"] == model["model"]), None)
        if saved:
            model["id"] = saved["id"]
        else:
            while model["id"] in occupied:
                model["id"] += "_system"
    return models


def fixed_canvas_model(
    saved: dict[str, Any] | None = None,
    *,
    canvas_url: str = "",
) -> dict[str, Any]:
    """内置固定的 Gemini Canvas 修图模型；不可删除，项目地址由管理员统一配置，代理和登录态由桌面端维护。"""
    defaults = canvas_connection_defaults()
    existing = saved if isinstance(saved, dict) else {}
    effective_canvas_url = (
        normalize_canvas_url(canvas_url)
        if str(canvas_url or "").strip()
        else DEFAULT_GEMINI_CANVAS_URL
    )
    model_id = str(existing.get("id") or CANVAS_FIXED_MODEL_ID).strip() or CANVAS_FIXED_MODEL_ID
    local_id = str(existing.get("local_connection_id") or model_id).strip() or model_id
    fingerprint = str(existing.get("local_config_fingerprint") or "").strip() or local_configuration_fingerprint(defaults)
    row: dict[str, Any] = {
        "id": model_id,
        "name": str(existing.get("name") or "Gemini Canvas 修图").strip() or "Gemini Canvas 修图",
        "connection_type": CONNECTION_TYPE_BROWSER,
        "provider": "Gemini Canvas",
        "api_style": API_STYLE_OPENAI_COMPATIBLE,
        "base_url": "",
        "base_url_env": "",
        "api_key": "",
        "api_key_env": "",
        "model": str(existing.get("model") or "").strip(),
        "model_env": "",
        "browser_provider": "gemini",
        "local_connection_id": local_id,
        "local_config_fingerprint": fingerprint,
        "capabilities": [CAP_IMAGE_EDIT],
        "timeout_seconds": existing.get("timeout_seconds") or 180,
        "enabled": existing.get("enabled", True) is not False,
        "canvas_managed": True,
        "canvas_url": effective_canvas_url,
    }
    for local_field in ("browser_mode", "browser_profile", "browser_port", "browser_url", "browser_proxy"):
        if local_field in existing:
            row[local_field] = existing[local_field]
    if existing.get("capability_profiles"):
        row["capability_profiles"] = normalize_capability_profiles(existing.get("capability_profiles"))
    return row


def ensure_fixed_canvas_model(
    models: list[dict[str, Any]],
    *,
    canvas_url: str = "",
) -> list[dict[str, Any]]:
    """确保模型列表中始终存在内置固定的 Gemini Canvas 条目，并平滑承接历史配置。"""
    rows = [dict(row) for row in (models or []) if isinstance(row, dict)]
    index = next(
        (idx for idx, row in enumerate(rows) if str(row.get("id") or "") == CANVAS_FIXED_MODEL_ID),
        None,
    )
    if index is None:
        index = next((idx for idx, row in enumerate(rows) if is_canvas_model(row)), None)
    if index is not None:
        rows[index] = fixed_canvas_model(rows[index], canvas_url=canvas_url)
        return rows
    rows.append(fixed_canvas_model(canvas_url=canvas_url))
    return rows


def browser_model_presets(*, canvas_url: str = "") -> list[AiBrowserPreset]:
    """可重复添加的网页连接模板，不包含用户登录、代理或能力测试结果。"""
    defaults = canvas_connection_defaults()
    if str(canvas_url or "").strip():
        defaults = {**defaults, "browser_url": normalize_canvas_url(canvas_url)}
    return [{
        "id": "gemini_canvas",
        "label": "Gemini Canvas",
        "description": "通过 Gemini 网页修改图片，使用自己的 Google 账号登录。",
        "supported_capabilities": [CAP_IMAGE_EDIT],
        "model": {
            "id": CANVAS_FIXED_MODEL_ID, "name": "Gemini Canvas 修图",
            "connection_type": CONNECTION_TYPE_BROWSER, "provider": "Gemini Canvas",
            "browser_provider": "gemini", **defaults,
            "model": "", "timeout_seconds": 180, "capabilities": [CAP_IMAGE_EDIT], "enabled": True,
            "canvas_managed": True,
        },
    }]


def normalize_ai_model(value: Any, index: int = 0) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    model_id = str(raw.get("id") or f"ai_model_{index + 1}").strip()
    connection_type = normalize_connection_type(raw.get("connection_type"))
    cli_tool = normalize_cli_tool(raw.get("cli_tool") or raw.get("cli_provider") or raw.get("tool"))
    cli_command = str(raw.get("command") or raw.get("cli_command") or raw.get("command_path") or "").strip()
    if connection_type == CONNECTION_TYPE_CLI and not cli_command:
        cli_command = default_cli_command(cli_tool)
    capabilities = normalize_capabilities(raw.get("capabilities"))
    provider_id = ai_provider_catalog.normalize_provider_id(raw.get("provider_id"))
    try:
        provider_spec = ai_provider_catalog.provider_spec(provider_id)
    except ValueError:
        provider_spec = None
    provider = (
        provider_spec.label
        if provider_spec is not None
        else provider_id
    )
    base_url = str(raw.get("base_url") or "").strip()
    base_url_env = str(raw.get("base_url_env") or "").strip()
    api_key = str(raw.get("api_key") or "").strip()
    api_key_env = str(raw.get("api_key_env") or "").strip()
    model_name_value = str(raw.get("model") or "").strip()
    model_env = str(raw.get("model_env") or "").strip()
    if (
        connection_type == CONNECTION_TYPE_API
        and not base_url
        and not base_url_env
        and provider_spec is not None
    ):
        base_url = provider_spec.default_base_url
    if connection_type == CONNECTION_TYPE_CLI:
        provider = str(raw.get("provider") or AI_CLI_TOOL_LABELS.get(cli_tool) or "本地 CLI").strip()
        base_url = ""
        base_url_env = ""
        api_key = ""
        api_key_env = ""
        model_name_value = str(raw.get("model") or "").strip()
        model_env = ""
    if connection_type == CONNECTION_TYPE_BROWSER:
        provider = str(raw.get("provider") or "浏览器 AI").strip()
        base_url = ""
        base_url_env = ""
        api_key = ""
        api_key_env = ""
        model_name_value = str(raw.get("model") or "").strip()
        model_env = ""
    normalized: dict[str, Any] = {
        "id": model_id,
        "name": str(raw.get("name") or model_id).strip(),
        "connection_type": connection_type,
        "provider": provider,
        "api_style": normalize_api_style(
            raw.get("api_style"),
            default=(
                provider_spec.default_api_style
                if provider_spec is not None
                else API_STYLE_OPENAI_COMPATIBLE
            ),
        ),
        "base_url": base_url,
        "base_url_env": base_url_env,
        "api_key": api_key,
        "api_key_env": api_key_env,
        "model": model_name_value,
        "model_env": model_env,
        "capabilities": capabilities,
        "enabled": bool(raw.get("enabled", True)),
    }
    if connection_type == CONNECTION_TYPE_API:
        normalized["provider_id"] = provider_id
        thinking_enabled = raw.get("thinking_enabled")
        if thinking_enabled is not None:
            if not isinstance(thinking_enabled, bool):
                raise ValueError("模型 Thinking 开关必须是布尔值，留空表示使用服务商默认。")
            normalized["thinking_enabled"] = thinking_enabled
            ai_generation_settings.validate_generation_settings_for_model(
                normalized,
                {"reasoning": {"mode": "enabled" if thinking_enabled else "disabled"}},
            )
    capability_profiles = normalize_capability_profiles(raw.get("capability_profiles"))
    if capability_profiles:
        normalized["capability_profiles"] = capability_profiles
    if connection_type == CONNECTION_TYPE_CLI:
        normalized["cli_tool"] = cli_tool
        normalized["command"] = cli_command
        normalized["profile"] = str(raw.get("profile") or raw.get("cli_profile") or "").strip()
        normalized["sandbox"] = str(raw.get("sandbox") or CLI_DEFAULT_SANDBOX).strip() or CLI_DEFAULT_SANDBOX
    if connection_type == CONNECTION_TYPE_BROWSER:
        normalized["browser_provider"] = str(raw.get("browser_provider") or raw.get("browserProvider") or "").strip()
        if not normalized["browser_provider"]:
            normalized["browser_provider"] = "chatgpt"
        normalized["browser_mode"] = normalize_browser_mode(raw.get("browser_mode") or raw.get("browserMode"))
        normalized["browser_profile"] = str(raw.get("browser_profile") or raw.get("browserProfile") or "").strip()
        browser_port = str(raw.get("browser_port") or raw.get("browserPort") or "").strip()
        if browser_port:
            normalized["browser_port"] = browser_port
        browser_url = str(raw.get("browser_url") or raw.get("browserUrl") or "").strip()
        if browser_url:
            normalized["browser_url"] = browser_url
        browser_proxy = normalize_browser_proxy(raw.get("browser_proxy"))
        if browser_proxy:
            normalized["browser_proxy"] = browser_proxy
    if connection_type == CONNECTION_TYPE_BROWSER and raw.get("canvas_managed") is True:
        normalized["canvas_managed"] = True
        normalized["capabilities"] = [CAP_IMAGE_EDIT]
        canvas_url = str(raw.get("canvas_url") or "").strip()
        if canvas_url:
            normalized["canvas_url"] = normalize_canvas_url(canvas_url)
    if connection_type in {CONNECTION_TYPE_CLI, CONNECTION_TYPE_BROWSER} and raw.get('local_connection_id'):
        normalized['local_connection_id'] = str(raw['local_connection_id'])
        normalized['local_config_fingerprint'] = str(raw.get('local_config_fingerprint') or '')
        if not any(key in raw for key in LOCAL_MODEL_FIELDS):
            for key in LOCAL_MODEL_FIELDS:
                normalized.pop(key, None)
    image_capable = model_has_image_capability(normalized)
    for key in ("quality", "size"):
        value_for_key = raw.get(key)
        if image_capable and value_for_key not in (None, ""):
            normalized[key] = value_for_key
    value_for_key = raw.get("timeout_seconds")
    if value_for_key not in (None, ""):
        normalized["timeout_seconds"] = value_for_key
    extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
    if extra:
        normalized["extra"] = extra
    profiles = normalized.get("capability_profiles")
    if isinstance(profiles, dict) and profiles and not normalized.get("canvas_managed"):
        fingerprint = model_configuration_fingerprint(normalized)
        stale_capabilities = {
            capability
            for capability, profile in profiles.items()
            if isinstance(profile, dict)
            and str(profile.get("configuration_fingerprint") or "").strip()
            and profile.get("configuration_fingerprint") != fingerprint
        }
        if stale_capabilities:
            normalized["capability_profiles"] = {
                capability: profile
                for capability, profile in profiles.items()
                if capability not in stale_capabilities
            }
            normalized["capabilities"] = [
                capability
                for capability in normalized["capabilities"]
                if capability not in stale_capabilities
            ]
            if not normalized["capability_profiles"]:
                normalized.pop("capability_profiles")
    return normalized


def normalize_ai_models(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    if not raw_items:
        raw_items = default_ai_models()
    models = [normalize_ai_model(item, index) for index, item in enumerate(raw_items)]
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for index, model in enumerate(models):
        model_id = str(model.get("id") or f"ai_model_{index + 1}").strip()
        if not model_id:
            model_id = f"ai_model_{index + 1}"
        if model_id in seen:
            model_id = f"{model_id}_{index + 1}"
            model["id"] = model_id
        seen.add(model_id)
        unique.append(model)
    return unique


def _normalize_timeout_override_seconds(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("AI 功能绑定超时必须是正整数秒。") from exc
    if seconds <= 0:
        raise ValueError("AI 功能绑定超时必须大于 0 秒。")
    return seconds


def normalize_ai_use_case_bindings(value: Any) -> dict[str, dict[str, Any]]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for use_case_id, item in raw.items():
        use_case = AI_USE_CASES.get(str(use_case_id))
        if not use_case or use_case.get("global_binding") is False:
            continue
        item_dict = item if isinstance(item, dict) else {"model_id": item}
        model_id = str(item_dict.get("model_id") or "").strip()
        timeout_seconds = _normalize_timeout_override_seconds(item_dict.get("timeout_override_seconds"))
        generation = ai_generation_settings.normalize_generation_settings(
            item_dict.get("generation")
        )
        if generation and CAP_CHAT not in normalize_capabilities(
            use_case.get("required_capabilities")
        ):
            raise ValueError(f"AI 功能 {use_case_id} 不是文本生成调用，不能配置 generation。")
        binding: dict[str, Any] = {}
        if model_id:
            binding["model_id"] = model_id
        if timeout_seconds is not None:
            binding["timeout_override_seconds"] = timeout_seconds
        if generation:
            binding["generation"] = generation
        if binding:
            result[str(use_case_id)] = binding
    return result


def ai_use_case_required_capabilities(use_case_id: str) -> list[str]:
    use_case = AI_USE_CASES.get(str(use_case_id), {})
    return normalize_capabilities(use_case.get("required_capabilities"))


def ai_use_case_binding(app_config: dict[str, Any] | None, use_case_id: str) -> dict[str, Any]:
    config = app_config if isinstance(app_config, dict) else {}
    return dict(normalize_ai_use_case_bindings(config.get("ai_use_case_bindings")).get(use_case_id) or {})


def model_api_key(model: dict[str, Any]) -> str:
    from erp_web.context import bound_context
    context = bound_context()
    systems = context.config.managed_models if context and context.model_key else ()
    system = next((row for row in systems if row["id"] == model.get("id")), None)
    if system or model.get("api_key_env") == MANAGED_KEY_SOURCE:
        if not system or not all(model.get(field) == system[field] for field in SYSTEM_CONNECTION_FIELDS):
            raise SystemModelError("系统模型连接与当前账号不匹配。")
        return context.model_key()
    api_key = str(model.get("api_key") or "").strip()
    if api_key:
        return api_key
    env_name = str(model.get("api_key_env") or "").strip()
    return _personal_environment(env_name)


def model_base_url(model: dict[str, Any]) -> str:
    explicit_base_url = str(model.get("base_url") or "").strip()
    if explicit_base_url:
        return explicit_base_url
    env_name = str(model.get("base_url_env") or "").strip()
    return _personal_environment(env_name)


def model_name(model: dict[str, Any]) -> str:
    explicit_model = str(model.get("model") or "").strip()
    if explicit_model:
        return explicit_model
    env_name = str(model.get("model_env") or "").strip()
    return _personal_environment(env_name)


def _personal_environment(env_name: str) -> str:
    from erp_web.context import peek_context
    context = peek_context()
    if env_name in {"NEWAPI_SERVICE_SECRET", "ERP_SERVICE_SECRET"} or (context is not None and not context.allow_environment_credentials):
        return ""
    return os.getenv(env_name, "").strip() if env_name else ""


def model_connection_type(model: dict[str, Any]) -> str:
    return normalize_connection_type(model.get("connection_type"))


def model_cli_tool(model: dict[str, Any]) -> str:
    return normalize_cli_tool(model.get("cli_tool"))


def model_cli_command(model: dict[str, Any]) -> str:
    command = str(model.get("command") or model.get("cli_command") or "").strip()
    return command or default_cli_command(model_cli_tool(model))


def model_configuration_fingerprint(model: dict[str, Any]) -> str:
    """生成不包含认证信息与 capability 声明的稳定连接指纹。"""

    connection_type = model_connection_type(model)
    payload: dict[str, Any] = {
        "connection_type": connection_type,
        "model": model_name(model),
    }
    if connection_type in {CONNECTION_TYPE_CLI, CONNECTION_TYPE_BROWSER} and model.get('local_connection_id'):
        payload.update({key: model.get(key, '') for key in ('local_connection_id', 'local_config_fingerprint', 'cli_tool', 'browser_provider')})
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if connection_type == CONNECTION_TYPE_API:
        if isinstance(model.get("thinking_enabled"), bool):
            payload["thinking_enabled"] = model["thinking_enabled"]
        payload.update(
            {
                "provider_id": str(model.get("provider_id") or "").strip(),
                "api_style": normalize_api_style(model.get("api_style")),
                "base_url": model_base_url(model),
                "extra": model.get("extra")
                if isinstance(model.get("extra"), dict)
                else {},
            }
        )
    elif connection_type == CONNECTION_TYPE_CLI:
        payload.update(
            {
                "cli_tool": model_cli_tool(model),
                "command": model_cli_command(model),
                "profile": str(model.get("profile") or "").strip(),
                "sandbox": str(model.get("sandbox") or "").strip(),
            }
        )
    else:
        payload.update(
            {
                "browser_provider": str(
                    model.get("browser_provider") or ""
                ).strip(),
                "browser_mode": str(model.get("browser_mode") or "").strip(),
                "browser_profile": str(
                    model.get("browser_profile") or ""
                ).strip(),
                "browser_port": str(model.get("browser_port") or "").strip(),
                "browser_url": str(model.get("browser_url") or "").strip(),
            }
        )
        if model.get("browser_proxy"):
            payload["browser_proxy"] = normalize_browser_proxy(model["browser_proxy"])
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def missing_model_capabilities(
    model: dict[str, Any],
    required: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    capabilities = set(normalize_capabilities(model.get("capabilities")))
    missing: list[str] = []
    for item in required:
        if item == CAP_IMAGE_UNDERSTANDING and model_connection_type(model) != CONNECTION_TYPE_API:
            missing.append(item)
        elif item not in capabilities:
            missing.append(item)
    return missing


def model_has_capabilities(model: dict[str, Any], required: list[str] | tuple[str, ...] | set[str]) -> bool:
    return not missing_model_capabilities(model, required)


def _format_capability_names(capabilities: list[str]) -> str:
    return ", ".join(
        f"{AI_CAPABILITY_LABELS[item]}（{item}）"
        if item in AI_CAPABILITY_LABELS
        else item
        for item in capabilities
    )


def resolve_ai_model(
    app_config: dict[str, Any] | None,
    use_case_id: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    model_id: str = "",
) -> dict[str, Any]:
    config = app_config if isinstance(app_config, dict) else {}
    models = normalize_ai_models(config.get("ai_models"))
    bindings = normalize_ai_use_case_bindings(config.get("ai_use_case_bindings"))
    required = list(required_capabilities or ai_use_case_required_capabilities(use_case_id))
    preferred_id = str(model_id or (bindings.get(use_case_id) or {}).get("model_id") or "").strip()
    candidates = [model for model in models if model.get("enabled", True)]
    if preferred_id:
        for model in candidates:
            if str(model.get("id") or "") == preferred_id:
                missing = missing_model_capabilities(model, required) if required else []
                if missing:
                    label = str(model.get("name") or preferred_id).strip()
                    raise RuntimeError(
                        f"AI 模型「{label}」不满足能力要求，缺少：{_format_capability_names(missing)}"
                    )
                return model
        raise RuntimeError(f"AI 模型不存在或未启用: {preferred_id}")
    for model in candidates:
        if model.get("canvas_managed"):
            continue
        if not required or model_has_capabilities(model, required):
            return model
    raise RuntimeError(f"没有可用 AI 模型满足能力要求：{_format_capability_names(required)}")


def validate_ai_use_case_generation_bindings(
    app_config: dict[str, Any] | None,
) -> None:
    """确保每个显式功能覆盖都能被实际绑定模型转换。"""

    config = app_config if isinstance(app_config, dict) else {}
    for use_case_id, binding in normalize_ai_use_case_bindings(
        config.get("ai_use_case_bindings")
    ).items():
        generation = binding.get("generation")
        if not isinstance(generation, dict) or not generation:
            continue
        model = resolve_ai_model(config, use_case_id)
        try:
            ai_generation_settings.validate_generation_settings_for_model(
                model,
                generation,
            )
        except ValueError as exc:
            raise ValueError(f"AI 功能 {use_case_id} 的生成配置无效：{exc}") from exc


def validate_ai_model_request_overrides(
    app_config: dict[str, Any] | None,
) -> None:
    """保存配置时拒绝可绕过 Pydantic message/tool/stream owner 的字段。"""

    config = app_config if isinstance(app_config, dict) else {}
    for model in normalize_ai_models(config.get("ai_models")):
        if model_connection_type(model) != CONNECTION_TYPE_API:
            continue
        try:
            provider_spec = ai_provider_catalog.provider_spec_for_model(model)
        except ValueError as exc:
            raise ValueError(
                f"AI 模型 {model.get('id') or 'unknown'} 的 Provider 无效：{exc}"
            ) from None
        api_style = normalize_api_style(model.get("api_style"))
        if api_style not in provider_spec.supported_api_styles:
            raise ValueError(
                f"AI 模型 {model.get('id') or 'unknown'} 的 Provider "
                f"{provider_spec.label} 不支持 API 协议 {api_style}。"
            )
        if (
            model_has_image_capability(model)
            and "images" not in provider_spec.supported_model_kinds
        ):
            raise ValueError(
                f"AI 模型 {model.get('id') or 'unknown'} 的 Provider "
                f"{provider_spec.label} 未接入 Images Model。"
            )
        explicit_base_url = str(model.get("base_url") or "").strip()
        if (
            explicit_base_url
            and not provider_spec.base_url_editable
            and explicit_base_url.rstrip("/")
            != provider_spec.default_base_url.rstrip("/")
        ):
            raise ValueError(
                f"AI 模型 {model.get('id') or 'unknown'} 的 Provider "
                f"{provider_spec.label} 使用固定 Base URL；"
                "请使用该服务商的官方地址。"
            )
        try:
            ai_generation_settings.pydantic_model_settings_payload(model, None)
        except ValueError as exc:
            raise ValueError(
                f"AI 模型 {model.get('id') or 'unknown'} 的高级请求配置无效：{exc}"
            ) from exc


def public_ai_config(app_config: dict[str, Any] | None) -> dict[str, Any]:
    config = app_config if isinstance(app_config, dict) else {}
    models: list[dict[str, Any]] = []
    normalized = normalize_ai_models(config.get("ai_models"))
    for model in normalized:
        public = dict(model)
        public["generation_capabilities"] = ai_generation_settings.generation_capabilities(model)
        managed = model.get("api_key_env") == MANAGED_KEY_SOURCE
        key = "" if managed else model_api_key(model)
        public["api_key_configured"] = managed or bool(key)
        public["api_key_masked"] = mask_secret(key)
        if managed:
            public["system_managed"] = True
            for field in ("base_url", "base_url_env", "api_key_env", "model_env"):
                public.pop(field, None)
        public.pop("api_key", None)
        models.append(public)
    managed_canvas_url = next(
        (str(row.get("canvas_url") or "").strip() for row in normalized if row.get("canvas_managed") and row.get("canvas_url")),
        "",
    )
    return {
        "ai_models": models,
        "ai_use_case_bindings": normalize_ai_use_case_bindings(config.get("ai_use_case_bindings")),
        "ai_use_cases": list(AI_USE_CASES.values()),
        "connection_types": list(AI_CONNECTION_TYPES),
        "browser_modes": list(AI_BROWSER_MODES),
        "browser_presets": browser_model_presets(canvas_url=managed_canvas_url),
        "capabilities": list(AI_MODEL_CAPABILITIES),
        "api_styles": list(AI_API_STYLES),
        "providers": ai_provider_catalog.public_provider_catalog(),
        "cli_tools": local_cli_tool_status(),
        "image_quality_options": list(AI_IMAGE_QUALITY_OPTIONS),
    }


def local_cli_tool_status() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in AI_CLI_TOOLS:
        command = default_cli_command(tool)
        path = ""
        tools.append(
            {
                "value": tool,
                "label": AI_CLI_TOOL_LABELS.get(tool, tool),
                "command": command,
                "installed": bool(path),
                "path": path or "",
            }
        )
    return tools
