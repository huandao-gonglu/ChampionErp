"""Configuration helpers for AI models and local runtime settings."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from erp_web.schemas.requests import RequestValidationError
from . import ai_model_config


SENSITIVE_CONFIG_KEYS = {
    "access_key_id",
    "secret_access_key",
    "access_token",
    "alibaba_cookie",
    "api_key",
    "api_token",
    "app_id",
    "app_key",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "code_verifier",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "source_key",
    "token",
}
SENSITIVE_CONFIG_KEY_SUFFIXES = (
    "_api_key",
    "_app_id",
    "_app_key",
    "_cookie",
    "_password",
    "_private_key",
    "_secret",
    "_source_key",
    "_token",
)


def service_status() -> dict[str, str]:
    return {"service": "config", "status": "ready"}


def config_dir(app_dir: Path | str) -> Path:
    path = Path(app_dir) / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_path(app_dir: Path | str) -> Path:
    return config_dir(app_dir) / ".env"


def load_env(app_dir: Path | str) -> None:
    from erp_web.context import peek_context
    context = peek_context()
    if context is not None and not context.allow_environment_credentials:
        return
    load_dotenv(env_path(app_dir), override=False)


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def normalize_config_key(key: Any) -> str:
    """把 camelCase、kebab-case 等字段名归一为 snake_case。"""

    text = str(key or "").strip()
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def is_sensitive_config_key(key: Any) -> bool:
    normalized = normalize_config_key(key)
    if not normalized or normalized.startswith("masked_"):
        return False
    return (
        normalized in SENSITIVE_CONFIG_KEYS
        or normalized.endswith(SENSITIVE_CONFIG_KEY_SUFFIXES)
    )


def mask_nested_config(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {nested_key: mask_nested_config(nested_value, nested_key) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [mask_nested_config(item, key) for item in value]
    if is_sensitive_config_key(key):
        return mask_secret(value)
    return value


def is_masked_secret_placeholder(value: Any) -> bool:
    """识别公共配置响应里的掩码占位，避免把占位符当成真实凭据。"""

    text = str(value or "").strip()
    if not text:
        return False
    if set(text) == {"*"}:
        return True
    return re.fullmatch(r".{1,4}(?:\.\.\.|\*{4}).{0,4}", text) is not None


def resolve_runtime_secret_value(
    saved_value: Any,
    incoming_value: Any,
    key: str,
) -> Any:
    """请求未显式给出真凭据时回落到已保存值，绝不重放公共掩码。"""

    incoming_text = str(incoming_value or "").strip()
    if not incoming_text or is_masked_secret_placeholder(incoming_text):
        return saved_value
    if (
        str(saved_value or "").strip()
        and mask_nested_config(saved_value, key) == incoming_value
    ):
        return saved_value
    return incoming_value


def merge_runtime_secret_section(
    saved: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并请求级覆盖；敏感字段的空值或掩码表示“沿用已保存值”."""

    current = saved if isinstance(saved, dict) else {}
    override = incoming if isinstance(incoming, dict) else {}
    merged = dict(current)
    for key, value in override.items():
        if is_sensitive_config_key(key):
            resolved = resolve_runtime_secret_value(current.get(key), value, key)
            if not str(resolved or "").strip() and is_masked_secret_placeholder(value):
                merged.pop(key, None)
                continue
            merged[key] = resolved
            continue
        merged[key] = value
    return merged


