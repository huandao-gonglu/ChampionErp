"""Configuration helpers for AI models and local runtime settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import ai_model_config, ai_prompt_templates


PRODUCT_RESEARCH_SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "source_key",
    "token",
}


def service_status() -> dict[str, str]:
    return {"service": "config", "status": "ready"}


def config_dir(app_dir: Path | str) -> Path:
    path = Path(app_dir) / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_path(app_dir: Path | str) -> Path:
    return config_dir(app_dir) / ".env"


def load_env(app_dir: Path | str) -> None:
    load_dotenv(env_path(app_dir), override=False)


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def mask_nested_config(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {nested_key: mask_nested_config(nested_value, nested_key) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [mask_nested_config(item, key) for item in value]
    if key.lower() in PRODUCT_RESEARCH_SENSITIVE_CONFIG_KEYS:
        return mask_secret(value)
    return value


def public_ai_config(app_dir: Path | str, app_config: dict[str, Any] | None = None) -> dict[str, Any]:
    load_env(app_dir)
    cfg = app_config if isinstance(app_config, dict) else {}
    public = ai_model_config.public_ai_config(cfg)
    public["ai_use_case_prompts"] = ai_prompt_templates.public_ai_use_case_prompts(app_dir, cfg)
    public["storage"] = {
        "config_dir": str(config_dir(app_dir)),
        "env_path": str(env_path(app_dir)),
    }
    return public


def merge_ai_config(app_dir: Path | str, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    if isinstance(incoming.get("ai_models"), list):
        raw_models = incoming.get("ai_models")
        current_models = ai_model_config.normalize_ai_models(current.get("ai_models") if isinstance(current, dict) else None)
        current_by_id = {str(model.get("id") or ""): model for model in current_models if str(model.get("id") or "")}
        copy_source_by_id = {
            str(model.get("id") or "").strip(): str(model.get("copy_source_id") or "").strip()
            for model in raw_models
            if isinstance(model, dict) and str(model.get("id") or "").strip() and str(model.get("copy_source_id") or "").strip()
        }
        next_models = ai_model_config.normalize_ai_models(raw_models)
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
    if isinstance(incoming.get("ai_use_case_prompts"), dict):
        merged["ai_use_case_prompts"] = ai_prompt_templates.merge_ai_use_case_prompts(
            app_dir,
            current if isinstance(current, dict) else {},
            incoming.get("ai_use_case_prompts"),
        )
    if isinstance(incoming.get("pricing_defaults"), dict):
        current_pricing = merged.get("pricing_defaults") if isinstance(merged.get("pricing_defaults"), dict) else {}
        incoming_pricing = incoming.get("pricing_defaults") if isinstance(incoming.get("pricing_defaults"), dict) else {}
        merged["pricing_defaults"] = {**current_pricing, **incoming_pricing}
    if isinstance(incoming.get("1688_api"), dict):
        current_1688_api = merged.get("1688_api") if isinstance(merged.get("1688_api"), dict) else {}
        incoming_1688_api = incoming.get("1688_api") if isinstance(incoming.get("1688_api"), dict) else {}
        merged["1688_api"] = {**current_1688_api, **incoming_1688_api}
    if isinstance(incoming.get("yunexpress"), dict):
        current_yunexpress = merged.get("yunexpress") if isinstance(merged.get("yunexpress"), dict) else {}
        incoming_yunexpress = incoming.get("yunexpress") if isinstance(incoming.get("yunexpress"), dict) else {}
        merged["yunexpress"] = {**current_yunexpress, **incoming_yunexpress}
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
    safe = json.loads(json.dumps(config or {}, ensure_ascii=False))
    if isinstance(safe.get("ai_models"), list):
        for model in safe["ai_models"]:
            if isinstance(model, dict) and model.get("api_key"):
                model["api_key"] = mask_secret(model["api_key"])
    if isinstance(safe.get("1688_api"), dict):
        for key in ("app_key", "app_secret", "access_token"):
            if safe["1688_api"].get(key):
                safe["1688_api"][key] = mask_secret(safe["1688_api"][key])
    if isinstance(safe.get("yunexpress"), dict):
        for key in ("app_id", "app_secret", "source_key"):
            if safe["yunexpress"].get(key):
                safe["yunexpress"][key] = mask_secret(safe["yunexpress"][key])
    product_research = safe.get("product_research")
    if isinstance(product_research, dict):
        sources = []
        if isinstance(product_research.get("source_registry"), list):
            sources.extend(product_research["source_registry"])
        if isinstance(product_research.get("search_providers"), list):
            sources.extend(product_research["search_providers"])
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("config_json"), dict):
                continue
            source["config_json"] = mask_nested_config(source["config_json"])
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = [
    "config_dir",
    "env_path",
    "load_env",
    "mask_secret",
    "merge_ai_config",
    "public_ai_config",
    "save_config_snapshot",
    "service_status",
    "write_env_template",
]
