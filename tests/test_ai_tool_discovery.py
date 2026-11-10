"""生产工具目录按需发现，原生历史恢复和业务权限不变。"""

import asyncio
from dataclasses import replace
import json

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import ToolDefinition

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.services.ai_tool_discovery import match_tool_queries
from tests.ai_code_mode_helpers import business_returns
from tests.test_native_agent_integration import CONVERSATION, binding, body, service, tools


def test_matching_prefers_exact_names_and_supports_chinese():
    corpus = [ToolDefinition(name="draft_read", description="读取草稿"),
              ToolDefinition(name="draft_save", description="保存草稿，先调用 draft_read"),
              ToolDefinition(name="orders_read", description="查询订单")]
    assert match_tool_queries(None, ["draft_read"], corpus) == ["draft_read"]
    assert match_tool_queries(None, ["draft read", "draft_read"], corpus) == ["draft_read"]
    assert match_tool_queries(None, ["订单"], corpus) == ["orders_read"]
    assert match_tool_queries(None, ["draft_read", "draft_save"], corpus) == ["draft_read", "draft_save"]
    assert match_tool_queries(None, ["unknown_tool"], corpus) == []


@pytest.mark.parametrize("vision", [False, True])
def test_mixed_sku_and_image_query_discovers_reader_in_full_catalog(tmp_path, vision):
    queries = ["draft sku read", "product image read", "product_sku_facts_update", "draft images"]
    requests = []

    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        requests.append(names)
        if len(requests) == 1:
            yield {0: DeltaToolCall(name="search_tools", json_args=json.dumps({"queries": queries}),
                                    tool_call_id="find-sku-images")}
        else:
            yield "已查找所需工具。"

    capabilities = ["chat", "tool_calling"]
    if vision:
        capabilities.append("image_understanding")
    config = {"ai_models": [{"id": "test", "capabilities": capabilities}],
              "ai_use_case_bindings": {"global.chat": {"model_id": "test"}}}
    toolset = build_global_chat_toolset(get_context(), chat_config=config)
    ui = service(tmp_path, FunctionModel(stream_function=model, profile={"supported_native_tools": frozenset()}), toolset)
    asyncio.run(ui.prepare_run(body("把当前草稿 SKU 图片上的尺寸识别写入 SKU 包装尺寸")).stream(lambda _: None))
    assert len(requests) == 2
    assert "product_sku_facts_update" in requests[1]
    assert ("product_image_read" in requests[1]) == vision


@pytest.mark.parametrize("native_search", [False, True])
def test_full_catalog_starts_small_and_restores_only_discovered_tools(tmp_path, native_search):
    requests = []
    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        requests.append(names)
        if len(requests) == 1:
            assert names <= {"search_tools", "run_code", "request_user_input", "business_data_catalog", "business_data_read"}
            assert sum(len(t.description or "") + len(json.dumps(t.parameters_json_schema)) for t in info.function_tools) < 7000
            assert len(info.instructions or "") < 18000
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["product_publish_validate"]}', tool_call_id="find-validate")}
        else:
            assert "product_publish_validate" in names
            assert names <= {"search_tools", "run_code", "request_user_input", "product_publish_validate", "business_data_catalog", "business_data_read"}
            assert "async def product_publish_validate(" in info.instructions
            assert "async def order_detail_read(" not in info.instructions
            yield "已找到预检能力。"
    profile = None if native_search else {"supported_native_tools": frozenset()}
    ui = service(tmp_path, FunctionModel(stream_function=model, profile=profile), build_global_chat_toolset(get_context()))
    for number in (1, 2):
        chunks = []
        asyncio.run(ui.prepare_run(body("查看预检工具", message_id=f"u{number}")).stream(chunks.append))
        assert b'"type":"error"' not in b"".join(chunks)
    assert len(requests) == 3


def test_discovery_does_not_execute_approval_tool_and_resume_does_not_repeat_write(tmp_path):
    executed = []
    async def model(messages, info):
        if "publish" not in {t.name for t in info.function_tools}:
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["publish"]}', tool_call_id="find-publish")}
        elif not business_returns(messages):
            assert "async def publish(" not in (info.instructions or "")
            yield {0: DeltaToolCall(name="publish", json_args='{"draft_id":"d1"}', tool_call_id="publish-once")}
        else:
            yield "提交成功。"
    toolset = replace(tools(binding("publish", lambda args, _: executed.append(args) or {"ok": True}, approval=True, write=True)), defer_loading=True)
    ui = service(tmp_path, FunctionModel(stream_function=model, profile={"supported_native_tools": frozenset()}), toolset)
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert executed == []
    pending = ui.call_store.pending(CONVERSATION)
    assert pending and len(pending[0].approvals) == 1
    messages = ui.dump_ui_messages(CONVERSATION)["messages"]
    approvals = []
    for message in messages:
        for part in message.get("parts", []):
            if part.get("state") == "approval-requested":
                part["state"] = "approval-responded"
                part["approval"]["approved"] = True
                approvals.append(part)
    request = json.dumps({"id": CONVERSATION, "trigger": "submit-message", "messages": [{"id": "approval", "role": "assistant", "parts": approvals}]}).encode()
    asyncio.run(ui.prepare_run(request, approval_token="test-token").stream(lambda _: None))
    assert executed == [{"draft_id": "d1"}]
    assert ui.call_store.pending(CONVERSATION) is None
