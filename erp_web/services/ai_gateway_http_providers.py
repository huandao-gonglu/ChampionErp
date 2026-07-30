"""OpenAI-compatible HTTP Provider implementations and shared provider recipes.

CLI、浏览器与注册表入口位于各自的聚焦模块；本模块只保留 HTTP 协议实现及
跨 Provider 共用的模型能力配方。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from erp_web import http_client

from . import ai_gateway_parsing as gateway_parsing
from . import ai_gateway_probe as probe_runtime
from . import ai_model_config, config_service
from .ai_gateway_provider_types import AiChatRequest
from .ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    AiChatProvider,
)

logger = logging.getLogger(__name__)


class AIHTTPError(RuntimeError):
    """HTTP error raised by the configured AI model endpoint."""

    def __init__(
        self,
        *,
        status_code: int,
        reason: str,
        detail: str,
        model_id: str,
        model_name: str,
        api_style: str,
        endpoint: str,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        self.model_id = model_id
        self.model_name = model_name
        self.api_style = api_style
        self.endpoint = endpoint
        model_label = model_id or model_name or "unknown"
        detail_text = f": {detail}" if detail else f": {reason}" if reason else ""
        super().__init__(
            f"AI 模型请求失败：{model_label} ({api_style}, {endpoint}) HTTP {status_code}{detail_text}"
        )


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


def _chat_stream_delta_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("delta"), str):
        return payload["delta"]
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    choices = payload.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            text = _content_to_text(delta.get("content")) or _content_to_text(message.get("content")) or str(choice.get("text") or "")
            if text:
                parts.append(text)
        return "".join(parts)
    return ""


def _model_api_style(model: dict[str, Any]) -> str:
    return ai_model_config.normalize_api_style(model.get("api_style"))


def _http_provider_for_model(model: dict[str, Any]) -> "_HttpCapabilityProbeMixin":
    """仅在 HTTP 协议簇内按 API 风格选择实现，避免反向依赖总注册表。"""

    provider: _HttpCapabilityProbeMixin
    if _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        provider = OpenAIResponsesProvider()
    else:
        provider = OpenAICompatibleProvider()
    if not provider.supports(model, CAPABILITY_CHAT_JSON):
        raise RuntimeError("当前模型不是可执行 HTTP 请求的 Provider。")
    return provider


def _model_request_body_overrides(model: dict[str, Any]) -> dict[str, Any]:
    """Return the user-managed JSON fields that are merged into API requests last."""
    extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
    request_body = extra.get("request_body")
    return dict(request_body) if isinstance(request_body, dict) else {}


def _merge_request_body(
    model: dict[str, Any],
    body: dict[str, Any],
    request_extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep the standard request shape, then let explicit request JSON override duplicate keys."""
    merged = dict(body)
    if request_extra_body:
        merged.update(request_extra_body)
    merged.update(_model_request_body_overrides(model))
    return merged


def _web_search_body_for_model(model: dict[str, Any]) -> dict[str, Any]:
    provider = _http_provider_for_model(model)
    _, body = provider.default_web_search_profile(model)
    return body


def _chat_web_search_request_mode(extra: dict[str, Any]) -> str:
    request_mode = str(extra.get("web_search_request_mode") or "enable_search").strip().lower()
    return request_mode if request_mode in {"enable_search", "web_search_options"} else "enable_search"


def _web_search_body_for_mode(extra: dict[str, Any], request_mode: str) -> dict[str, Any]:
    if request_mode == "enable_search":
        # Model Studio's OpenAI-compatible endpoint uses ``enable_search``.
        # ``forced_search`` is important for a capability probe: without it the
        # model may elect not to search and turn a working integration into a
        # false negative.
        configured_options = extra.get("search_options")
        search_options = dict(configured_options) if isinstance(configured_options, dict) else {}
        search_options.setdefault("forced_search", True)
        return {"enable_search": True, "search_options": search_options}
    if request_mode == "web_search_options":
        return {"web_search_options": extra.get("web_search_options") or {"search_context_size": "medium"}}
    return {"enable_search": True, "search_options": {"forced_search": True}}


