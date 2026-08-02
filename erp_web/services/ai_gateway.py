"""AI 网关稳定公开入口。

``connection_type=api`` 的推理请求由 Pydantic Direct Model/Agent 承担；
CLI 与浏览器是明确的非 API 连接，不参与 API 协议转换。
"""

from __future__ import annotations

# CLI 测试和运行时打桩依赖标准库模块单例；它们不属于 API 推理路径。
import shutil
import subprocess

from . import browser_ai_runtime
from .ai_gateway_parsing import parse_json_text
from .ai_gateway_probe import _web_search_probe_date_iso
from .ai_gateway_providers import (
    AI_PROVIDER_REGISTRY,
    AIHTTPError,
    AiChatRequest,
    AiProviderClient,
    BrowserAiProvider,
    CodexCliProvider,
    _provider_for_model,
    chat_json,
    edit_images,
    generate_images,
    list_remote_models,
    probe_browser_model_capabilities,
    probe_cli_model_capabilities,
    probe_model_capabilities,
    resolve_model_for_use_case,
    test_ai_model,
)
from .ai_provider_contracts import AiChatProvider, AiProvider


__all__ = [
    "AI_PROVIDER_REGISTRY",
    "AIHTTPError",
    "AiChatProvider",
    "AiChatRequest",
    "AiProvider",
    "AiProviderClient",
    "BrowserAiProvider",
    "CodexCliProvider",
    "chat_json",
    "edit_images",
    "generate_images",
    "list_remote_models",
    "parse_json_text",
    "probe_browser_model_capabilities",
    "probe_cli_model_capabilities",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
    "test_ai_model",
]
