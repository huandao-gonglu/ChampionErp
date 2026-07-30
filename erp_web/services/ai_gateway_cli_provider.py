"""Codex CLI AI Provider 与本地 CLI 传输实现。"""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Callable

from . import ai_gateway_probe as probe_runtime
from . import ai_model_config, ai_work_service
from .ai_gateway_parsing import _sanitize_cli_error, parse_json_text
from .ai_gateway_provider_profiles import non_http_capability_profile
from .ai_gateway_provider_prompting import _cli_prompt
from .ai_gateway_provider_types import AiChatRequest
from .ai_provider_contracts import CAPABILITY_CHAT_JSON, AiChatProvider

_probe_options = probe_runtime._probe_options
_capability_test_outcome = probe_runtime._capability_test_outcome

def _cli_command_parts(command: str) -> list[str]:
    parts = shlex.split(str(command or "").strip())
    if not parts:
        raise RuntimeError("请先填写 CLI 命令路径。")
    return parts

def _codex_cli_args(app_dir: Path | str, model: dict[str, Any], output_path: str) -> list[str]:
    command = ai_model_config.model_cli_command(model)
    args = _cli_command_parts(command)
    sandbox = str(model.get("sandbox") or ai_model_config.CLI_DEFAULT_SANDBOX).strip() or ai_model_config.CLI_DEFAULT_SANDBOX
    profile = str(model.get("profile") or "").strip()
    model_name = ai_model_config.model_name(model)
    args.extend(["exec", "--color", "never", "--ephemeral", "--skip-git-repo-check", "-C", str(app_dir), "--sandbox", sandbox])
    if profile:
        args.extend(["-p", profile])
    if model_name:
        args.extend(["-m", model_name])
    args.extend(["-o", output_path, "-"])
    return args

def _run_codex_cli_text(
    app_dir: Path | str,
    model: dict[str, Any],
    prompt: str,
    timeout: int,
) -> str:
    command = ai_model_config.model_cli_command(model)
    executable = _cli_command_parts(command)[0]
    if not shutil.which(executable):
        raise RuntimeError(f"未找到本地 CLI 命令：{executable}。请先安装，或填写完整命令路径。")
    with tempfile.NamedTemporaryFile(prefix="champion_erp_codex_", suffix=".txt", delete=True) as output_file:
        args = _codex_cli_args(app_dir, model, output_file.name)
        try:
            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(app_dir),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex CLI 调用超时（{timeout} 秒）。") from exc
        if completed.returncode != 0:
            detail = _sanitize_cli_error(completed.stderr or completed.stdout)
            model_name = ai_model_config.model_name(model)
            if model_name and re.search(r"model .*not supported|model metadata .*not found", detail, flags=re.IGNORECASE):
                raise RuntimeError(
                    f"Codex CLI 模型 {model_name} 不可用。请清空 CLI 模型字段使用本机 Codex 默认模型，"
                    "或填写 Codex CLI 支持的模型名；不要沿用 DeepSeek/OpenAI-Compatible 的 API 模型名。"
                )
            hint = "请在终端运行 codex login 或 codex doctor 检查本机 Codex 状态。"
            raise RuntimeError(f"Codex CLI 调用失败：{detail or '命令返回非 0 状态'}。{hint}")
        output_file.seek(0)
        final_text = output_file.read().decode("utf-8", errors="replace").strip()
    return final_text or str(completed.stdout or "").strip()

def _chat_json_via_cli(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    timeout_seconds: int | None = None,
    required_capabilities: tuple[str, ...] = (),
    response_format: bool = True,
    stream: bool = False,
    token_callback: Callable[[str], None] | None = None,
    conversation: ai_work_service.AiWorkConversation | None = None,
) -> dict[str, Any]:
    cli_tool = ai_model_config.model_cli_tool(model)
    if cli_tool != ai_model_config.CLI_TOOL_CODEX:
        raise RuntimeError(f"CLI 工具 {cli_tool} 已预留，但当前版本只支持 Codex CLI。")
    timeout = int(timeout_seconds or model.get("timeout_seconds") or 180)
    allow_external_read = ai_model_config.CAP_WEB_SEARCH in required_capabilities
    prompt = _cli_prompt(messages, response_format=response_format, allow_external_read=allow_external_read)
    if conversation:
        conversation.emit_custom(
            "provider.request",
            {
                "command": ai_model_config.model_cli_command(model),
                "messages": messages,
                "provider_payload": {"prompt": prompt, "stream": bool(stream)},
            },
        )
    text = _run_codex_cli_text(app_dir, model, prompt, timeout)
    if stream and token_callback and text:
        token_callback(text)
    if conversation:
        conversation.finish_assistant_message(text)
    return parse_json_text(text)

def _cli_json_probe(
    app_dir: Path | str,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    timeout: int,
    *,
    allow_external_read: bool = False,
) -> dict[str, Any]:
    text = _run_codex_cli_text(
        app_dir,
        model,
        _cli_prompt(messages, response_format=True, allow_external_read=allow_external_read),
        timeout,
    )
    return parse_json_text(text)