def _web_search_probe_candidates(model: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """由当前 HTTP Provider 生成协议内的联网搜索参数候选项。"""

    provider = _http_provider_for_model(model)
    return provider.web_search_probe_candidates(model)


def _capability_profile(
    model: dict[str, Any],
    capability: str,
    *,
    request_body: dict[str, Any] | None = None,
    request_mode: str = "",
    operation: str = "",
    strategy: str = "",
    tested: bool = True,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "version": 1,
        "tested": tested,
        "connection_type": ai_model_config.model_connection_type(model),
        "provider": str(model.get("provider") or "").strip(),
        "model": ai_model_config.model_name(model),
    }
    if profile["connection_type"] == ai_model_config.CONNECTION_TYPE_API:
        profile["api_style"] = _model_api_style(model)
        profile["base_url"] = ai_model_config.model_base_url(model)
    if request_mode:
        profile["request_mode"] = request_mode
    if operation:
        profile["operation"] = operation
    if strategy:
        profile["strategy"] = strategy
    if request_body is not None:
        profile["request_body"] = dict(request_body)
    return profile


def _default_capability_profile(
    model: dict[str, Any],
    capability: str,
    *,
    tested: bool = False,
) -> dict[str, Any]:
    connection_type = ai_model_config.model_connection_type(model)
    if connection_type == ai_model_config.CONNECTION_TYPE_CLI:
        strategy = "external_read" if capability == ai_model_config.CAP_WEB_SEARCH else "cli_prompt"
        return _capability_profile(model, capability, strategy=strategy, tested=tested)
    if connection_type == ai_model_config.CONNECTION_TYPE_BROWSER:
        strategy = "external_read" if capability == ai_model_config.CAP_WEB_SEARCH else "browser_prompt"
        return _capability_profile(model, capability, strategy=strategy, tested=tested)
    if capability == ai_model_config.CAP_JSON:
        request_body = (
            {"text": {"format": {"type": "json_object"}}}
            if _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES
            else {"response_format": {"type": "json_object"}}
        )
        return _capability_profile(model, capability, request_body=request_body, tested=tested)
    if capability == ai_model_config.CAP_WEB_SEARCH:
        extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
        if isinstance(extra.get("web_search_request_body"), dict):
            request_mode = "custom_request_body"
        elif _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES:
            request_mode = "openai_tools"
        else:
            request_mode = _chat_web_search_request_mode(extra)
        return _capability_profile(
            model,
            capability,
            request_body=_web_search_body_for_model(model),
            request_mode=request_mode,
            tested=tested,
        )
    operation_by_capability = {
        ai_model_config.CAP_CHAT: "chat",
        ai_model_config.CAP_IMAGE_GENERATE: "images.generate",
        ai_model_config.CAP_IMAGE_EDIT: "images.edit",
        ai_model_config.CAP_TOOL_CALLING: "tools",
    }
    return _capability_profile(
        model,
        capability,
        request_body={},
        operation=operation_by_capability.get(capability, ""),
        tested=tested,
    )


def _saved_capability_profile(model: dict[str, Any], capability: str) -> dict[str, Any] | None:
    profiles = ai_model_config.normalize_capability_profiles(model.get("capability_profiles"))
    profile = profiles.get(capability)
    if not profile:
        return None
    if profile.get("tested") is not True:
        raise RuntimeError(f"AI 能力 {capability} 的配方尚未通过测试，请重新测试该能力。")
    connection_type = ai_model_config.model_connection_type(model)
    saved_connection_type = str(profile.get("connection_type") or "").strip()
    if saved_connection_type and saved_connection_type != connection_type:
        raise RuntimeError(f"AI 能力 {capability} 的连接类型已变化，请重新测试该能力。")
    identity_fields = {
        "provider": str(model.get("provider") or "").strip(),
        "model": ai_model_config.model_name(model),
    }
    if connection_type == ai_model_config.CONNECTION_TYPE_API:
        identity_fields["base_url"] = ai_model_config.model_base_url(model)
    for key, current_value in identity_fields.items():
        saved_value = str(profile.get(key) or "").strip()
        if saved_value and saved_value != current_value:
            raise RuntimeError(f"AI 能力 {capability} 的 {key} 已变化，请重新测试该能力。")
    if connection_type == ai_model_config.CONNECTION_TYPE_API:
        saved_api_style = str(profile.get("api_style") or "").strip()
        if saved_api_style and saved_api_style != _model_api_style(model):
            raise RuntimeError(f"AI 能力 {capability} 的 API 风格已变化，请重新测试该能力。")
        model_overrides = _model_request_body_overrides(model)
        request_body = profile.get("request_body")
        if isinstance(request_body, dict):
            for key, value in request_body.items():
                if key in model_overrides and model_overrides[key] != value:
                    raise RuntimeError(f"AI 能力 {capability} 的请求字段 {key} 已变化，请重新测试该能力。")
    return profile


def _resolved_capability_profile(model: dict[str, Any], capability: str) -> dict[str, Any]:
    """Return the saved activation recipe or the current model-derived default."""
    return _saved_capability_profile(model, capability) or _default_capability_profile(model, capability)


def _capability_request_body(
    model: dict[str, Any],
    capabilities: set[str] | tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Compose provider request fields from model-level capability recipes."""
    merged: dict[str, Any] = {}
    for capability in ai_model_config.normalize_capabilities(list(capabilities)):
        profile = _resolved_capability_profile(model, capability)
        request_body = profile.get("request_body")
        if not isinstance(request_body, dict):
            continue
        for key, value in request_body.items():
            if key in merged and merged[key] != value:
                raise RuntimeError(f"AI 能力配方冲突：{capability} 与其他能力都定义了请求字段 {key}。")
            merged[key] = value
    return merged


def _validate_capability_profiles(
    model: dict[str, Any],
    capabilities: tuple[str, ...] | list[str],
) -> None:
    for capability in ai_model_config.normalize_capabilities(list(capabilities)):
        _resolved_capability_profile(model, capability)


def _profile_with_effective_request_body(profile: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Capture the exact tested values for fields owned by this capability."""
    request_body = profile.get("request_body")
    if not isinstance(request_body, dict):
        return profile
    effective = {key: body[key] for key in request_body if key in body}
    return {**profile, "request_body": effective}


def _ai_http_error(
    exc: urllib.error.HTTPError,
    *,
    model: dict[str, Any],
    model_name: str,
    api_style: str,
    url: str,
) -> AIHTTPError:
    return AIHTTPError(
        status_code=int(exc.code or 0),
        reason=str(exc.reason or ""),
        detail=_http_error_detail(exc),
        model_id=str(model.get("id") or ""),
        model_name=model_name,
        api_style=api_style,
        endpoint=_safe_endpoint_label(url),
    )


def _resolved_model(app_dir: Path | str, app_config: dict[str, Any] | None, use_case_id: str, model_id: str = "") -> dict[str, Any]:
    config_service.load_env(app_dir)
    return ai_model_config.resolve_ai_model(app_config, use_case_id, model_id=model_id)


def _resolved_use_case_request(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
    *,
    model_id: str = "",
    timeout_seconds: int | None = None,
    default_timeout_seconds: int,
) -> tuple[dict[str, Any], tuple[str, ...], int]:
    model = _resolved_model(app_dir, app_config, use_case_id, model_id)
    binding = ai_model_config.ai_use_case_binding(app_config, use_case_id)
    required_capabilities = tuple(ai_model_config.ai_use_case_required_capabilities(use_case_id))
    provider_default_timeout = (
        180
        if ai_model_config.model_connection_type(model) != ai_model_config.CONNECTION_TYPE_API
        else default_timeout_seconds
    )
    effective_timeout = int(
        binding.get("timeout_override_seconds")
        or timeout_seconds
        or model.get("timeout_seconds")
        or provider_default_timeout
    )
    return model, required_capabilities, effective_timeout


def resolve_model_for_use_case(app_dir: Path | str, app_config: dict[str, Any] | None, use_case_id: str, model_id: str = "") -> dict[str, Any]:
    return _resolved_model(app_dir, app_config, use_case_id, model_id)


def list_remote_models(base_url: str, api_key: str, timeout: int = 60) -> list[dict[str, str]]:
    url = _models_url(base_url)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    return _model_options(payload)


def _post_json(url: str, api_key: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    return http_client.request_json(
        url,
        method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        timeout=timeout,
    )


def probe_model_capabilities(
    model: dict[str, Any],
    api_key: str,
    model_name: str,
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = _http_provider_for_model(model)
    return probe_runtime.run_capability_probes(
        provider,
        probe_runtime.CapabilityProbeContext(
            model=model,
            api_key=api_key,
            model_name=model_name,
            timeout=timeout,
            probe_options=probe_options,
        ),
        capabilities,
    )


# HTTP Provider 统一使用拆分后的解析/探测公共实现，不在本模块复制协议解析。

parse_json_text = gateway_parsing.parse_json_text
_models_url = gateway_parsing._models_url
_image_generations_url = gateway_parsing._image_generations_url
_image_edits_url = gateway_parsing._image_edits_url
_model_options = gateway_parsing._model_options
_safe_endpoint_label = gateway_parsing._safe_endpoint_label
_http_error_detail = gateway_parsing._http_error_detail

_probe_options = probe_runtime._probe_options
_capability_test_outcome = probe_runtime._capability_test_outcome


def _ensure_http_model_ready(model: dict[str, Any]) -> tuple[str, str, str]:
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 API Key。")
    model_name = ai_model_config.model_name(model)
    if not model_name:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置模型名。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise RuntimeError(f"AI 模型 {model.get('id')} 未配置 Base URL。")
    return api_key, model_name, base_url


def _test_http_model(app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw_model if isinstance(raw_model, dict) else {}
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError("请先填写 API Key。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise RuntimeError("请先填写 Base URL。")
    timeout = int(model.get("timeout_seconds") or 60)
    available_models = list_remote_models(base_url, api_key, timeout)
    model_name = ai_model_config.model_name(model)
    probe_requested = raw.get("probe_capabilities", True) is not False
    trigger = str(raw.get("test_trigger") or "").strip()
    probe_options = _probe_options(raw)
    requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
    capability_probe = {"supported": [], "unsupported": [], "results": {}}
    if probe_requested:
        if not model_name:
            raise RuntimeError("请先选择模型。")
        capability_probe = probe_model_capabilities(
            model,
            api_key,
            model_name,
            requested_capabilities,
            timeout,
            probe_options,
        )
    outcome = _capability_test_outcome(
        capability_probe,
        probe_requested,
        True,
        f"{model.get('name') or model.get('id')} 测试成功：接口可以连接。",
        "可以保存配置并继续使用 AI 功能。",
    )
    provider = _http_provider_for_model(model)
    result = {
        **outcome,
        "channel": "ai_model",
        "model_id": model.get("id"),
        "provider": model.get("provider"),
        "connection_type": ai_model_config.CONNECTION_TYPE_API,
        "api_style": provider.api_style,
        "model": model_name,
        "available_models": available_models,
        "supported_capabilities": capability_probe["supported"],
        "capability_results": capability_probe["results"],
        "tested_capabilities": requested_capabilities,
        "test_trigger": trigger,
        "masked_key": ai_model_config.mask_secret(api_key),
    }
    logger.info(
        "AI model test result trigger=%s model_id=%s provider=%s model=%s probe=%s requested=%s supported=%s unsupported=%s capability_errors=%s available_models=%s",
        trigger,
        model.get("id"),
        model.get("provider"),
        model_name,
        probe_requested,
        requested_capabilities,
        capability_probe["supported"],
        capability_probe["unsupported"],
        {key: value.get("error") for key, value in capability_probe["results"].items() if isinstance(value, dict) and value.get("error")},
        len(available_models),
    )
    return result


class _HttpCapabilityProbeMixin:
    """HTTP Provider 的统一能力探测编排。

    具体协议只在子类实现 endpoint、请求体、响应文本和工具调用校验；本类不读取
    ``api_style``，因此新增 HTTP 协议不会再扩散到共享 probe 函数。
    """

    api_style = ""
    probe_reraise_marker = "Codex CLI 模型"
    probe_web_search_meta = True

    def endpoint_url(self, base_url: str) -> str:
        raise NotImplementedError

    def build_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def response_text(self, payload: Any) -> str:
        raise NotImplementedError

    def default_web_search_profile(
        self,
        model: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def web_search_probe_candidates(
        self,
        model: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        raise NotImplementedError

    def tool_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def validate_tool_probe_response(self, payload: Any) -> None:
        raise NotImplementedError

    def _post_probe(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        body = self.build_probe_body(
            context.model,
            context.model_name,
            messages,
            max_tokens,
            extra_body,
        )
        payload = _post_json(
            self.endpoint_url(ai_model_config.model_base_url(context.model)),
            context.api_key,
            body,
            context.timeout,
        )
        return body, payload

    def _probe_chat(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        self._post_probe(context, messages, 8)

    def _probe_json(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        profile = _default_capability_profile(
            context.model,
            ai_model_config.CAP_JSON,
            tested=True,
        )
        capability_body = profile.get("request_body")
        body, payload = self._post_probe(
            context,
            messages,
            32,
            capability_body if isinstance(capability_body, dict) else {},
        )
        parse_json_text(self.response_text(payload))
        return _profile_with_effective_request_body(profile, body)

    def _probe_web_search(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        json_profile = _default_capability_profile(
            context.model,
            ai_model_config.CAP_JSON,
            tested=True,
        )
        json_body = json_profile.get("request_body")
        json_body = json_body if isinstance(json_body, dict) else {}
        errors: list[str] = []
        for request_mode, web_search_body in self.web_search_probe_candidates(
            context.model
        ):
            try:
                profile = _capability_profile(
                    context.model,
                    ai_model_config.CAP_WEB_SEARCH,
                    request_body=web_search_body,
                    request_mode=request_mode,
                    tested=True,
                )
                body, payload = self._post_probe(
                    context,
                    messages,
                    600,
                    {**json_body, **web_search_body},
                )
                probe_runtime._validate_web_search_probe_data(
                    parse_json_text(self.response_text(payload))
                )
                return _profile_with_effective_request_body(profile, body)
            except Exception as exc:
                errors.append(
                    f"{request_mode}: {probe_runtime._capability_error_text(exc)}"
                )
        raise RuntimeError("未能验证实时联网搜索。已尝试 " + "；".join(errors))

    def _probe_tool_calling(
        self,
        context: probe_runtime.CapabilityProbeContext,
    ) -> None:
        body = self.tool_probe_body(context.model, context.model_name)
        payload = _post_json(
            self.endpoint_url(ai_model_config.model_base_url(context.model)),
            context.api_key,
            _merge_request_body(context.model, body),
            context.timeout,
        )
        self.validate_tool_probe_response(payload)

    def _probe_image_generate(
        self,
        context: probe_runtime.CapabilityProbeContext,
        prompt: str,
    ) -> None:
        _post_json(
            _image_generations_url(
                ai_model_config.model_base_url(context.model)
            ),
            context.api_key,
            {
                "model": context.model_name,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
            },
            context.timeout,
        )

    def _probe_image_edit(
        self,
        context: probe_runtime.CapabilityProbeContext,
        prompt: str,
    ) -> None:
        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f"
            b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00"
            b"IEND\xaeB`\x82"
        )
        body, boundary = probe_runtime._multipart_body(
            {
                "model": context.model_name,
                "prompt": prompt,
                "size": "1024x1024",
                "n": "1",
            },
            {"image": ("probe.png", tiny_png, "image/png")},
        )
        request = urllib.request.Request(
            _image_edits_url(ai_model_config.model_base_url(context.model)),
            data=body,
            headers={
                "Authorization": f"Bearer {context.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=context.timeout) as response:
            response.read()

    def probe_capability(
        self,
        capability: str,
        context: probe_runtime.CapabilityProbeContext,
    ) -> dict[str, Any]:
        model = context.model
        options = context.probe_options
        profile = _default_capability_profile(model, capability, tested=True)
        if capability == ai_model_config.CAP_CHAT:
            self._probe_chat(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._chat_probe_default_messages(),
                ),
            )
        elif capability == ai_model_config.CAP_JSON:
            profile = self._probe_json(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._json_probe_default_messages(),
                ),
            )
        elif capability == ai_model_config.CAP_WEB_SEARCH:
            profile = self._probe_web_search(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._web_search_probe_prompt(),
                ),
            )
        elif capability == ai_model_config.CAP_IMAGE_GENERATE:
            self._probe_image_generate(
                context,
                probe_runtime._probe_image_prompt(options, "single small blue square"),
            )
        elif capability == ai_model_config.CAP_IMAGE_EDIT:
            self._probe_image_edit(
                context,
                probe_runtime._probe_image_prompt(options, "turn the pixel blue"),
            )
        elif capability == ai_model_config.CAP_TOOL_CALLING:
            self._probe_tool_calling(context)
        else:
            raise probe_runtime.SkipCapabilityProbe
        return profile


class OpenAICompatibleProvider(_HttpCapabilityProbeMixin, AiChatProvider):
    provider_id = "openai_compatible"
    api_style = ai_model_config.API_STYLE_OPENAI_COMPATIBLE

    def endpoint_url(self, base_url: str) -> str:
        return gateway_parsing._chat_completions_url(base_url)

    def build_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _merge_request_body(
            model,
            {
                "model": model_name,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
                **(extra_body or {}),
            },
        )

    def response_text(self, payload: Any) -> str:
        return gateway_parsing._chat_response_text(payload)

    def read_stream_text(
        self,
        response: Any,
        token_callback: Callable[[str], None] | None = None,
    ) -> str:
        return gateway_parsing._read_chat_stream_text(response, token_callback)

    def default_web_search_profile(
        self,
        model: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
        custom_body = extra.get("web_search_request_body")
        if isinstance(custom_body, dict):
            return "custom_request_body", dict(custom_body)
        request_mode = _chat_web_search_request_mode(extra)
        return request_mode, _web_search_body_for_mode(extra, request_mode)

    def web_search_probe_candidates(
        self,
        model: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
        candidates: list[tuple[str, dict[str, Any]]] = []
        custom_body = extra.get("web_search_request_body")
        if isinstance(custom_body, dict) and custom_body:
            candidates.append(("custom_request_body", dict(custom_body)))
        seen: set[str] = set()
        for request_mode in (
            _chat_web_search_request_mode(extra),
            "enable_search",
            "web_search_options",
        ):
            if request_mode in seen:
                continue
            seen.add(request_mode)
            candidates.append(
                (request_mode, _web_search_body_for_mode(extra, request_mode))
            )
        return candidates

    def tool_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
    ) -> dict[str, Any]:
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": "Call the noop tool."}],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "description": "No-op test tool.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "noop"},
            },
        }

    def validate_tool_probe_response(self, payload: Any) -> None:
        choices = payload.get("choices") if isinstance(payload, dict) else []
        first = (
            choices[0]
            if isinstance(choices, list)
            and choices
            and isinstance(choices[0], dict)
            else {}
        )
        message = (
            first.get("message")
            if isinstance(first.get("message"), dict)
            else {}
        )
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise RuntimeError(
                "Provider accepted tool parameters but did not return tool_calls."
            )

    def _build_chat_body(
        self,
        request: AiChatRequest,
        model_name: str,
    ) -> dict[str, Any]:
        capabilities = [
            capability
            for capability in request.required_capabilities
            if capability != ai_model_config.CAP_JSON or request.response_format
        ]
        body: dict[str, Any] = {
            "model": model_name,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": bool(request.stream),
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        body.update(_capability_request_body(request.model, capabilities))
        body = _merge_request_body(request.model, body, request.extra_body)
        body["stream"] = bool(request.stream)
        return body

    def supports(self, model: dict[str, Any], capability: str = CAPABILITY_CHAT_JSON) -> bool:
        return (
            capability == CAPABILITY_CHAT_JSON
            and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API
            and _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_COMPATIBLE
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        model = request.model
        api_key, model_name, base_url = _ensure_http_model_ready(model)
        url = self.endpoint_url(base_url)
        body = self._build_chat_body(request, model_name)
        timeout = int(request.timeout_seconds or model.get("timeout_seconds") or 60)
        if request.conversation:
            request.conversation.emit_custom(
                "provider.request",
                {
                    "endpoint": url,
                    "method": "POST",
                    "messages": request.messages,
                    "provider_payload": body,
                },
            )
        raw_text = self._post_chat(model, api_key, model_name, url, body, timeout, request)
        if request.conversation:
            request.conversation.finish_assistant_message(raw_text)
        return parse_json_text(raw_text)

    def _post_chat(
        self,
        model: dict[str, Any],
        api_key: str,
        model_name: str,
        url: str,
        body: dict[str, Any],
        timeout: int,
        request: AiChatRequest,
    ) -> str:
        stream = bool(body.get("stream"))
        http_request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                if stream:
                    return self.read_stream_text(response, request.emit_delta)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _ai_http_error(
                exc,
                model=model,
                model_name=model_name,
                api_style=self.api_style,
                url=url,
            ) from exc
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if request.conversation:
            request.conversation.emit("RAW", event=payload, source=self.provider_id)
        return self.response_text(payload)

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        return _test_http_model(app_dir, model, raw_model)


class OpenAIResponsesProvider(_HttpCapabilityProbeMixin, AiChatProvider):
    provider_id = "openai_responses"
    api_style = ai_model_config.API_STYLE_OPENAI_RESPONSES

    def endpoint_url(self, base_url: str) -> str:
        return gateway_parsing._responses_url(base_url)

    def request_input(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": str(message.get("role") or "user"),
                "content": str(message.get("content") or ""),
            }
            for message in messages
        ]

    def build_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _merge_request_body(
            model,
            {
                "model": model_name,
                "input": self.request_input(messages),
                "temperature": 0,
                "max_output_tokens": max_tokens,
                "stream": False,
                **(extra_body or {}),
            },
        )

    def response_text(self, payload: Any) -> str:
        return gateway_parsing._chat_response_text(payload)

    def read_stream_text(
        self,
        response: Any,
        token_callback: Callable[[str], None] | None = None,
    ) -> str:
        return gateway_parsing._read_responses_stream_text(
            response,
            token_callback,
        )

    def default_web_search_profile(
        self,
        model: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
        custom_body = extra.get("web_search_request_body")
        if isinstance(custom_body, dict):
            return "custom_request_body", dict(custom_body)
        return (
            "openai_tools",
            {"tools": extra.get("web_search_tools") or [{"type": "web_search"}]},
        )

    def web_search_probe_candidates(
        self,
        model: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
        candidates: list[tuple[str, dict[str, Any]]] = []
        custom_body = extra.get("web_search_request_body")
        if isinstance(custom_body, dict) and custom_body:
            candidates.append(("custom_request_body", dict(custom_body)))
        candidates.append(
            (
                "openai_tools",
                {
                    "tools": extra.get("web_search_tools")
                    or [{"type": "web_search"}]
                },
            )
        )
        return candidates

    def tool_probe_body(
        self,
        model: dict[str, Any],
        model_name: str,
    ) -> dict[str, Any]:
        return {
            "model": model_name,
            "input": self.request_input(
                [{"role": "user", "content": "Call the noop tool."}]
            ),
            "temperature": 0,
            "max_output_tokens": 32,
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "name": "noop",
                    "description": "No-op test tool.",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            "tool_choice": {"type": "function", "name": "noop"},
        }

    def validate_tool_probe_response(self, payload: Any) -> None:
        output = payload.get("output") if isinstance(payload, dict) else []
        tool_calls = (
            [
                item
                for item in output
                if isinstance(item, dict)
                and str(item.get("type") or "") == "function_call"
            ]
            if isinstance(output, list)
            else []
        )
        if not tool_calls:
            raise RuntimeError(
                "Provider accepted tool parameters but did not return "
                "a function_call output item."
            )

    def _build_chat_body(
        self,
        request: AiChatRequest,
        model_name: str,
    ) -> dict[str, Any]:
        capabilities = [
            capability
            for capability in request.required_capabilities
            if capability != ai_model_config.CAP_JSON or request.response_format
        ]
        body: dict[str, Any] = {
            "model": model_name,
            "input": self.request_input(request.messages),
            "temperature": request.temperature,
            "stream": bool(request.stream),
        }
        if request.max_tokens is not None:
            body["max_output_tokens"] = request.max_tokens
        body.update(_capability_request_body(request.model, capabilities))
        body = _merge_request_body(request.model, body, request.extra_body)
        body["stream"] = bool(request.stream)
        return body

    def supports(self, model: dict[str, Any], capability: str = CAPABILITY_CHAT_JSON) -> bool:
        return (
            capability == CAPABILITY_CHAT_JSON
            and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API
            and _model_api_style(model) == ai_model_config.API_STYLE_OPENAI_RESPONSES
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        model = request.model
        api_key, model_name, base_url = _ensure_http_model_ready(model)
        url = self.endpoint_url(base_url)
        body = self._build_chat_body(request, model_name)
        timeout = int(request.timeout_seconds or model.get("timeout_seconds") or 60)
        stream = bool(body.get("stream"))
        if request.conversation:
            request.conversation.emit_custom(
                "provider.request",
                {
                    "endpoint": url,
                    "method": "POST",
                    "messages": request.messages,
                    "provider_payload": body,
                },
            )
        http_request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                if stream:
                    raw_text = self.read_stream_text(response, request.emit_delta)
                    if request.conversation:
                        request.conversation.finish_assistant_message(raw_text)
                    return parse_json_text(raw_text)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _ai_http_error(
                exc,
                model=model,
                model_name=model_name,
                api_style=ai_model_config.API_STYLE_OPENAI_RESPONSES,
                url=url,
            ) from exc
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        raw_text = self.response_text(payload)
        if request.conversation:
            request.conversation.emit("RAW", event=payload, source=self.provider_id)
            request.conversation.finish_assistant_message(raw_text)
        return parse_json_text(raw_text)

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        return _test_http_model(app_dir, model, raw_model)


__all__ = [
    "AIHTTPError",
    "AiChatRequest",
    "OpenAICompatibleProvider",
    "OpenAIResponsesProvider",
    "_capability_test_outcome",
    "_default_capability_profile",
    "_model_api_style",
    "_resolved_use_case_request",
    "_validate_capability_profiles",
    "list_remote_models",
    "probe_model_capabilities",
    "resolve_model_for_use_case",
]
