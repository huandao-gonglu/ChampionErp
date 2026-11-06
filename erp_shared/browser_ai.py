"""浏览器模型的纯配置与返回形状。"""
from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path
from typing import Any

BROWSER_AI_MODE_MANAGED_PROFILE = "managed_profile"
BROWSER_AI_MODE_EXISTING_BROWSER = "existing_browser"
BROWSER_AI_MODES = (BROWSER_AI_MODE_MANAGED_PROFILE, BROWSER_AI_MODE_EXISTING_BROWSER)
BROWSER_AI_DEFAULT_PORT = int(os.environ.get("ERP_AI_BROWSER_DEBUG_PORT", "19333"))
BROWSER_AI_PROVIDER_URLS = {
    "chatgpt": "https://chatgpt.com/",
    "claude": "https://claude.ai/new",
    "gemini": "https://gemini.google.com/app",
    "perplexity": "https://www.perplexity.ai/",
}
BROWSER_AI_PROVIDER_PORT_OFFSETS = {
    "chatgpt": 0,
    "claude": 1,
    "gemini": 2,
    "perplexity": 3,
}


@dataclass(frozen=True)
class BrowserAiRunResult:
    text: str
    image_urls: list[str]
    provider: str
    browser_url: str
    profile_dir: str
    port: int
    ready: bool = True
    title: str = ""
    page_url: str = ""
    message: str = ""


def normalize_browser_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "openai": "chatgpt",
        "chat.openai": "chatgpt",
        "chat.openai.com": "chatgpt",
        "chatgpt.com": "chatgpt",
        "anthropic": "claude",
        "claude.ai": "claude",
        "google": "gemini",
        "bard": "gemini",
        "gemini.google.com": "gemini",
    }
    return aliases.get(text, text or "chatgpt")


def normalize_browser_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in BROWSER_AI_MODES else BROWSER_AI_MODE_MANAGED_PROFILE


def browser_ai_url(model: dict[str, Any]) -> str:
    explicit = str(model.get("browser_url") or model.get("url") or "").strip()
    if explicit.startswith(("http://", "https://")):
        return explicit
    provider = normalize_browser_provider(model.get("browser_provider"))
    url = BROWSER_AI_PROVIDER_URLS.get(provider, "")
    if not url:
        raise RuntimeError(f"浏览器 AI Provider {provider} 未配置网页入口，请填写 browser_url。")
    return url


def browser_ai_profile_dir(app_dir: Path | str, model: dict[str, Any]) -> Path:
    provider = _safe_path_segment(normalize_browser_provider(model.get("browser_provider")), "chatgpt")
    profile = _safe_path_segment(str(model.get("browser_profile") or "").strip() or "default", "default")
    return Path(app_dir) / "browser_profile" / "ai" / provider / profile


def browser_ai_port(model: dict[str, Any]) -> int:
    explicit = str(model.get("browser_port") or "").strip()
    if explicit.isdigit():
        port = int(explicit)
        if 1 <= port <= 65535:
            return port
    provider = normalize_browser_provider(model.get("browser_provider"))
    if provider in BROWSER_AI_PROVIDER_PORT_OFFSETS:
        return BROWSER_AI_DEFAULT_PORT + BROWSER_AI_PROVIDER_PORT_OFFSETS[provider]
    digest = int(hashlib.sha1(provider.encode("utf-8", errors="ignore")).hexdigest()[:4], 16)
    return BROWSER_AI_DEFAULT_PORT + 10 + digest % 100



def _safe_path_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return text[:80] or fallback


def is_canvas_model(model):
    return model.get('connection_type') == 'browser' and normalize_browser_provider(model.get('browser_provider')) == 'gemini'
