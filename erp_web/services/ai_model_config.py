"""Model-level AI configuration and use-case registry."""

from __future__ import annotations

import os
import shutil
from typing import Any

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
CAP_IMAGE_GENERATE = "image_generate"
CAP_IMAGE_EDIT = "image_edit"
CAP_TOOL_CALLING = "tool_calling"

API_STYLE_OPENAI_COMPATIBLE = "openai_compatible"
API_STYLE_OPENAI_RESPONSES = "openai_responses"
AI_API_STYLES = (API_STYLE_OPENAI_COMPATIBLE, API_STYLE_OPENAI_RESPONSES)
AI_IMAGE_QUALITY_OPTIONS = ("auto", "low", "medium", "high")
MODEL_QUALITY_FAST = "fast"
MODEL_QUALITY_BALANCED = "balanced"
MODEL_QUALITY_HIGH = "high_quality"
AI_MODEL_QUALITY_LEVELS = (MODEL_QUALITY_FAST, MODEL_QUALITY_BALANCED, MODEL_QUALITY_HIGH)
AI_MODEL_QUALITY_ALIASES = {
    "low": MODEL_QUALITY_FAST,
    "speed": MODEL_QUALITY_FAST,
    "fast": MODEL_QUALITY_FAST,
    "medium": MODEL_QUALITY_BALANCED,
    "normal": MODEL_QUALITY_BALANCED,
    "balanced": MODEL_QUALITY_BALANCED,
    "auto": MODEL_QUALITY_BALANCED,
    "high": MODEL_QUALITY_HIGH,
    "quality": MODEL_QUALITY_HIGH,
    "high_quality": MODEL_QUALITY_HIGH,
}

AI_MODEL_CAPABILITIES = (
    CAP_CHAT,
    CAP_JSON,
    CAP_WEB_SEARCH,
    CAP_IMAGE_GENERATE,
    CAP_IMAGE_EDIT,
    CAP_TOOL_CALLING,
)
AI_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 ChampionERP/1.0"
)

