from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import assert_no_old_path
from erp_web.context import get_context
from erp_web.runtime_units import publish_helpers
from erp_web.services import (
    ai_gateway,
    ai_gateway_providers,
    ai_model_config,
    browser_ai_runtime,
    copy_service,
)
from tests.runtime_test_utils import temp_app_context


def _api_model() -> dict[str, object]:
    return {
        "id": "copy_model",
        "name": "Copy Model",
        "connection_type": "api",
        "provider": "OpenAI",
        "provider_id": "openai",
        "api_style": "openai_compatible",
        "api_key": "test-key",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-test",
        "capabilities": ["chat", "json"],
        "enabled": True,
    }


def test_generate_copy_without_api_key_does_not_create_fallback_copy(
    app_dir: Path,
) -> None:
    result = copy_service.generate_copy(
        str(app_dir),
        {
            "name": "Manual test organizer",
            "materials": ["PP"],
            "selling_points": ["Foldable"],
        },
        {
            "ai_models": [
                {
                    **_api_model(),
                    "api_key": "",
                    "api_key_env": "MISSING_TEST_API_KEY",
                }
            ]
        },
        target_market="mercadolibre",
        language="Spanish (Mexico)",
    )

    assert result["ok"] is False
    assert "API Key" in result["error"]
    assert result["copy"] == {}


def test_generate_copy_uses_bound_model_and_registry_language(
    app_dir: Path,
    monkeypatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_resolve(*args, **kwargs):
        seen["use_case"] = args[2]
        return {"id": "bound_copy_model", "provider": "Test Provider"}

    def fake_chat(*args, **kwargs):
        seen["messages"] = kwargs.get("messages") or args[3]
        return {
            "title": "Органайзер для дома",
            "description": "Компактный органайзер для хранения вещей дома.",
        }

    monkeypatch.setattr(copy_service.ai_gateway, "resolve_model_for_use_case", fake_resolve)
    monkeypatch.setattr(copy_service.ai_gateway, "chat_json", fake_chat)

    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "Manual organizer"},
        {"ai_models": []},
        target_market="ozon",
    )

    assert result["ok"] is True
    assert result["language"] == "ru-RU"
    assert result["provider"] == "Test Provider"
    assert result["copy"]["bullets"] == []
    assert seen["use_case"] == "copy.generate"


def test_generate_copy_rejects_overlong_title_instead_of_truncating(
    app_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        copy_service.ai_gateway,
        "resolve_model_for_use_case",
        lambda *_args, **_kwargs: {
            "id": "bound_copy_model",
            "provider": "Test Provider",
        },
    )
    overlong_title = "x" * 61
    monkeypatch.setattr(
        copy_service.ai_gateway,
        "chat_json",
        lambda *_args, **_kwargs: {
            "title": overlong_title,
            "description": "Description",
        },
    )

    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "Manual organizer"},
        {"ai_models": []},
        target_market="mercadolibre",
        language="en-US",
    )

    assert result["ok"] is False
    assert "超过 60 个字符" in result["error"]
    assert result["copy"] == {}


def test_configured_copy_prompt_contains_target_and_product_context(
    app_dir: Path,
) -> None:
    prompt = copy_service.build_copy_prompt_from_config(
        str(app_dir),
        {},
        {"name": "Manual organizer", "selling_points": ["Foldable"]},
        "ozon",
        "ru-RU",
        "rewrite",
    )

    assert "ru-RU" in prompt["user"]
    assert "Ozon" in prompt["user"]
    assert "Manual organizer" in prompt["user"]
    assert "{$" not in prompt["user"]


def test_copy_service_does_not_hardcode_keys(
    app_dir: Path,
    old_path_markers: tuple[str, ...],
) -> None:
    assert_no_old_path((app_dir / "erp_web/services/copy_service.py").read_text(), old_path_markers)


