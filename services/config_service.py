"""Configuration helpers for AI channels and local runtime settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


TEXT_PROVIDERS = ("DeepSeek", "OpenAI", "OpenAI-Compatible")
IMAGE_PROVIDERS = ("OpenAI", "OpenAI-Compatible")


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


def ai_config_from_sources(app_dir: Path | str, app_config: dict[str, Any] | None = None) -> dict[str, Any]:
    load_env(app_dir)
    cfg = app_config if isinstance(app_config, dict) else {}
    text_ai = cfg.get("text_ai") if isinstance(cfg.get("text_ai"), dict) else {}
    image_ai = cfg.get("image_ai") if isinstance(cfg.get("image_ai"), dict) else {}
    text_provider = str(text_ai.get("platform") or "DeepSeek").strip()
    if text_provider.lower() == "nvidia":
        text_provider = "DeepSeek"
    image_provider = str(image_ai.get("platform") or "OpenAI").strip()
    if image_provider.lower() == "nvidia":
        image_provider = "OpenAI"
    return {
        "text_ai": {
            "platform": text_provider,
            "api_key": str(text_ai.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or "").strip(),
            "base_url": str(text_ai.get("base_url") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip(),
            "model": str(text_ai.get("model") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip(),
        },
        "image_ai": {
            "platform": image_provider,
            "api_key": str(image_ai.get("api_key") or os.getenv("OPENAI_API_KEY") or "").strip(),
            "base_url": str(image_ai.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip(),
            "model": str(image_ai.get("model") or os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip(),
            "quality": str(image_ai.get("quality") or "medium").strip(),
        },
        "providers": {
            "text": list(TEXT_PROVIDERS),
            "image": list(IMAGE_PROVIDERS),
            "deprecated": ["NVIDIA"],
        },
        "storage": {
            "config_dir": str(config_dir(app_dir)),
            "env_path": str(env_path(app_dir)),
        },
    }


def public_ai_config(app_dir: Path | str, app_config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = ai_config_from_sources(app_dir, app_config)
    for section in ("text_ai", "image_ai"):
        cfg[section]["api_key_masked"] = mask_secret(cfg[section].get("api_key"))
        cfg[section]["api_key_configured"] = bool(cfg[section].get("api_key"))
        cfg[section].pop("api_key", None)
    return cfg


def merge_ai_config(app_dir: Path | str, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    for section in ("text_ai", "image_ai"):
        current_section = merged.get(section) if isinstance(merged.get(section), dict) else {}
        incoming_section = incoming.get(section) if isinstance(incoming.get(section), dict) else {}
        next_section = dict(current_section)
        for key in ("platform", "api_key", "base_url", "model", "quality"):
            if key in incoming_section:
                next_section[key] = incoming_section[key]
        if str(next_section.get("platform") or "").lower() == "nvidia":
            next_section["platform"] = "DeepSeek" if section == "text_ai" else "OpenAI"
            next_section["deprecated_note"] = "NVIDIA provider is deprecated and hidden in the Web UI."
        merged[section] = next_section
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
    for section in ("text_ai", "image_ai"):
        if isinstance(safe.get(section), dict) and safe[section].get("api_key"):
            safe[section]["api_key"] = mask_secret(safe[section]["api_key"])
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
