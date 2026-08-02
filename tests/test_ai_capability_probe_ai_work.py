from __future__ import annotations

from pathlib import Path

from erp_web.context import get_context
from erp_web.services import (
    ai_gateway_browser_provider,
    ai_gateway_cli_provider,
    ai_gateway_probe,
    browser_ai_runtime,
)


def _assert_probe_conversation(report: dict, capability: str, output: str) -> None:
    result = report["results"][capability]
    conversation_id = result["conversation_id"]
    events = get_context().ai_journal.read_events(conversation_id)

    assert any(
        event.get("name") == "capability_probe.request" for event in events
    )
    assert [
        event.get("delta")
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT"
    ] == [output]
    assert events[-1]["type"] == "RUN_FINISHED"


def test_cli_probe_uses_ai_work_conversation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "cli-probe-token"
    monkeypatch.setattr(ai_gateway_probe.secrets, "token_hex", lambda _: token)
    monkeypatch.setattr(
        ai_gateway_cli_provider,
        "_run_codex_cli_text",
        lambda *args, **kwargs: token,
    )
    model = {
        "id": "codex-cli",
        "name": "Codex CLI",
        "connection_type": "cli",
        "cli_tool": "codex",
        "command": "codex",
        "model": "",
    }

    report = ai_gateway_cli_provider.probe_cli_model_capabilities(
        tmp_path,
        model,
        ["chat"],
        10,
    )

    _assert_probe_conversation(report, "chat", token)


def test_browser_probe_uses_ai_work_conversation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "browser-probe-token"
    monkeypatch.setattr(ai_gateway_probe.secrets, "token_hex", lambda _: token)
    monkeypatch.setattr(
        ai_gateway_browser_provider,
        "_browser_chat_result",
        lambda *args, **kwargs: browser_ai_runtime.BrowserAiRunResult(
            text=token,
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "profile"),
            port=9333,
            page_url="https://chatgpt.com/c/test",
        ),
    )
    model = {
        "id": "chatgpt-browser",
        "name": "ChatGPT Browser",
        "connection_type": "browser",
        "browser_provider": "chatgpt",
        "model": "",
    }

    report = ai_gateway_browser_provider.probe_browser_model_capabilities(
        tmp_path,
        model,
        ["chat"],
        10,
    )

    _assert_probe_conversation(report, "chat", token)
