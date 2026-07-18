from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import urllib.error

from conftest import assert_no_old_path
from erp_web import runtime as erp_web_app
import erp_web.runtime as erp_runtime
from erp_web.services import ai_gateway, ai_model_config, browser_ai_runtime, copy_service


def test_generate_copy_without_api_key_returns_warning(app_dir: Path) -> None:
    product = {
        "name": "Manual test organizer",
        "materials": ["PP"],
        "selling_points": ["Foldable", "For home storage"],
        "source_text": "Use for organizing household items.",
    }
    result = copy_service.generate_copy(
        str(app_dir),
        product,
        {
            "ai_models": [
                {
                    "id": "copy_model",
                    "provider": "DeepSeek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
        target_market="mercadolibre",
        language="Spanish (Mexico)",
    )

    assert result["ok"] is True
    assert result["warning"]
    assert "API Key" in result["warning"]
    assert result["target_market"] == "mercadolibre"
    assert result["language"] == "Spanish (Mexico)"
    assert result["provider"] == "DeepSeek"
    assert result["copy"]["title"]


def test_generate_copy_defaults_ozon_to_russian(app_dir: Path) -> None:
    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "Manual organizer"},
        {"ai_models": []},
        target_market="ozon",
    )

    assert result["language"] == "Russian"


def test_prompt_contains_manual_product_data_and_mexico_spanish() -> None:
    prompt = copy_service.build_copy_prompt(
        {
            "name": "Manual organizer",
            "materials": ["PP"],
            "selling_points": ["For kitchen", "Reusable"],
            "source_text": "Manual text only, no crawler required.",
        },
        "mercadolibre",
        "Spanish (Mexico)",
        "rewrite",
    )

    assert "Spanish (Mexico)" in prompt
    assert "Mercado Libre Mexico" in prompt
    assert "Manual organizer" in prompt
    assert "Manual text only" in prompt


def test_copy_service_does_not_hardcode_keys(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    source = (app_dir / "erp_web" / "services" / "copy_service.py").read_text(encoding="utf-8", errors="ignore")
    assert "sk-" not in source
    assert_no_old_path(source, old_path_markers)


def test_ai_model_connection_uses_model_config(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"deepseek-chat"},{"id":"deepseek-reasoner"}]}'

    def fake_urlopen(request, timeout):
        calls.append({
            "url": request.full_url,
            "auth": request.get_header("Authorization"),
            "ua": request.get_header("User-agent") or request.get_header("User-Agent"),
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
            "probe_capabilities": False,
        },
    )

    assert result["ok"] is True
    assert result["available_models"] == [
        {"id": "deepseek-chat", "label": "deepseek-chat"},
        {"id": "deepseek-reasoner", "label": "deepseek-reasoner"},
    ]
    assert calls == [{
        "url": "https://api.deepseek.com/models",
        "auth": "Bearer test-key",
        "ua": ai_model_config.AI_HTTP_USER_AGENT,
        "timeout": 60,
    }]


