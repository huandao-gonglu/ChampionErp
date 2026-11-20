"""唯一用户指令文件的保存、并发保护与数据隔离。"""

import asyncio
import json
from pathlib import Path
import re

import pytest
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.user_agents_capability import USER_AGENTS_UPDATE
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.user_agents import read_user_agents, load_user_agent_instructions
from tests.ai_code_mode_helpers import discover_tools
from tests.test_native_agent_integration import CONVERSATION, body, service


def test_initial_outline_is_comments_only_and_read_does_not_write(app_dir):
    document = get_context().config.user_agents()
    assert "交流偏好" in document["content"]
    assert re.sub(r"<!--[\s\S]*?-->", "", document["content"]).strip() == ""
    assert load_user_agent_instructions(app_dir) == ""
    assert not (app_dir / "config/userAgents.md").exists()


def test_save_clear_and_optimistic_concurrency_preserve_exact_text(app_dir):
    config = get_context().config
    first = config.user_agents()
    content = "<!-- 仅供编辑参考 -->\n\n## 回复偏好\n请先给结果。\n"
    saved = config.save_user_agents({"content": content, "expected_revision": first["revision"]})
    assert saved == read_user_agents(app_dir)
    path = app_dir / "config/userAgents.md"
    assert path.read_bytes() == content.encode()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "仅供编辑参考" not in load_user_agent_instructions(app_dir)
    assert "请先给结果" in load_user_agent_instructions(app_dir)
    with pytest.raises(RequestValidationError) as error:
        config.save_user_agents({"content": "旧页面覆盖", "expected_revision": first["revision"]})
    assert error.value.status_code == 409
    assert path.read_text() == content
    payload = validate_request_payload({"content": "", "expected_revision": saved["revision"]},
                                       endpoint="/api/system-settings/user-agents")
    config.save_user_agents(payload)
    assert path.read_bytes() == b"" and load_user_agent_instructions(app_dir) == ""


def test_invalid_content_or_arbitrary_paths_do_not_replace_user_preferences(app_dir):
    config = get_context().config
    initial = config.user_agents()
    saved = config.save_user_agents({"content": "保留这段内容", "expected_revision": initial["revision"]})
    for extra in ({"content": "汉" * 12000}, {"path": "/tmp/systemAgents.md"}):
        with pytest.raises(ValueError):
            config.save_user_agents({"content": "不应保存", "expected_revision": saved["revision"], **extra})
    assert config.user_agents() == saved
    assert not list((app_dir / "config").glob(".userAgents.md.*.tmp"))


def test_failed_atomic_replace_preserves_original_file(monkeypatch, app_dir):
    config = get_context().config
    initial = config.user_agents()
    saved = config.save_user_agents({"content": "旧内容", "expected_revision": initial["revision"]})
    def fail(*args):
        raise OSError("模拟替换失败")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="模拟替换失败"):
        config.save_user_agents({"content": "新内容", "expected_revision": saved["revision"]})
    assert config.user_agents() == saved
    assert not list((app_dir / "config").glob(".userAgents.md.*.tmp"))


def test_user_agents_symlink_cannot_read_or_overwrite_another_file(app_dir, tmp_path):
    other = tmp_path / "another-account.md"
    other.write_text("其他账号资料")
    path = app_dir / "config/userAgents.md"
    path.parent.mkdir(exist_ok=True)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="符号链接"):
        read_user_agents(app_dir)
    assert other.read_text() == "其他账号资料"


@pytest.mark.parametrize("decision", ["append", "replace", "duplicate", "deny", "stale"])
def test_chat_updates_user_agents_only_after_native_confirmation(app_dir, decision):
    context = get_context()
    config = context.config
    original = "<!-- 编辑说明 -->\n\n始终显示单位。\n\n先给结果。\n"
    initial = config.save_user_agents({"content": original, "expected_revision": config.user_agents()["revision"]})
    text = "先给结果。" if decision == "duplicate" else "Yandex 托盘缺少形状变体字段时，先确认后再用真实形状和尺寸区分。"
    arguments = {"text": text}
    if decision in {"replace", "stale"}:
        arguments["current_text"] = "先给结果。"
    receipt = []
    fresh_instructions = []

    async def model(messages, info):
        for message in reversed(messages):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == USER_AGENTS_UPDATE:
                    receipt.append(part.content)
                    yield "已收到处理结果。"
                    return
                if isinstance(part, UserPromptPart) and part.content == "读取已保存偏好":
                    fresh_instructions.append(info.instructions)
                    yield "已读取。"
                    return
        call = discover_tools(info, USER_AGENTS_UPDATE) or DeltaToolCall(
            name=USER_AGENTS_UPDATE, json_args=json.dumps(arguments), tool_call_id="update-rule",
        )
        yield {0: call}

    ui = service(app_dir, FunctionModel(stream_function=model), build_global_chat_toolset(context))
    ui.chat_service.approval_mode_reader = lambda: "full"
    asyncio.run(ui.prepare_run(body("把这个操作写入记忆")).stream(lambda _: None))
    assert config.user_agents() == initial
    part, = [p for m in ui.dump_ui_messages(CONVERSATION)["messages"] for p in m["parts"] if p.get("state") == "approval-requested"]
    assert part["input"] == arguments
    if decision == "stale":
        config.save_user_agents({"content": original + "设置页另存的规则。\n", "expected_revision": initial["revision"]})
    before = config.user_agents()
    part["state"] = "approval-responded"
    part["approval"]["approved"] = decision != "deny"
    payload = {"id": CONVERSATION, "trigger": "submit-message",
               "messages": [{"id": "review", "role": "assistant", "parts": [part]}]}
    asyncio.run(ui.prepare_run(json.dumps(payload).encode(), approval_token="test-token").stream(lambda _: None))

    saved = config.user_agents()
    if decision in {"deny", "stale", "duplicate"}:
        assert saved == before
        if decision == "stale":
            assert receipt[0]["error"]["code"] == "USER_AGENTS_CHANGED"
        if decision == "duplicate":
            assert receipt == [{"changed": False, "text": text, "revision": saved["revision"]}]
        return
    expected = original.replace("先给结果。", text) if decision == "replace" else original + "\n" + text + "\n"
    assert saved["content"] == expected
    assert receipt == [{"changed": True, "text": text, "revision": saved["revision"]}]
    fresh_id = "conversation_global_chat_" + "7" * 32
    fresh = body("读取已保存偏好", "fresh").replace(CONVERSATION.encode(), fresh_id.encode())
    asyncio.run(ui.prepare_run(fresh).stream(lambda _: None))
    assert text in fresh_instructions[0] and "始终显示单位。" in fresh_instructions[0]
    assert "编辑说明" not in fresh_instructions[0]