def public_app_config(
    app_dir: Path | str,
    app_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回可下发给本地前端的完整设置视图，所有凭据只保留掩码。"""

    load_env(app_dir)
    safe = json.loads(json.dumps(app_config or {}, ensure_ascii=False))
    public = mask_nested_config(safe)
    public.pop("ai_use_case_prompts", None)
    for model in public.get("ai_models", []):
        if model.get("api_key_env") == ai_model_config.MANAGED_KEY_SOURCE:
            model["system_managed"] = True
            for field in ("base_url", "api_key", "api_key_env"):
                model.pop(field, None)
    public.pop("1688_api", None)
    # 共享汇率 API 与共享图片托管由管理员维护，不进入通用配置视图。
    public.pop("pricing_defaults", None)
    public.pop("image_hosting", None)
    return public


def public_store_config(store_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回店铺静态配置和凭据占位，绝不返回可直接使用的秘密。"""

    safe = json.loads(json.dumps(store_config or {}, ensure_ascii=False))
    public = mask_nested_config(safe)
    # Mercado Libre OAuth 的 App/Client ID 是公开标识符，前端生成授权 URL
    # 必须拿到真值；Secret、Token、PKCE 等字段仍按统一策略脱敏。
    raw_ml = (
        safe.get("mercadolibre")
        if isinstance(safe.get("mercadolibre"), dict)
        else {}
    )
    public_ml = (
        public.get("mercadolibre")
        if isinstance(public.get("mercadolibre"), dict)
        else {}
    )
    for field in ("app_id", "client_id"):
        if field in raw_ml:
            public_ml[field] = raw_ml[field]
    return public


def _merge_masked_secret_section(
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    return merge_runtime_secret_section(current, incoming)


def public_ai_config(app_dir: Path | str, app_config: dict[str, Any] | None = None) -> dict[str, Any]:
    load_env(app_dir)
    cfg = app_config if isinstance(app_config, dict) else {}
    public = ai_model_config.public_ai_config(cfg)
    from .desktop_model_config_service import local_models_view
    from .gemini_canvas_runtime import canvas_accounts_status
    if any(row.get("connection_type") in {"browser", "cli"} for row in public["ai_models"]):
        public['ai_models'] = local_models_view(public['ai_models'])
    public["canvas_status"] = canvas_accounts_status()
    # 1688 应用与买家授权通过独立入口管理，不进入通用配置视图。
    public.pop("1688_api", None)
    public["storage"] = {
        "config_dir": str(config_dir(app_dir)),
        "env_path": str(env_path(app_dir)),
    }
    return public


def merge_ai_config(app_dir: Path | str, current: dict[str, Any], incoming: dict[str, Any], *, preserve_local_models: bool = False) -> dict[str, Any]:
    if "ai_use_case_prompts" in incoming:
        raise RequestValidationError("功能提示词由系统维护，不能通过用户配置修改。请刷新页面。",
                                     status_code=403, error_code="SYSTEM_PROMPT_READ_ONLY")
    merged = {key: value for key, value in (current or {}).items() if key != "ai_use_case_prompts"}
    if isinstance(incoming.get("ai_models"), list):
        from .desktop_model_config_service import prepare_local_model
        current_models = ai_model_config.normalize_ai_models(current.get("ai_models") if isinstance(current, dict) else None)
        current_by_id = {str(model.get("id") or ""): model for model in current_models if str(model.get("id") or "")}
        incoming_models = incoming["ai_models"]
        if not all(isinstance(row, dict) for row in incoming_models):
            raise RequestValidationError("模型配置必须是对象。")
        raw_models = incoming_models if preserve_local_models else [prepare_local_model(model) for model in incoming_models]
        copy_source_by_id = {
            str(model.get("id") or "").strip(): str(model.get("copy_source_id") or "").strip()
            for model in raw_models
            if isinstance(model, dict) and str(model.get("id") or "").strip() and str(model.get("copy_source_id") or "").strip()
        }
        next_models = ai_model_config.normalize_ai_models(raw_models)
        if preserve_local_models:
            # 先规范化 ID 和连接类型；别名、大小写和重复 ID 不能绕过网页的只读限制。
            saved_local = {key: row for key, row in current_by_id.items() if row['connection_type'] != ai_model_config.CONNECTION_TYPE_API}
            incoming_local = {row['id'] for row in next_models if row['connection_type'] != ai_model_config.CONNECTION_TYPE_API}
            local_id_count = sum(row['id'] in saved_local for row in next_models)
            if incoming_local != saved_local.keys() or local_id_count != len(saved_local):
                raise RequestValidationError('本机模型需要在桌面端管理。请刷新后重试。', status_code=409, error_code='OPERATION_UNAVAILABLE')
            next_models = [saved_local.get(row['id'], row) for row in next_models]
        for model in next_models:
            if ai_model_config.model_connection_type(model) in {ai_model_config.CONNECTION_TYPE_CLI, ai_model_config.CONNECTION_TYPE_BROWSER}:
                model["api_key"] = ""
                continue
            model_id = str(model.get("id") or "")
            current_model = current_by_id.get(model_id, {})
            source_model = current_by_id.get(copy_source_by_id.get(model_id, ""), {})
            current_key = str(current_model.get("api_key") or source_model.get("api_key") or "").strip()
            incoming_key = str(model.get("api_key") or "").strip()
            if current_key and (not incoming_key or incoming_key == mask_secret(current_key)):
                model["api_key"] = current_key
        merged["ai_models"] = next_models
    if isinstance(incoming.get("ai_use_case_bindings"), dict):
        merged["ai_use_case_bindings"] = ai_model_config.normalize_ai_use_case_bindings(incoming.get("ai_use_case_bindings"))
    if isinstance(merged.get("ai_models"), list) or isinstance(merged.get("ai_use_case_bindings"), dict):
        merged["ai_use_case_bindings"] = ai_model_config.prune_incompatible_ai_use_case_bindings(
            merged.get("ai_models"),
            merged.get("ai_use_case_bindings"),
        )
    ai_model_config.validate_ai_use_case_generation_bindings(merged)
    ai_model_config.validate_ai_model_request_overrides(merged)
    return merged


def write_env_template(app_dir: Path | str) -> Path:
    path = env_path(app_dir)
    if not path.exists():
        path.write_text(
            "\n".join(
                [
                    "DEEPSEEK_API_KEY=",
                    "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                    "DEEPSEEK_MODEL=deepseek-chat",
                    "OPENAI_API_KEY=",
                    "OPENAI_BASE_URL=https://api.openai.com/v1",
                    "OPENAI_IMAGE_MODEL=gpt-image-1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return path


def save_config_snapshot(app_dir: Path | str, config: dict[str, Any]) -> Path:
    path = config_dir(app_dir) / "ai_config.snapshot.json"
    copied = json.loads(json.dumps(config or {}, ensure_ascii=False))
    safe = mask_nested_config(copied)
    temporary = path.with_name(
        f".{path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(safe, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


__all__ = [
    "config_dir",
    "env_path",
    "is_masked_secret_placeholder",
    "is_sensitive_config_key",
    "load_env",
    "mask_secret",
    "merge_runtime_secret_section",
    "merge_ai_config",
    "normalize_config_key",
    "public_app_config",
    "public_ai_config",
    "public_store_config",
    "save_config_snapshot",
    "service_status",
    "resolve_runtime_secret_value",
    "write_env_template",
]