def test_api_chat_uses_pydantic_direct_boundary(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_direct_chat(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        fake_direct_chat,
    )

    result = ai_gateway.chat_json(
        tmp_path,
        {"ai_models": [_api_model()]},
        "copy.generate",
        [{"role": "user", "content": "Return JSON."}],
        temperature=0.35,
        max_tokens=128,
        stream=False,
    )

    assert result == {"ok": True}
    assert seen["use_case_id"] == "copy.generate"
    assert seen["temperature"] == 0.35
    assert seen["max_tokens"] == 128
    assert seen["required_capabilities"] == ("chat", "json")
    assert all(
        provider.provider_id != "pydantic_direct"
        for provider in ai_gateway.AI_PROVIDER_REGISTRY
    )


def test_api_chat_rejects_business_extra_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        lambda **kwargs: pytest.fail("非法 extra_body 不应到达 Pydantic 请求"),
    )

    with pytest.raises(ValueError, match="不允许业务层传入 extra_body"):
        ai_gateway.chat_json(
            tmp_path,
            {"ai_models": [_api_model()]},
            "copy.generate",
            [{"role": "user", "content": "Return JSON."}],
            extra_body={"tools": []},
            stream=False,
        )


def test_api_models_never_enter_external_provider_registry() -> None:
    normalized = ai_model_config.normalize_ai_model(_api_model())
    with pytest.raises(RuntimeError, match="Pydantic Direct Model"):
        ai_gateway._provider_for_model(normalized)
    assert {
        type(provider) for provider in ai_gateway.AI_PROVIDER_REGISTRY
    } == {ai_gateway.CodexCliProvider, ai_gateway.BrowserAiProvider}


def test_codex_cli_chat_json_uses_local_command(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        ai_gateway.shutil,
        "which",
        lambda command: f"/usr/local/bin/{command}" if command == "codex" else "",
    )

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        Path(args[args.index("-o") + 1]).write_text(
            '{"ok":true,"title":"Codex OK"}',
            encoding="utf-8",
        )
        calls.append({"args": args, "input": input, "cwd": cwd, "timeout": timeout})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)
    result = ai_gateway.chat_json(
        tmp_path,
        {
            "ai_models": [
                {
                    "id": "codex_cli_text",
                    "connection_type": "cli",
                    "provider": "Codex CLI",
                    "cli_tool": "codex",
                    "command": "codex",
                    "model": "gpt-5-codex",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
        stream=False,
    )

    assert result == {"ok": True, "title": "Codex OK"}
    assert calls[0]["args"][:2] == ["codex", "exec"]
    assert "最终输出必须是一个合法 JSON 对象" in calls[0]["input"]


def test_browser_ai_provider_uses_browser_runtime(tmp_path: Path, monkeypatch) -> None:
    prompts: list[str] = []

    def fake_run_chat(app_dir, model, prompt, timeout=180):
        prompts.append(prompt)
        return browser_ai_runtime.BrowserAiRunResult(
            text='{"ok":true,"title":"Browser OK"}',
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile"),
            port=9333,
            ready=True,
        )

    monkeypatch.setattr(
        ai_gateway.browser_ai_runtime,
        "run_browser_ai_chat",
        fake_run_chat,
    )
    browser_model = {
        "id": "browser_text",
        "connection_type": "browser",
        "provider": "Browser AI",
        "browser_provider": "chatgpt",
        "capabilities": ["chat", "json"],
        "enabled": True,
    }
    result = ai_gateway.chat_json(
        tmp_path,
        {"ai_models": [browser_model]},
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
        stream=False,
    )

    assert result == {"ok": True, "title": "Browser OK"}
    assert "最终输出必须是一个合法 JSON 对象" in prompts[0]


def test_assign_upc_writes_current_product_and_returns_full_payload(
    tmp_path: Path,
) -> None:
    with temp_app_context(tmp_path):
        (tmp_path / "upc_pool.json").write_text(
            '{"values":["725272000007"],"used":[]}',
            encoding="utf-8",
        )
        get_context().products.save_product(
            {
                "name": "UPC test product",
                # 只有具备真实业务内容的草稿才会持久化；默认草稿模板不是
                # 独立平台事实，不能用商品主档 UPC 隐式回填。
                "drafts": {
                    "mercadolibre": {
                        "enabled": True,
                        "title": "UPC test Mercado Libre draft",
                    }
                },
            }
        )

        result = publish_helpers.assign_upc()

        assert result["ok"] is True
        assert result["upc"] == "725272000007"
        assert result["product"]["upc"] == "725272000007"
        assert result["product"]["drafts"]["mercadolibre"]["upc"] == "725272000007"
        assert isinstance(result["productsIndex"], list)