def probe_cli_model_capabilities(
    app_dir: Path | str,
    model: dict[str, Any],
    capabilities: list[str],
    timeout: int,
    probe_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested = ai_model_config.normalize_capabilities(capabilities)
    cli_tool = ai_model_config.model_cli_tool(model)
    if cli_tool != ai_model_config.CLI_TOOL_CODEX:
        error = f"CLI 工具 {cli_tool} 已预留，但当前版本只支持 Codex CLI。"
        return {
            "supported": [],
            "unsupported": requested,
            "results": {
                capability: {"ok": False, "error": error}
                for capability in requested
            },
        }
    return probe_runtime.run_capability_probes(
        CodexCliProvider(),
        probe_runtime.CapabilityProbeContext(
            model=model,
            app_dir=app_dir,
            timeout=timeout,
            probe_options=probe_options,
        ),
        requested,
    )

class CodexCliProvider(AiChatProvider):
    provider_id = "codex_cli"
    probe_reraise_marker = "Codex CLI 模型"
    probe_web_search_meta = False

    def supports(self, model: dict[str, Any], capability: str = CAPABILITY_CHAT_JSON) -> bool:
        return (
            capability == CAPABILITY_CHAT_JSON
            and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_CLI
            and ai_model_config.model_cli_tool(model) == ai_model_config.CLI_TOOL_CODEX
        )

    def chat_json(self, request: AiChatRequest) -> dict[str, Any]:
        return _chat_json_via_cli(
            request.app_dir,
            request.model,
            request.messages,
            timeout_seconds=request.timeout_seconds,
            required_capabilities=request.required_capabilities,
            response_format=request.response_format,
            stream=request.stream,
            token_callback=request.emit_delta,
            conversation=request.conversation,
        )

    def _probe_chat(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        text = _run_codex_cli_text(
            context.app_dir or ".",
            context.model,
            _cli_prompt(messages, response_format=False),
            context.timeout,
        )
        if not text:
            raise RuntimeError("CLI did not return any text.")

    def _probe_json(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        _cli_json_probe(
            context.app_dir or ".",
            context.model,
            messages,
            context.timeout,
        )

    def _probe_web_search(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        data = _cli_json_probe(
            context.app_dir or ".",
            context.model,
            messages,
            context.timeout,
            allow_external_read=True,
        )
        probe_runtime._validate_web_search_probe_data(data)

    def _probe_image_generate(
        self,
        context: probe_runtime.CapabilityProbeContext,
        messages: list[dict[str, str]],
    ) -> None:
        app_dir = context.app_dir or "."
        text = _run_codex_cli_text(
            app_dir,
            context.model,
            _cli_prompt(
                messages,
                response_format=True,
                allow_generated_artifacts=True,
            ),
            context.timeout,
        )
        data = probe_runtime._cli_image_probe_data_from_text(text)
        probe_runtime._validate_cli_image_generate_probe(data, app_dir)

    def probe_capability(
        self,
        capability: str,
        context: probe_runtime.CapabilityProbeContext,
    ) -> dict[str, Any]:
        options = context.probe_options
        messages = probe_runtime._probe_messages
        if capability == ai_model_config.CAP_CHAT:
            self._probe_chat(
                context,
                messages(options, probe_runtime._chat_probe_default_messages()),
            )
        elif capability == ai_model_config.CAP_JSON:
            self._probe_json(
                context,
                messages(options, probe_runtime._json_probe_default_messages()),
            )
        elif capability == ai_model_config.CAP_WEB_SEARCH:
            self._probe_web_search(
                context,
                messages(options, probe_runtime._web_search_probe_prompt()),
            )
        elif capability == ai_model_config.CAP_IMAGE_GENERATE:
            self._probe_image_generate(
                context,
                messages(options, probe_runtime._cli_image_generate_probe_prompt()),
            )
        else:
            raise RuntimeError(
                "CLI Provider 当前仅支持 chat/json/web_search/image_generate 能力测试。"
            )
        return non_http_capability_profile(
            context.model,
            capability,
            channel="cli",
            tested=True,
        )

    def test_model(self, app_dir: Path | str, model: dict[str, Any], raw_model: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = raw_model if isinstance(raw_model, dict) else {}
        timeout = int(model.get("timeout_seconds") or 180)
        probe_options = _probe_options(raw)
        requested_capabilities = probe_options["capabilities"] or ai_model_config.normalize_capabilities(model.get("capabilities"))
        probe_requested = raw.get("probe_capabilities", True) is not False
        capability_probe = {"supported": [], "unsupported": [], "results": {}}
        if probe_requested:
            capability_probe = probe_cli_model_capabilities(app_dir, model, requested_capabilities, timeout, probe_options)
        command = ai_model_config.model_cli_command(model)
        executable = _cli_command_parts(command)[0] if command else ""
        installed_path = shutil.which(executable) if executable else ""
        if not installed_path:
            raise RuntimeError(f"未找到本地 CLI 命令：{executable or command}。请先安装，或填写完整命令路径。")
        outcome = _capability_test_outcome(
            capability_probe,
            probe_requested,
            True,
            f"{model.get('name') or model.get('id')} 测试成功：本地 CLI 可以调用。",
            "可以保存配置并继续使用 AI 功能；登录和账号状态由本机 CLI 自己管理。",
        )
        return {
            **outcome,
            "channel": "ai_model",
            "model_id": model.get("id"),
            "provider": model.get("provider"),
            "connection_type": ai_model_config.CONNECTION_TYPE_CLI,
            "cli_tool": ai_model_config.model_cli_tool(model),
            "command": command,
            "command_path": installed_path,
            "model": ai_model_config.model_name(model),
            "available_models": ([{"id": ai_model_config.model_name(model), "label": ai_model_config.model_name(model)}] if ai_model_config.model_name(model) else []),
            "supported_capabilities": capability_probe["supported"],
            "capability_results": capability_probe["results"],
            "tested_capabilities": requested_capabilities,
            "test_trigger": str(raw.get("test_trigger") or "").strip(),
        }

__all__ = [
    "CodexCliProvider",
    "_chat_json_via_cli",
    "_cli_command_parts",
    "_cli_json_probe",
    "_codex_cli_args",
    "_run_codex_cli_text",
    "probe_cli_model_capabilities",
]
