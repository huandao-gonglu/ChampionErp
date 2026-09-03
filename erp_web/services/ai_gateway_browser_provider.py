"""浏览器会话 AI Provider 与浏览器传输编排。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ai_gateway_probe as probe_runtime
from . import ai_model_config, browser_ai_runtime
from .ai_gateway_parsing import parse_json_text
from .ai_gateway_provider_profiles import non_http_capability_profile
from .ai_gateway_provider_prompting import _browser_prompt
from .ai_gateway_provider_types import AiChatRequest
from .ai_provider_contracts import CAPABILITY_CHAT_JSON, AiChatProvider

_probe_options = probe_runtime._probe_options
_capability_test_outcome = probe_runtime._capability_test_outcome

def _browser_chat_result(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    timeout: int,
    *,
    response_format: bool = True,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> browser_ai_runtime.BrowserAiRunResult:
    prompt = _browser_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
    )
    return browser_ai_runtime.run_browser_ai_chat(app_dir, model, prompt, timeout=timeout)

def probe_browser_model_capabilities(
    app_dir: Path | str,
    model: dict[str, Any],
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return probe_runtime.run_capability_probes(
        BrowserAiProvider(),
        probe_runtime.CapabilityProbeContext(
            model=model,
            app_dir=app_dir,
            timeout=timeout,
            probe_options=probe_options,
        ),
        capabilities,
    )

class BrowserAiProvider(AiChatProvider):
    provider_id = "browser"

    def supports(self, model: dict[str, Any], capability: str = CAPABILITY_CHAT_JSON) -> bool:
        return (
            capability == CAPABILITY_CHAT_JSON
            and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_BROWSER
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        timeout = int(request.timeout_seconds or request.model.get("timeout_seconds") or 180)
        prompt = _browser_prompt(
            request.messages,
            response_format=request.response_format,
            allow_external_read=ai_model_config.CAP_WEB_SEARCH in request.required_capabilities,
        )
        result = browser_ai_runtime.run_browser_ai_chat(
            request.app_dir,
            request.model,
            prompt,
            timeout=timeout,
        )
        if request.stream and result.text:
            request.emit_delta(result.text)
        return parse_json_text(result.text)

    def _run_probe(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
        *,
        response_format: bool,
        allow_external_read: bool = False,
        allow_generated_artifacts: bool = False,
    ) -> browser_ai_runtime.BrowserAiRunResult:
        return _browser_chat_result(
            context.app_dir or ".",
            context.model,
            messages,
            context.timeout,
            response_format=response_format,
            allow_external_read=allow_external_read,
            allow_generated_artifacts=allow_generated_artifacts,
        )

    def _probe_chat(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        result = self._run_probe(context, messages, response_format=False)
        probe_runtime._validate_chat_probe_text(result.text)

    def _probe_json(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        result = self._run_probe(context, messages, response_format=True)
        probe_runtime._validate_json_probe_data(parse_json_text(result.text))

    def _probe_web_search(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        result = self._run_probe(
            context,
            messages,
            response_format=True,
            allow_external_read=True,
        )
        probe_runtime._validate_web_search_probe_data(parse_json_text(result.text))

    def _probe_image_generate(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        app_dir = context.app_dir or "."
        result = self._run_probe(
            context,
            messages,
            response_format=True,
            allow_generated_artifacts=True,
        )
        data = probe_runtime._browser_image_probe_data_from_result(result)
        probe_runtime._validate_browser_image_generate_probe(
            data,
            result,
            app_dir,
        )

    def probe_capability(
        self,
        capability: str,
        context: probe_runtime.CapabilityProbeContext,
    ) -> dict[str, Any]:
        options = context.probe_options
        if capability == ai_model_config.CAP_CHAT:
            self._probe_chat(
                context,
                probe_runtime._chat_probe_default_messages(),
            )
        elif capability == ai_model_config.CAP_JSON:
            self._probe_json(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._json_probe_default_messages(),
                ),
            )
        elif capability == ai_model_config.CAP_WEB_SEARCH:
            self._probe_web_search(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._web_search_probe_prompt(),
                ),
            )
        elif capability == ai_model_config.CAP_IMAGE_GENERATE:
            self._probe_image_generate(
                context,
                probe_runtime._probe_messages(
                    options,
                    probe_runtime._cli_image_generate_probe_prompt(),
                ),
            )
        else:
            raise probe_runtime.CapabilityProbeUnavailable(
                f"浏览器 Provider 尚未接入 {capability} 能力。"
            )
        return non_http_capability_profile(
            context.model,
            capability,
            channel="browser",
            tested=True,
        )

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = raw_model if isinstance(raw_model, dict) else {}
        timeout = int(model.get("timeout_seconds") or 180)
        probe_options = _probe_options(raw)
        requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
        probe_requested = raw.get("probe_capabilities", True) is not False
        page = browser_ai_runtime.open_browser_ai_page(app_dir, model, timeout=min(timeout, 45))
        capability_probe = probe_runtime.empty_capability_probe_report()
        if probe_requested:
            capability_probe = probe_browser_model_capabilities(app_dir, model, requested_capabilities, timeout, probe_options)
        connection_ok = bool(page.ready)
        connection_message = (
            f"{model.get('name') or model.get('id')} 测试成功：浏览器网页已连接。"
            if connection_ok
            else f"{model.get('name') or model.get('id')} 已打开浏览器网页，请先手动登录。"
        )
        connection_next_action = (
            "能力勾选会发送测试消息；测试成功后才会启用对应能力。"
            if connection_ok
            else "在打开的浏览器窗口完成登录后，再勾选需要的能力并测试。"
        )
        outcome = _capability_test_outcome(
            capability_probe,
            probe_requested,
            connection_ok,
            connection_message,
            connection_next_action,
        )
        return {
            **outcome,
            "channel": "ai_model",
            "model_id": model.get("id"),
            "provider": model.get("provider"),
            "connection_type": ai_model_config.CONNECTION_TYPE_BROWSER,
            "browser_provider": browser_ai_runtime.normalize_browser_provider(model.get("browser_provider")),
            "browser_mode": browser_ai_runtime.normalize_browser_mode(model.get("browser_mode")),
            "browser_profile": str(model.get("browser_profile") or "default"),
            "browser_url": page.browser_url,
            "profile_dir": page.profile_dir,
            "port": page.port,
            "model": ai_model_config.model_name(model),
            "available_models": ([{"id": ai_model_config.model_name(model), "label": ai_model_config.model_name(model)}] if ai_model_config.model_name(model) else []),
            "supported_capabilities": capability_probe["supported"],
            "unsupported_capabilities": capability_probe["unsupported"],
            "unavailable_capabilities": capability_probe["unavailable"],
            "inconclusive_capabilities": capability_probe["inconclusive"],
            "capability_results": capability_probe["results"],
            "tested_capabilities": requested_capabilities,
            "test_trigger": str(raw.get("test_trigger") or "").strip(),
            "ready": page.ready,
        }

__all__ = [
    "BrowserAiProvider",
    "_browser_chat_result",
    "probe_browser_model_capabilities",
]
