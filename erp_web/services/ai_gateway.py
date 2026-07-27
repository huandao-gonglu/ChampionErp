"""统一 AI 网关的稳定公开入口。

模型解析、Provider 实现、能力探测和响应解析分别位于聚焦模块；本文件只维护
既有调用签名与少量兼容导出，业务代码无需了解具体连接类型或 API 协议。
"""

from __future__ import annotations

# 这些标准库模块保留为兼容打桩入口。模块对象是进程级单例，因此测试在这里
# 替换 urlopen / subprocess.run / shutil.which 时，Provider 模块会看到同一替换。
import shutil
import subprocess
import urllib

from . import browser_ai_runtime
from .ai_gateway_parsing import (
    _chat_completions_url,
    _chat_response_text,
    _chat_stream_delta_text,
    _content_to_text,
    _http_error_detail,
    _image_edits_url,
    _image_generations_url,
    _model_options,
    _models_url,
    _parse_chat_json_text_or_payload,
    _read_chat_stream_text,
    _read_responses_stream_text,
    _responses_input,
    _responses_stream_delta_text,
    _responses_stream_reasoning_delta_text,
    _responses_url,
    _safe_endpoint_label,
    _sanitize_cli_error,
    parse_json_text,
)
from .ai_gateway_probe import _web_search_probe_date_iso
from .ai_gateway_providers import (
    AI_PROVIDER_REGISTRY,
    AIHTTPError,
    AiChatRequest,
    AiProviderClient,
    BrowserAiProvider,
    CodexCliProvider,
    OpenAICompatibleProvider,
    OpenAIImageProvider,
    OpenAIResponsesProvider,
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
from .ai_provider_contracts import (
    AiChatProvider,
    AiImageProvider,
    AiImageRequest,
    AiProvider,
)

__all__ = [
    "AI_PROVIDER_REGISTRY",
    "AIHTTPError",
    "AiChatProvider",
    "AiChatRequest",
    "AiImageProvider",
    "AiImageRequest",
    "AiProvider",
    "AiProviderClient",
    "BrowserAiProvider",
    "CodexCliProvider",
    "OpenAICompatibleProvider",
    "OpenAIImageProvider",
    "OpenAIResponsesProvider",
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