def test_ai_model_connection_uses_saved_key_when_public_payload_omits_secret(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"deepseek-chat"}]}'

    def fake_urlopen(request, timeout):
        calls.append({
            "url": request.full_url,
            "auth": request.get_header("Authorization"),
            "ua": request.get_header("User-agent") or request.get_header("User-Agent"),
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(
        erp_runtime,
        "load_app_config",
        lambda: {
            "ai_models": [
                {
                    "id": "copy_model",
                    "name": "Copy Model",
                    "provider": "DeepSeek",
                    "api_key": "saved-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
    )
    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "",
            "api_key_configured": True,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
            "probe_capabilities": False,
        },
    )

    assert result["ok"] is True
    assert calls == [{
        "url": "https://api.deepseek.com/models",
        "auth": "Bearer saved-key",
        "ua": ai_model_config.AI_HTTP_USER_AGENT,
        "timeout": 60,
    }]


def test_ai_model_connection_copies_saved_key_from_source_model(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"data":[{"id":"deepseek-reasoner"}]}'

    def fake_urlopen(request, timeout):
        calls.append({
            "url": request.full_url,
            "auth": request.get_header("Authorization"),
            "ua": request.get_header("User-agent") or request.get_header("User-Agent"),
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(
        erp_runtime,
        "load_app_config",
        lambda: {
            "ai_models": [
                {
                    "id": "copy_model",
                    "name": "Copy Model",
                    "provider": "DeepSeek",
                    "api_key": "saved-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
    )
    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model_copy",
            "copy_source_id": "copy_model",
            "name": "Copy Model 副本",
            "provider": "DeepSeek",
            "api_key": "",
            "api_key_configured": True,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-reasoner",
            "capabilities": ["chat", "json"],
            "probe_capabilities": False,
        },
    )

    assert result["ok"] is True
    assert calls == [{
        "url": "https://api.deepseek.com/models",
        "auth": "Bearer saved-key",
        "ua": ai_model_config.AI_HTTP_USER_AGENT,
        "timeout": 60,
    }]


def test_codex_cli_chat_json_uses_local_command(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        output_path = Path(args[args.index("-o") + 1])
        output_path.write_text('{"ok":true,"title":"Codex OK"}', encoding="utf-8")
        calls.append(
            {
                "args": args,
                "input": input,
                "text": text,
                "capture_output": capture_output,
                "cwd": cwd,
                "timeout": timeout,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    result = ai_gateway.chat_json(
        tmp_path,
        {
            "ai_models": [
                {
                    "id": "codex_cli_text",
                    "name": "Codex CLI 文本模型",
                    "connection_type": "cli",
                    "provider": "Codex CLI",
                    "cli_tool": "codex",
                    "command": "codex",
                    "model": "gpt-5-codex",
                    "capabilities": ["chat", "json"],
                    "timeout_seconds": "180",
                }
            ]
        },
        "copy.generate",
        [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Return title."},
        ],
    )

    assert result == {"ok": True, "title": "Codex OK"}
    assert calls
    args = calls[0]["args"]
    assert args[:2] == ["codex", "exec"]
    assert ["--sandbox", "read-only"] == args[args.index("--sandbox") : args.index("--sandbox") + 2]
    assert ["-m", "gpt-5-codex"] == args[args.index("-m") : args.index("-m") + 2]
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[0]["timeout"] == 180
    assert "最终输出必须是一个合法 JSON 对象" in calls[0]["input"]
    assert "[System]" not in calls[0]["input"]
    assert "[User]" not in calls[0]["input"]


def test_ai_model_connection_uses_codex_cli_without_api_credentials(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        output_path = Path(args[args.index("-o") + 1])
        output_path.write_text('{"ok":true}', encoding="utf-8")
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    result = ai_gateway.test_ai_model(
        tmp_path,
        {
            "id": "codex_cli_text",
            "name": "Codex CLI 文本模型",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "codex",
            "model": "",
            "capabilities": ["chat", "json"],
            "timeout_seconds": "180",
        },
    )

    assert result["ok"] is True
    assert result["connection_type"] == "cli"
    assert result["command_path"] == "/usr/local/bin/codex"
    assert result["supported_capabilities"] == ["chat", "json"]
    assert calls and calls[0][:2] == ["codex", "exec"]


def test_codex_cli_probe_supports_web_search_and_image_generate(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    probe_date = ai_gateway._web_search_probe_date_iso()

    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        output_path = Path(args[args.index("-o") + 1])
        calls.append(input)
        if "成都" in input:
            output_path.write_text(
                json.dumps(
                    {
                        "can_access_web": True,
                        "source_url": "https://weather.com/weather/today/l/Chengdu",
                        "location": "成都",
                        "date": probe_date,
                        "weather": "多云",
                        "temperature": "26°C",
                        "evidence": "Chengdu current weather was checked during the probe.",
                    }
                ),
                encoding="utf-8",
            )
        elif "single blue square" in input:
            generated_path = tmp_path / "blue-square.png"
            generated_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
            output_path.write_text(f"Generated Image:\nSaved to:\nfile://{generated_path}", encoding="utf-8")
        else:
            output_path.write_text('{"ok":true}', encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    result = ai_gateway.test_ai_model(
        tmp_path,
        {
            "id": "codex_cli_text",
            "name": "Codex CLI 文本模型",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "codex",
            "model": "",
            "capabilities": ["chat", "json", "web_search", "image_generate"],
            "timeout_seconds": "180",
        },
    )

    assert result["ok"] is True
    assert result["supported_capabilities"] == ["chat", "json", "web_search", "image_generate"]
    assert result["capability_results"]["web_search"]["ok"] is True
    assert result["capability_results"]["image_generate"]["ok"] is True
    assert len(calls) == 4
    assert all("适配器" not in call and "verifying" not in call.lower() for call in calls)
    assert all("[System]" not in call and "[User]" not in call for call in calls)


def test_codex_cli_probe_reports_web_search_and_image_generate_failures(tmp_path: Path, monkeypatch) -> None:
    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        output_path = Path(args[args.index("-o") + 1])
        if "成都" in input:
            output_path.write_text('{"can_access_web":false,"reason":"No live web/search tool is available."}', encoding="utf-8")
        elif "single blue square" in input:
            output_path.write_text('{"can_generate_image":false,"reason":"No image generation tool is available."}', encoding="utf-8")
        else:
            output_path.write_text('{"ok":true}', encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    result = ai_gateway.test_ai_model(
        tmp_path,
        {
            "id": "codex_cli_text",
            "name": "Codex CLI 文本模型",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "codex",
            "model": "",
            "capabilities": ["chat", "json", "web_search", "image_generate"],
            "timeout_seconds": "180",
        },
    )

    assert result["ok"] is True
    assert result["supported_capabilities"] == ["chat", "json"]
    assert result["capability_results"]["web_search"]["ok"] is False
    assert result["capability_results"]["image_generate"]["ok"] is False
    assert "No live web/search tool" in result["capability_results"]["web_search"]["error"]
    assert "No image generation tool" in result["capability_results"]["image_generate"]["error"]


def test_codex_cli_probe_only_capability_runs_single_probe(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    probe_date = ai_gateway._web_search_probe_date_iso()

    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        output_path = Path(args[args.index("-o") + 1])
        calls.append(input)
        output_path.write_text(
            json.dumps(
                {
                    "can_access_web": True,
                    "source_url": "https://weather.com/weather/today/l/Chengdu",
                    "location": "成都",
                    "date": probe_date,
                    "weather": "多云",
                    "temperature": "26°C",
                    "evidence": "Chengdu current weather was checked during the probe.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    result = ai_gateway.test_ai_model(
        tmp_path,
        {
            "id": "codex_cli_text",
            "connection_type": "cli",
            "provider": "Codex CLI",
            "cli_tool": "codex",
            "command": "codex",
            "capabilities": ["chat", "json", "web_search", "image_generate"],
            "probe_only_capability": "web_search",
            "probe_capabilities": True,
            "test_trigger": "capability_checkbox",
        },
    )

    assert result["supported_capabilities"] == ["web_search"]
    assert result["tested_capabilities"] == ["web_search"]
    assert result["test_trigger"] == "capability_checkbox"
    assert len(calls) == 1
    assert "成都" in calls[0]
    assert probe_date in calls[0]
    assert "[System]" not in calls[0]
    assert "[User]" not in calls[0]


def test_ai_provider_registry_exposes_typed_provider_classes() -> None:
    assert issubclass(ai_gateway.OpenAICompatibleProvider, ai_gateway.AiProvider)
    assert issubclass(ai_gateway.OpenAIResponsesProvider, ai_gateway.AiProvider)
    assert issubclass(ai_gateway.CodexCliProvider, ai_gateway.AiProvider)
    assert issubclass(ai_gateway.BrowserAiProvider, ai_gateway.AiProvider)
    assert all(isinstance(provider, ai_gateway.AiProvider) for provider in ai_gateway.AI_PROVIDER_REGISTRY)

    provider_ids = [provider.provider_id for provider in ai_gateway.AI_PROVIDER_REGISTRY]
    assert provider_ids == ["codex_cli", "browser", "openai_responses", "openai_compatible"]


def test_browser_ai_provider_uses_browser_runtime(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_open_page(app_dir, model, timeout=30):
        calls.append({"fn": "open", "app_dir": app_dir, "model": model, "timeout": timeout})
        return browser_ai_runtime.BrowserAiRunResult(
            text="ready",
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile" / "ai" / "chatgpt" / "default"),
            port=9333,
            ready=True,
            title="ChatGPT",
            page_url="https://chatgpt.com/",
            message="ready",
        )

    def fake_run_chat(app_dir, model, prompt, timeout=180):
        calls.append({"fn": "chat", "app_dir": app_dir, "model": model, "prompt": prompt, "timeout": timeout})
        return browser_ai_runtime.BrowserAiRunResult(
            text='{"ok":true,"title":"Browser OK"}',
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile" / "ai" / "chatgpt" / "default"),
            port=9333,
            ready=True,
        )

    monkeypatch.setattr(ai_gateway.browser_ai_runtime, "open_browser_ai_page", fake_open_page)
    monkeypatch.setattr(ai_gateway.browser_ai_runtime, "run_browser_ai_chat", fake_run_chat)

    browser_model = ai_model_config.normalize_ai_model(
        {
            "id": "browser_text",
            "name": "浏览器文本模型",
            "connection_type": "browser",
            "browser_provider": "chatgpt",
            "capabilities": ["chat", "json", "web_search"],
        }
    )
    provider = ai_gateway._provider_for_model(browser_model)

    assert isinstance(provider, ai_gateway.BrowserAiProvider)
    connection = ai_gateway.test_ai_model(tmp_path, {**browser_model, "probe_capabilities": False})
    assert connection["ok"] is True
    assert connection["connection_type"] == "browser"
    assert connection["browser_provider"] == "chatgpt"
    assert connection["profile_dir"].endswith("browser_profile/ai/chatgpt/default")

    parsed = ai_gateway.chat_json(
        tmp_path,
        {"ai_models": [{**browser_model, "enabled": True}]},
        "copy.generate",
        [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Return title."},
        ],
    )
    assert parsed == {"ok": True, "title": "Browser OK"}
    assert [call["fn"] for call in calls] == ["open", "chat"]
    assert "最终输出必须是一个合法 JSON 对象" in calls[-1]["prompt"]
    assert "[System]" not in calls[-1]["prompt"]
    assert "[User]" not in calls[-1]["prompt"]


def test_browser_ai_probe_only_capability_runs_single_browser_message(tmp_path: Path, monkeypatch) -> None:
    prompts: list[str] = []
    probe_date = ai_gateway._web_search_probe_date_iso()

    def fake_open_page(app_dir, model, timeout=30):
        return browser_ai_runtime.BrowserAiRunResult(
            text="ready",
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile" / "ai" / "chatgpt" / "default"),
            port=9333,
            ready=True,
        )

    def fake_run_chat(app_dir, model, prompt, timeout=180):
        prompts.append(prompt)
        return browser_ai_runtime.BrowserAiRunResult(
            text=json.dumps(
                {
                    "can_access_web": True,
                    "source_url": "https://weather.com/weather/today/l/Chengdu",
                    "location": "成都",
                    "date": probe_date,
                    "weather": "多云",
                    "temperature": "26°C",
                    "evidence": "Chengdu current weather was checked now.",
                }
            ),
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile" / "ai" / "chatgpt" / "default"),
            port=9333,
            ready=True,
        )

    monkeypatch.setattr(ai_gateway.browser_ai_runtime, "open_browser_ai_page", fake_open_page)
    monkeypatch.setattr(ai_gateway.browser_ai_runtime, "run_browser_ai_chat", fake_run_chat)

    result = ai_gateway.test_ai_model(
        tmp_path,
        {
            "id": "browser_text",
            "connection_type": "browser",
            "browser_provider": "chatgpt",
            "capabilities": ["chat", "json", "web_search"],
            "probe_only_capability": "web_search",
            "probe_capabilities": True,
            "test_trigger": "capability_checkbox",
        },
    )

    assert result["supported_capabilities"] == ["web_search"]
    assert result["tested_capabilities"] == ["web_search"]
    assert result["test_trigger"] == "capability_checkbox"
    assert len(prompts) == 1
    assert "成都" in prompts[0]
    assert probe_date in prompts[0]
    assert "实时验证" in prompts[0] or "live web" in prompts[0].lower()
    assert "[System]" not in prompts[0]
    assert "[User]" not in prompts[0]


def test_codex_cli_reports_api_model_name_as_configuration_error(tmp_path: Path, monkeypatch) -> None:
    def fake_which(command: str) -> str:
        return f"/usr/local/bin/{command}" if command == "codex" else ""

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=(
                "warning: Model metadata for `deepseek-chat` not found. "
                "ERROR: {\"message\":\"The 'deepseek-chat' model is not supported when using Codex with a ChatGPT account.\"}"
            ),
        )

    monkeypatch.setattr(ai_gateway.shutil, "which", fake_which)
    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)

    try:
        ai_gateway.test_ai_model(
            tmp_path,
            {
                "id": "codex_cli_text",
                "connection_type": "cli",
                "provider": "Codex CLI",
                "cli_tool": "codex",
                "command": "codex",
                "model": "deepseek-chat",
                "capabilities": ["chat", "json"],
            },
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected Codex CLI unsupported model error")

    assert "Codex CLI 模型 deepseek-chat 不可用" in message
    assert "请清空 CLI 模型字段" in message


def test_ai_model_probe_reports_capability_failures(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request, timeout):
        calls.append({"url": request.full_url, "timeout": timeout})
        if request.full_url.endswith("/models"):
            return FakeResponse(b'{"data":[{"id":"deepseek-chat"}]}')
        body = json.loads((request.data or b"{}").decode("utf-8"))
        if body.get("response_format"):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b"unsupported response_format"),
            )
        return FakeResponse(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "copy_model",
            "name": "Copy Model",
            "provider": "DeepSeek",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capabilities": ["chat", "json"],
        },
    )

    assert result["ok"] is True
    assert result["supported_capabilities"] == ["chat"]
    assert result["capability_results"]["json"]["ok"] is False
    assert calls == [
        {"url": "https://api.deepseek.com/models", "timeout": 60},
        {"url": "https://api.deepseek.com/chat/completions", "timeout": 60},
        {"url": "https://api.deepseek.com/chat/completions", "timeout": 60},
    ]


def test_ai_model_probe_requires_real_web_search_evidence(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/models"):
            return FakeResponse(b'{"data":[{"id":"gpt-web"}]}')
        body = json.loads((request.data or b"{}").decode("utf-8"))
        if body.get("web_search_options"):
            return FakeResponse(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "can_access_web": False,
                                            "reason": "No live web/search tool is available.",
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
            )
        return FakeResponse(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(ai_gateway.urllib.request, "urlopen", fake_urlopen)
    result = erp_web_app.test_ai_model_config(
        {
            "id": "web_model",
            "name": "Web Model",
            "provider": "OpenAI-Compatible",
            "api_key": "test-key",
            "base_url": "https://ai.example.com/v1",
            "model": "gpt-web",
            "capabilities": ["chat", "web_search"],
        },
    )

    assert result["ok"] is True
    assert "chat" in result["supported_capabilities"]
    assert result["capability_results"]["web_search"]["ok"] is False
    assert "No live web/search tool" in result["capability_results"]["web_search"]["error"]


def test_assign_upc_writes_current_product_and_returns_full_payload(tmp_path: Path) -> None:
    original = {
        "APP_DIR": erp_web_app.APP_DIR,
        "DATA_DIR": erp_web_app.DATA_DIR,
        "CONFIG_DIR": erp_web_app.CONFIG_DIR,
        "APP_CONFIG_PATH": erp_web_app.APP_CONFIG_PATH,
        "LEGACY_APP_CONFIG_PATHS": erp_web_app.LEGACY_APP_CONFIG_PATHS,
    }
    try:
        erp_web_app.APP_DIR = tmp_path
        erp_web_app.DATA_DIR = tmp_path / "data"
        erp_web_app.CONFIG_DIR = tmp_path / "config"
        erp_web_app.APP_CONFIG_PATH = erp_web_app.CONFIG_DIR / "app_config.json"
        erp_web_app.LEGACY_APP_CONFIG_PATHS = (tmp_path / "app_config.json", tmp_path / "dist" / "app_config.json")
        (tmp_path / "upc_pool.json").write_text('{"values":["725272000007"],"used":[]}', encoding="utf-8")
        erp_web_app.save_product({"name": "UPC test product", "drafts": {"mercadolibre": {"enabled": True}}})

        result = erp_web_app.assign_upc()

        assert result["ok"] is True
        assert result["upc"] == "725272000007"
        assert result["product"]["upc"] == "725272000007"
        assert result["product"]["drafts"]["mercadolibre"]["upc"] == "725272000007"
        assert isinstance(result["productsIndex"], list)
    finally:
        for name, value in original.items():
            setattr(erp_web_app, name, value)
