"""草稿复制与选品的组合验收：真实 Store、原生 Agent 和可信回执。"""

import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart, ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.product_write_capabilities import DraftSkuSelectionUpdateRequest
from tests.test_erp_db import sample_product
from tests.test_native_agent_integration import service, body, CONVERSATION


def seed_draft():
    app = get_context()
    product = sample_product("五种橡胶垫组合")
    product["sku_items"] = [
        {"id": f"sku-{i}", "name": f"组合 {i}", "active": True, "cost_cny": str(i + 1)}
        for i in range(5)
    ]
    product["drafts"]["mercadolibre"]["sku_items"] = [
        {"sku_id": f"sku-{i}", "selected": True, "stock": "9",
         "overrides": {"cost_cny": str(i + 2)},
         "pricing": {"applied": True, "targets": {"mercadolibre:cbt": {
             "listing_currency": "USD", "applied_price": {"amount": str(20 + i), "currency": "USD"},
         }}}}
        for i in range(5)
    ]
    saved = app.products.save_product(product)
    return app, saved["drafts"]["mercadolibre"]["draft_id"]


def execution(source_id, *, conversation=CONVERSATION, tools=None):
    return AiExecutionContext.create(
        timeout_seconds=30, budget_profile="test", allow_write=True,
        permissions={"draft.write", "draft.read"},
        idempotency_context={"operation_key": "test-call"},
        business_scope={
            "conversation_id": conversation,
            "target_draft_ids": json.dumps([source_id]),
            "allowed_write_tools": json.dumps(tools or ["draft_duplicate", "draft_sku_selection_update"]),
        },
    )


