"""ERP 记忆跨对话加载、更新与原生系统上下文集成。"""

import asyncio

import pytest
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel

from erp_web.services.agent_memory import (
    AGENT_MEMORY_PATH,
    MAX_AGENT_MEMORY_BYTES,
    load_agent_memory_instructions,
)
from tests.test_native_agent_integration import CONVERSATION, body, service


def write_memory(app_dir, text):
    path = app_dir / AGENT_MEMORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_memory_is_instance_local_and_never_reads_development_agents(tmp_path):
    (tmp_path / "AGENTS.md").write_text("仅供开发 Agent 阅读", encoding="utf-8")
    assert load_agent_memory_instructions(tmp_path) == ""
    write_memory(tmp_path, "品牌优先平台真实无品牌。")
    instructions = load_agent_memory_instructions(tmp_path)
    assert "品牌优先平台真实无品牌。" in instructions
    assert "仅供开发 Agent 阅读" not in instructions
    other = tmp_path / "另一实例"
    other.mkdir()
    assert load_agent_memory_instructions(other) == ""


def test_memory_reloads_between_runs_and_is_independent_of_page_background(tmp_path):
    observed = []

    async def model(messages, info):
        observed.append(info.instructions)
        yield "已读取本轮要求"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    path = write_memory(tmp_path, "长期规则甲：无品牌优先。")
    asyncio.run(ui.prepare_run(body("填写属性", "u1", page_context={
        "page": "draft_editor", "draft_id": "draft-a",
    })).stream(lambda _: None))
    path.write_text("长期规则乙：公共属性与 SKU 特殊属性分开。", encoding="utf-8")
    asyncio.run(ui.prepare_run(body("继续", "u2")).stream(lambda _: None))
    path.write_text("\n", encoding="utf-8")
    asyncio.run(ui.prepare_run(body("查看情况", "u3")).stream(lambda _: None))

    assert "长期规则甲" in observed[0]
    assert "当前草稿 ID：draft-a" in observed[0]
    assert "长期规则乙" in observed[1]
    assert "长期规则甲" not in observed[1]
    assert "当前草稿 ID：draft-a" not in observed[1]
    assert "ERP 长期记忆（来源：config/agents.md）" not in observed[2]
    history = ui.chat_service.trusted_history(CONVERSATION)
    assert [p.content for m in history for p in m.parts if isinstance(p, UserPromptPart)] == [
        "填写属性", "继续", "查看情况",
    ]


def test_new_conversation_reads_persisted_memory(tmp_path):
    write_memory(tmp_path, "记住公共属性适用于所有已选 SKU。")
    observed = []

    async def model(messages, info):
        observed.append(info.instructions)
        yield "已读取"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    first = body("检查属性", "u1")
    second = first.replace(CONVERSATION.encode(), ("conversation_global_chat_" + "8" * 32).encode())
    for payload in (first, second):
        asyncio.run(ui.prepare_run(payload).stream(lambda _: None))
    assert len(observed) == 2
    assert all("记住公共属性适用于所有已选 SKU。" in item for item in observed)


@pytest.mark.parametrize("raw, message", [
    (b"x" * (MAX_AGENT_MEMORY_BYTES + 1), "不能超过 32 KiB"),
    (b"\xff", "必须使用 UTF-8"),
])
def test_invalid_memory_is_not_silently_truncated_or_ignored(tmp_path, raw, message):
    path = write_memory(tmp_path, "原始规则")
    path.write_bytes(raw)
    with pytest.raises(ValueError, match=message):
        load_agent_memory_instructions(tmp_path)
