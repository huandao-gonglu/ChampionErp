"""API 模型连接与能力探测；所有推理探针均走 Pydantic Model。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import ToolDefinition

from . import ai_direct_request_service, ai_gateway_probe, ai_model_config
from .ai_model_discovery import AiModelDiscoveryError, list_remote_models
from .ai_model_factory import (
    AiModelFactoryError,
    PydanticModelBinding,
    create_pydantic_probe_binding,
)


_IMAGE_EDIT_PROBE_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "image_edit_probe.png"
)


class _WebSearchProbeOutput(BaseModel):
    """实时联网能力探测的结构化终态。"""

    model_config = ConfigDict(extra="forbid")

    can_access_web: bool
    source_url: str = ""
    location: str = ""
    date: str = ""
    weather: str = ""
    temperature: str = ""
    evidence: str = ""
    reason: str = ""


def _image_edit_probe_bytes() -> bytes:
    """读取随应用发布的有效 PNG，供图片编辑能力探测使用。"""

    return _IMAGE_EDIT_PROBE_PATH.read_bytes()


def _json_probe_strategy(binding: PydanticModelBinding) -> str:
    if binding.api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        return "responses_prompted_json"
    if binding.model.profile.get("supports_json_object_output", False):
        return "json_object"
    return "prompted_json"


def _validate_api_image_results(
    results: list[dict[str, Any]],
    *,
    source: bytes | None = None,
) -> None:
    if not results:
        raise ai_gateway_probe.CapabilityProbeUnsupported(
            "Pydantic 图片 Model 没有返回图片。"
        )
    valid_reference = False
    changed_binary = source is None
    for result in results:
        if not isinstance(result, dict):
            continue
        image_url = str(result.get("url") or result.get("image_url") or "").strip()
        if image_url.startswith(("http://", "https://")):
            valid_reference = True
            changed_binary = True
        encoded = str(
            result.get("b64_json")
            or result.get("image_base64")
            or result.get("base64")
            or ""
        ).strip()
        if not ai_gateway_probe._valid_base64_image(encoded):
            continue
        valid_reference = True
        if source is not None:
            try:
                changed_binary = changed_binary or base64.b64decode(encoded) != source
            except ValueError:
                continue
    if not valid_reference:
        raise ai_gateway_probe.CapabilityProbeUnsupported(
            "图片探测没有返回可验证的图片数据或 URL。"
        )
    if source is not None and not changed_binary:
        raise ai_gateway_probe.CapabilityProbeUnsupported(
            "图片编辑结果与输入图片完全相同。"
        )


def _probe_capability(
    context: ai_gateway_probe.CapabilityProbeContext,
) -> dict[str, Any]:
    app_dir = context.app_dir or "."
    model = context.model
    capability = context.capability
    timeout = context.timeout
    options = (
        context.probe_options
        if isinstance(context.probe_options, dict)
        else {}
    )
    probe_token = context.probe_token
    try:
        binding = create_pydantic_probe_binding(
            model,
            probe_capability=capability,
            generation_settings=(
                {"temperature": 0}
                if capability
                in {
                    ai_model_config.CAP_CHAT,
                    ai_model_config.CAP_JSON,
                    ai_model_config.CAP_WEB_SEARCH,
                    ai_model_config.CAP_TOOL_CALLING,
                }
                else None
            ),
            timeout_seconds=timeout,
        )
    except AiModelFactoryError as exc:
        if "未接入" in str(exc) or "不支持 API 协议" in str(exc):
            raise ai_gateway_probe.CapabilityProbeUnavailable(str(exc)) from None
        raise ai_gateway_probe.CapabilityProbeInconclusive(str(exc)) from None
    if capability == ai_model_config.CAP_CHAT:
        messages = ai_gateway_probe._chat_probe_default_messages()
        response = ai_direct_request_service.request_for_probe(
            app_dir=app_dir,
            binding=binding,
            messages=messages,
        )
        ai_gateway_probe._validate_chat_probe_text(response.text or "")
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy="pydantic_direct_text",
        )
    if capability == ai_model_config.CAP_JSON:
        messages = ai_gateway_probe._probe_messages(
            options,
            ai_gateway_probe._json_probe_default_messages(),
        )
        data, _ = ai_direct_request_service.request_json_for_probe(
            app_dir=app_dir,
            binding=binding,
            messages=messages,
        )
        ai_gateway_probe._validate_json_probe_data(data)
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy=_json_probe_strategy(binding),
        )
    if capability == ai_model_config.CAP_WEB_SEARCH:
        messages = ai_gateway_probe._probe_messages(
            options,
            ai_gateway_probe._web_search_probe_prompt(),
        )
        try:
            data, request_mode = ai_direct_request_service.request_json_for_probe(
                app_dir=app_dir,
                binding=binding,
                messages=messages,
                web_search=True,
                output_type=_WebSearchProbeOutput,
            )
        except ai_direct_request_service.AiProbeStructuredOutputError:
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                "模型返回了普通文本或不符合约定的结果，"
                "未能证明实时联网能力。"
            ) from None
        ai_gateway_probe._validate_web_search_probe_data(data)
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy="pydantic_web_search",
            request_mode=request_mode,
        )
    if capability == ai_model_config.CAP_TOOL_CALLING:
        expected_final = f"probe-complete:{probe_token}"
        tool = ToolDefinition(
            name="noop",
            description="ERP Function Call capability probe.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "probe_token": {
                        "type": "string",
                        "description": "原样传入用户消息里的 probe_token。",
                    }
                },
                "required": ["probe_token"],
            },
        )
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": (
                    f"Call the noop tool with probe_token={probe_token}. "
                    f"After the tool returns, reply exactly {expected_final}"
                ),
            }
        ]
        response = ai_direct_request_service.request_for_probe(
            app_dir=app_dir,
            binding=binding,
            messages=messages,
            function_tools=[tool],
            # Qwen 思考模式不接受 tool_choice=required/object；允许文本会让
            # Pydantic 发送 tool_choice=auto，是否真的调用工具仍由下方严格校验。
            allow_text_output=True,
            # Function Call 需要两次模型请求；只把完成工具回传后的最终响应
            # 作为 presentation 根流，避免第一段 DONE 留下未闭合工具卡。
            publish_native_events=False,
        )
        tool_calls = [
            part for part in response.parts if isinstance(part, ToolCallPart)
        ]
        if len(tool_calls) != 1:
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                "模型没有返回唯一的 function tool call。"
            )
        tool_call = tool_calls[0]
        if tool_call.tool_name != "noop":
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                f"模型调用了未声明工具：{tool_call.tool_name or 'empty'}。"
            )
        if tool_call.args_as_dict() != {"probe_token": probe_token}:
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                "Function Call 参数不符合探测 schema。"
            )
        if not str(tool_call.tool_call_id or "").strip():
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                "Function Call 没有返回 tool_call_id。"
            )
        tool_result = {
            "probe_token": probe_token,
            "status": "ok",
        }
        final_response = ai_direct_request_service.request_for_probe(
            app_dir=app_dir,
            binding=binding,
            messages=[
                *messages,
                response,
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="noop",
                            content=tool_result,
                            tool_call_id=tool_call.tool_call_id,
                        )
                    ]
                ),
            ],
            function_tools=[tool],
            allow_text_output=True,
        )
        if (final_response.text or "").strip() != expected_final:
            raise ai_gateway_probe.CapabilityProbeUnsupported(
                "模型未能消费工具结果并完成 Function Call 往返。"
            )
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy="function_tool_round_trip",
        )
    if capability == ai_model_config.CAP_IMAGE_GENERATE:
        prompt = ai_gateway_probe._probe_image_prompt(
            options,
            "single small blue square",
        )
        results = ai_direct_request_service.generate_images_for_probe(
            app_dir=app_dir,
            binding=binding,
            prompt=prompt,
        )
        _validate_api_image_results(results)
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy="pydantic_image_generate",
        )
    if capability == ai_model_config.CAP_IMAGE_EDIT:
        source = _image_edit_probe_bytes()
        prompt = ai_gateway_probe._probe_image_prompt(
            options,
            "Change the red image to blue while preserving its dimensions.",
        )
        results = ai_direct_request_service.edit_image_for_probe(
            app_dir=app_dir,
            binding=binding,
            prompt=prompt,
            source=source,
        )
        _validate_api_image_results(results, source=source)
        return ai_gateway_probe.build_capability_profile(
            model,
            capability,
            strategy="pydantic_image_edit",
        )
    raise ai_gateway_probe.CapabilityProbeUnavailable(
        f"API Provider 尚未接入 {capability} 能力探测。"
    )


def probe_model_capabilities(
    model: dict[str, Any],
    api_key: str,
    model_name: str,
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
    *,
    app_dir: Path | str = ".",
) -> dict[str, Any]:
    """稳定公开签名；认证和模型名以规范化 model 配置为唯一真实来源。"""

    del api_key, model_name
    return ai_gateway_probe.run_capability_probes(
        ApiModelCapabilityProbeProvider(),
        ai_gateway_probe.CapabilityProbeContext(
            model=model,
            app_dir=app_dir,
            timeout=timeout,
            probe_options=probe_options,
        ),
        capabilities,
    )


class ApiModelCapabilityProbeProvider:
    """API 模型的统一能力探测 adapter。"""

    def probe_capability(
        self,
        capability: str,
        context: ai_gateway_probe.CapabilityProbeContext,
    ) -> dict[str, Any]:
        return _probe_capability(context)


def test_api_model(
    app_dir: Path | str,
    model: dict[str, Any],
    raw_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = raw_model if isinstance(raw_model, dict) else {}
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise RuntimeError("请先填写 API Key。")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise RuntimeError("请先填写 Base URL。")
    model_name = ai_model_config.model_name(model)
    timeout = int(model.get("timeout_seconds") or 60)
    available_models: list[dict[str, str]] = []
    discovery_error = ""
    discovery_succeeded = False
    try:
        available_models = list_remote_models(
            str(model.get("provider_id") or ""),
            base_url,
            api_key,
            timeout,
        )
        discovery_succeeded = True
    except AiModelDiscoveryError as exc:
        discovery_error = str(exc)
    probe_requested = raw.get("probe_capabilities", True) is not False
    options = ai_gateway_probe._probe_options(raw)
    requested = options["capabilities"] or ai_model_config.normalize_capabilities(
        model.get("capabilities")
    )
    capability_probe = ai_gateway_probe.empty_capability_probe_report()
    if probe_requested:
        if not model_name:
            raise RuntimeError("请先选择模型。")
        capability_probe = probe_model_capabilities(
            model,
            api_key,
            model_name,
            requested,
            timeout,
            options,
            app_dir=app_dir,
        )
    elif discovery_error:
        raise RuntimeError(discovery_error)
    connection_ok = discovery_succeeded or bool(
        capability_probe["supported"] or capability_probe["unsupported"]
    )
    connection_message = (
        f"{model.get('name') or model.get('id')} 测试成功：推理接口可以连接。"
        if connection_ok
        else f"{model.get('name') or model.get('id')} 尚未确认推理接口连接。"
    )
    if discovery_error and connection_ok:
        connection_message += " 当前 Provider 未提供可用的模型目录。"
    outcome = ai_gateway_probe._capability_test_outcome(
        capability_probe,
        probe_requested,
        connection_ok,
        connection_message,
        "可以保存配置并继续使用 AI 功能。",
    )
    return {
        **outcome,
        "channel": "ai_model",
        "model_id": model.get("id"),
        "provider_id": model.get("provider_id"),
        "provider": model.get("provider"),
        "connection_type": ai_model_config.CONNECTION_TYPE_API,
        "api_style": ai_model_config.normalize_api_style(model.get("api_style")),
        "model": model_name,
        "available_models": available_models,
        "model_discovery_error": discovery_error,
        "supported_capabilities": capability_probe["supported"],
        "unsupported_capabilities": capability_probe["unsupported"],
        "unavailable_capabilities": capability_probe["unavailable"],
        "inconclusive_capabilities": capability_probe["inconclusive"],
        "capability_results": capability_probe["results"],
        "tested_capabilities": requested,
        "test_trigger": str(raw.get("test_trigger") or "").strip(),
        "masked_key": ai_model_config.mask_secret(api_key),
    }


__all__ = [
    "ApiModelCapabilityProbeProvider",
    "probe_model_capabilities",
    "test_api_model",
]