def test_native_agent_copies_five_drafts_then_selects_one_sku_each(tmp_path):
    app, source_id = seed_draft()
    original = app.db.load_draft_model(source_id)
    facts = deepcopy(app.db.load_product_model(original["product_id"])["sku_items"])
    toolset = build_global_chat_toolset(app)
    copied_ids = []

    async def model(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        copies = [p for p in returns if p.tool_name == "draft_duplicate"]
        selections = [p for p in returns if p.tool_name == "draft_sku_selection_update"]
        assert {"draft_duplicate", "draft_sku_selection_update"} <= {t.name for t in info.function_tools}
        if len(copies) < 5:
            yield {0: DeltaToolCall(name="draft_duplicate", json_args=json.dumps({"draft_id": source_id}),
                                   tool_call_id=f"copy-{len(copies)}")}
        elif not selections:
            copied_ids[:] = [p.content["draft_id"] for p in copies]
            assert len(set(copied_ids)) == 5
            yield {
                i: DeltaToolCall(name="draft_sku_selection_update", json_args=json.dumps({
                    "draft_id": copied_id, "selected_sku_ids": [f"sku-{i}"],
                }), tool_call_id=f"select-{i}")
                for i, copied_id in enumerate(copied_ids)
            }
        else:
            assert len(selections) == 5
            assert all(p.content.get("changed") is True for p in selections), selections
            yield "五份草稿已创建，每份仅勾选一个组合。"

    ui = service(tmp_path, FunctionModel(stream_function=model), toolset)
    # 测试 service 使用独立消息库，业务门面使用同一个真实回执实例。
    app._agent_calls = ui.call_store
    asyncio.run(ui.prepare_run(body("复制五份并分别选择一个 SKU", target_draft_ids=[source_id])).stream(lambda _: None))
    assert len(copied_ids) == 5, ui.dump_ui_messages(CONVERSATION)
    assert app.db.load_draft_model(source_id) == original
    assert app.db.load_product_model(original["product_id"])["sku_items"] == facts
    for i, copied_id in enumerate(copied_ids):
        draft = app.db.load_draft_model(copied_id)
        assert [r["sku_id"] for r in draft["sku_items"] if r["selected"]] == [f"sku-{i}"]
        assert len(draft["sku_items"]) == 5
        assert draft["publication"] == {}
        for row, before in zip(draft["sku_items"], original["sku_items"]):
            assert row["sku"] != before["sku"]
            assert row["overrides"] == before["overrides"]
            assert row["stock"] == before["stock"]
            assert row["pricing"] == before["pricing"]

    # 原生调用 ID 重放必须返回旧副本，不能增加第六份。
    async def replay(messages, info):
        if not any(isinstance(p, ToolReturnPart) and p.tool_call_id == "copy-0"
                   for p in messages[-1].parts):
            yield {0: DeltaToolCall(name="draft_duplicate", json_args=json.dumps({"draft_id": source_id}), tool_call_id="copy-0")}
        else:
            yield "已核对原复制回执。"

    before_ids = {r["draft_id"] for r in app.db.list_draft_records(scope="all")}
    original_receipt = ui.call_store.receipt(CONVERSATION, "copy-0")["output_json"]
    resumed = service(tmp_path, FunctionModel(stream_function=replay), toolset)
    app._agent_calls = resumed.call_store
    asyncio.run(resumed.prepare_run(body("核对复制回执", message_id="user-replay", target_draft_ids=[source_id])).stream(lambda _: None))
    assert {r["draft_id"] for r in app.db.list_draft_records(scope="all")} == before_ids
    assert resumed.call_store.receipt(CONVERSATION, "copy-0")["output_json"] == original_receipt


def test_selection_is_atomic_idempotent_and_preserves_other_sku_fields():
    app, source_id = seed_draft()
    before = app.db.load_draft_model(source_id)
    for target in [before, *before["target_sites"]]:
        target["last_precheck"] = {"ok": True}
        target["publish_status"] = "ready"
    app.db.upsert_draft_model(before["product_id"], before["platform"], before)
    before = app.db.load_draft_model(source_id)
    result, error, _ = app.products.update_draft_sku_selection(source_id, ["sku-1", "sku-3"])
    assert error is None and result["changed"]
    after = app.db.load_draft_model(source_id)
    for target in [after, *after["target_sites"]]:
        assert target["last_precheck"] == {}
        assert target["publish_status"] == ""
    for row, original in zip(after["sku_items"], before["sku_items"]):
        assert {k: v for k, v in row.items() if k != "selected"} == {k: v for k, v in original.items() if k != "selected"}
    result, error, _ = app.products.update_draft_sku_selection(source_id, ["sku-3", "sku-1"])
    assert error is None and result["changed"] is False
    assert app.db.load_draft_model(source_id) == after
    for ids in (["sku-1", "foreign"], ["sku-1", "sku-1"]):
        _, error, status = app.products.update_draft_sku_selection(source_id, ids)
        assert error["error_code"] == "DRAFT_SKU_SELECTION_INVALID" and status == 400
        assert app.db.load_draft_model(source_id) == after
    result, error, _ = app.products.update_draft_sku_selection(source_id, [])
    assert error is None and result["changed"]
    assert not any(row["selected"] for row in app.db.load_draft_model(source_id)["sku_items"])


def test_selection_rejects_inactive_sku_and_unknown_draft():
    app, source_id = seed_draft()
    draft = app.db.load_draft_model(source_id)
    product = app.db.load_product_model(draft["product_id"])
    product["sku_items"][0]["active"] = False
    app.db.upsert_product_model(product)
    before = app.db.load_draft_model(source_id)
    _, error, _ = app.products.update_draft_sku_selection(source_id, ["sku-0"])
    assert error["error_code"] == "DRAFT_SKU_SELECTION_INVALID"
    assert app.db.load_draft_model(source_id) == before
    _, error, status = app.products.update_draft_sku_selection("missing", [])
    assert status == 404 and error["error_code"] == "DRAFT_NOT_FOUND"


def test_only_successful_same_conversation_copy_receipts_extend_scope():
    app, source_id = seed_draft()
    toolset = build_global_chat_toolset(app)
    copy_tool = toolset.bindings["draft_duplicate"]
    select_tool = toolset.bindings["draft_sku_selection_update"]
    copied = copy_tool.executor({"draft_id": source_id}, execution(source_id))
    copy_id = copied["draft_id"]
    args = {"draft_id": copy_id, "selected_sku_ids": ["sku-0"]}
    with pytest.raises(AiToolExecutionError, match="范围"):
        select_tool.executor(args, execution(source_id))
    app.agent_calls.begin_write(CONVERSATION, "copy", "draft_duplicate", {"draft_id": source_id})
    app.agent_calls.finish(CONVERSATION, "copy", {"ok": False, "error": {"code": "TOOL_OUTCOME_UNKNOWN"}})
    with pytest.raises(AiToolExecutionError, match="范围"):
        select_tool.executor(args, execution(source_id))
    app.agent_calls.finish(CONVERSATION, "copy", copied)
    assert select_tool.executor(args, execution(source_id))["changed"] is True
    grandchild = copy_tool.executor({"draft_id": copy_id}, execution(source_id))
    app.agent_calls.begin_write(CONVERSATION, "grandchild", "draft_duplicate", {"draft_id": copy_id})
    app.agent_calls.finish(CONVERSATION, "grandchild", grandchild)
    assert select_tool.executor({"draft_id": grandchild["draft_id"], "selected_sku_ids": []}, execution(source_id))["changed"] is True
    with pytest.raises(AiToolExecutionError, match="范围"):
        select_tool.executor(args, execution(source_id, conversation="another-conversation"))
    with pytest.raises(AiToolExecutionError, match="超出用户本轮要求"):
        select_tool.executor(args, execution(source_id, tools=["draft_duplicate"]))
    sibling = copy_tool.executor({"draft_id": source_id}, execution(source_id))
    with pytest.raises(AiToolExecutionError, match="范围"):
        select_tool.executor({**args, "draft_id": sibling["draft_id"]}, execution(source_id))
    with pytest.raises(AiToolExecutionError, match="范围"):
        copy_tool.executor({"draft_id": sibling["draft_id"]}, execution(source_id))
    # 新一轮仍可操作合法副本，但不会扩大新的手工选择范围。
    app.agent_calls.receive(CONVERSATION, "next", [ModelRequest(parts=[UserPromptPart("继续修改副本")])])
    assert select_tool.executor(args, execution(source_id))["changed"] is False
    with pytest.raises(AiToolExecutionError, match="范围"):
        select_tool.executor(args, execution(sibling["draft_id"]))


def test_selection_schema_and_read_preserve_boolean_and_structured_overrides():
    app, source_id = seed_draft()
    for ids in ([""], ["sku-0", "sku-0"]):
        with pytest.raises(ValidationError):
            DraftSkuSelectionUpdateRequest(draft_id=source_id, selected_sku_ids=ids)
    with pytest.raises(ValidationError):
        DraftSkuSelectionUpdateRequest(draft_id=source_id, selected_sku_ids=[], product_id="injected")
    toolset = build_global_chat_toolset(app)
    result = toolset.bindings["draft_read"].executor({"draft_id": source_id}, execution(source_id))
    assert result["draft"]["sku_items"][0]["selected"] is True
    assert result["draft"]["sku_items"][0]["overrides"] == {"cost_cny": "2"}
    assert result["product_context"]["sku_items"][0]["active"] is True
    app.products.update_draft_sku_selection(source_id, [])
    result = toolset.bindings["draft_read"].executor({"draft_id": source_id}, execution(source_id))
    assert result["draft"]["sku_items"][0]["selected"] is False