AI_USE_CASES: dict[str, dict[str, Any]] = {
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
        "label": "图片翻译/重绘",
        "required_capabilities": [CAP_IMAGE_EDIT],
    },
    "category.attribute_fill": {
        "id": "category.attribute_fill",
        "label": "类目属性 AI 填充",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
    "category.product_identify": {
        "id": "category.product_identify",
        "label": "商品主体识别与类目检索词",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
    "category.attribute_translation": {
        "id": "category.attribute_translation",
        "label": "类目属性翻译",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
    "category.result_translation": {
        "id": "category.result_translation",
        "label": "候选类目翻译",
        "required_capabilities": [CAP_CHAT, CAP_JSON],
    },
    "research.web_search": {
        "id": "research.web_search",
        "label": "产品调研 AI 联网搜索",
        "required_capabilities": [CAP_CHAT, CAP_JSON, CAP_WEB_SEARCH],
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


def normalize_connection_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_CONNECTION_TYPES else CONNECTION_TYPE_API


def normalize_cli_tool(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_CLI_TOOLS else CLI_TOOL_CODEX


def default_cli_command(cli_tool: str) -> str:
    return AI_CLI_TOOL_DEFAULT_COMMANDS.get(normalize_cli_tool(cli_tool), "")


def normalize_api_style(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_API_STYLES else API_STYLE_OPENAI_COMPATIBLE


def normalize_browser_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AI_BROWSER_MODES else BROWSER_MODE_MANAGED_PROFILE


def normalize_model_quality_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return AI_MODEL_QUALITY_ALIASES.get(text, MODEL_QUALITY_BALANCED)


def model_has_image_capability(model: dict[str, Any]) -> bool:
    capabilities = set(normalize_capabilities(model.get("capabilities")))
    return bool({CAP_IMAGE_GENERATE, CAP_IMAGE_EDIT} & capabilities)


def default_ai_models() -> list[dict[str, Any]]:
    return [
        {
            "id": "default_text",
            "name": "默认文本模型",
            "connection_type": CONNECTION_TYPE_API,
            "provider": "DeepSeek",
            "api_style": "openai_compatible",
            "base_url": "https://api.deepseek.com",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "api_key": "",
            "api_key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
            "model_env": "DEEPSEEK_MODEL",
            "capabilities": [CAP_CHAT, CAP_JSON],
            "quality_level": MODEL_QUALITY_BALANCED,
            "enabled": True,
        },
        {
            "id": "default_image",
            "name": "默认图片模型",
            "connection_type": CONNECTION_TYPE_API,
            "provider": "OpenAI",
            "api_style": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "base_url_env": "OPENAI_BASE_URL",
            "api_key": "",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-image-1",
            "model_env": "OPENAI_IMAGE_MODEL",
            "capabilities": [CAP_IMAGE_GENERATE, CAP_IMAGE_EDIT],
            "quality_level": MODEL_QUALITY_BALANCED,
            "quality": "medium",
            "enabled": True,
        },
    ]


def normalize_ai_model(value: Any, index: int = 0) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    model_id = str(raw.get("id") or f"ai_model_{index + 1}").strip()
    connection_type = normalize_connection_type(raw.get("connection_type"))
    cli_tool = normalize_cli_tool(raw.get("cli_tool") or raw.get("cli_provider") or raw.get("tool"))
    cli_command = str(raw.get("command") or raw.get("cli_command") or raw.get("command_path") or "").strip()
    if connection_type == CONNECTION_TYPE_CLI and not cli_command:
        cli_command = default_cli_command(cli_tool)
    capabilities = normalize_capabilities(raw.get("capabilities"))
    provider = str(raw.get("provider") or "OpenAI-Compatible").strip()
    base_url = str(raw.get("base_url") or "").strip()
    base_url_env = str(raw.get("base_url_env") or "").strip()
    api_key = str(raw.get("api_key") or "").strip()
    api_key_env = str(raw.get("api_key_env") or "").strip()
    model_name_value = str(raw.get("model") or "").strip()
    model_env = str(raw.get("model_env") or "").strip()
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
        "api_style": normalize_api_style(raw.get("api_style")),
        "base_url": base_url,
        "base_url_env": base_url_env,
        "api_key": api_key,
        "api_key_env": api_key_env,
        "model": model_name_value,
        "model_env": model_env,
        "capabilities": capabilities,
        "enabled": bool(raw.get("enabled", True)),
    }
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
    image_capable = model_has_image_capability(normalized)
    quality_level_source = raw.get("quality_level")
    if quality_level_source in (None, "") and not image_capable:
        quality_level_source = raw.get("quality")
    normalized["quality_level"] = normalize_model_quality_level(quality_level_source)
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


def normalize_ai_use_case_bindings(value: Any) -> dict[str, dict[str, str]]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, str]] = {}
    for use_case_id, item in raw.items():
        use_case = AI_USE_CASES.get(str(use_case_id))
        if not use_case or use_case.get("global_binding") is False:
            continue
        item_dict = item if isinstance(item, dict) else {}
        model_id = str(item_dict.get("model_id") or "").strip()
        if model_id:
            result[str(use_case_id)] = {"model_id": model_id}
    return result


def model_api_key(model: dict[str, Any]) -> str:
    api_key = str(model.get("api_key") or "").strip()
    if api_key:
        return api_key
    env_name = str(model.get("api_key_env") or "").strip()
    return os.getenv(env_name, "").strip() if env_name else ""


def model_base_url(model: dict[str, Any]) -> str:
    explicit_base_url = str(model.get("base_url") or "").strip()
    if explicit_base_url:
        return explicit_base_url
    env_name = str(model.get("base_url_env") or "").strip()
    return os.getenv(env_name, "").strip() if env_name else ""


def model_name(model: dict[str, Any]) -> str:
    explicit_model = str(model.get("model") or "").strip()
    if explicit_model:
        return explicit_model
    env_name = str(model.get("model_env") or "").strip()
    return os.getenv(env_name, "").strip() if env_name else ""


def model_connection_type(model: dict[str, Any]) -> str:
    return normalize_connection_type(model.get("connection_type"))


def model_cli_tool(model: dict[str, Any]) -> str:
    return normalize_cli_tool(model.get("cli_tool"))


def model_cli_command(model: dict[str, Any]) -> str:
    command = str(model.get("command") or model.get("cli_command") or "").strip()
    return command or default_cli_command(model_cli_tool(model))


def model_has_capabilities(model: dict[str, Any], required: list[str] | tuple[str, ...] | set[str]) -> bool:
    capabilities = set(normalize_capabilities(model.get("capabilities")))
    return all(item in capabilities for item in required)


def resolve_ai_model(
    app_config: dict[str, Any] | None,
    use_case_id: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    model_id: str = "",
) -> dict[str, Any]:
    config = app_config if isinstance(app_config, dict) else {}
    models = normalize_ai_models(config.get("ai_models"))
    bindings = normalize_ai_use_case_bindings(config.get("ai_use_case_bindings"))
    use_case = AI_USE_CASES.get(use_case_id, {})
    required = list(required_capabilities or use_case.get("required_capabilities") or [])
    preferred_id = str(model_id or (bindings.get(use_case_id) or {}).get("model_id") or "").strip()
    candidates = [model for model in models if model.get("enabled", True)]
    if preferred_id:
        for model in candidates:
            if str(model.get("id") or "") == preferred_id:
                if required and not model_has_capabilities(model, required):
                    raise RuntimeError(f"AI 模型 {preferred_id} 不满足能力要求: {', '.join(required)}")
                return model
        raise RuntimeError(f"AI 模型不存在或未启用: {preferred_id}")
    for model in candidates:
        if not required or model_has_capabilities(model, required):
            return model
    raise RuntimeError(f"没有可用 AI 模型满足能力要求: {', '.join(required)}")


def public_ai_config(app_config: dict[str, Any] | None) -> dict[str, Any]:
    config = app_config if isinstance(app_config, dict) else {}
    models: list[dict[str, Any]] = []
    for model in normalize_ai_models(config.get("ai_models")):
        public = dict(model)
        public["api_key_configured"] = bool(model_api_key(model))
        public["api_key_masked"] = mask_secret(model_api_key(model))
        public.pop("api_key", None)
        models.append(public)
    return {
        "ai_models": models,
        "ai_use_case_bindings": normalize_ai_use_case_bindings(config.get("ai_use_case_bindings")),
        "ai_use_cases": list(AI_USE_CASES.values()),
        "connection_types": list(AI_CONNECTION_TYPES),
        "browser_modes": list(AI_BROWSER_MODES),
        "capabilities": list(AI_MODEL_CAPABILITIES),
        "api_styles": list(AI_API_STYLES),
        "cli_tools": local_cli_tool_status(),
        "image_quality_options": list(AI_IMAGE_QUALITY_OPTIONS),
        "model_quality_levels": list(AI_MODEL_QUALITY_LEVELS),
    }


def local_cli_tool_status() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in AI_CLI_TOOLS:
        command = default_cli_command(tool)
        path = shutil.which(command) if command else ""
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
