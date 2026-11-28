"""包装估算覆盖原生人工确认、失效控制和区间试算。"""
import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.package_estimate_capabilities import CONFIRM, _size_check, draft_package_estimates_preview, PackageEstimateScope
from erp_web.schemas.package_estimates import EdgeEstimate, PackageEstimatesRequest, PackageFreightPreviewRequest
from tests.ai_code_mode_helpers import discovering_tools, python_call
from tests.test_draft_edit_capabilities import seed_draft
from tests.test_native_agent_integration import CONVERSATION, body, service


@pytest.mark.parametrize("vision,complete", [(True, False), (False, False), (True, True)])
def test_draft_page_discloses_package_tools_before_search(tmp_path, vision, complete):
    app, draft_id = seed_draft()
    if complete:
        product, _ = proposal(app, draft_id)
        app.products.update_product_sku_facts({
            "product_id": product["product_id"], "expected_updated_at": product["updated_at"],
            "changes": [{"sku_id": sku["id"], "package_dimensions": {"length_cm": 10, "width_cm": 8, "height_cm": 5}}
                        for sku in product["sku_items"]],
        })
    seen = []

    async def model(messages, info):
        seen.append({tool.name for tool in info.function_tools})
        yield "已核对工具。"

    caps = ["chat", "tool_calling"] + (["image_understanding"] if vision else [])
    config = {"ai_models": [{"id": "test", "capabilities": caps}],
              "ai_use_case_bindings": {"global.chat": {"model_id": "test"}}}
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app, chat_config=config))
    run = ui.prepare_run(body("准备这个草稿", page_context={"page": "draft_editor", "draft_id": draft_id}))
    asyncio.run(run.stream(lambda _: None))
    names, = seen
    assert (CONFIRM in names) == (not complete)
    assert ("draft_package_estimates_preview" in names) == (not complete)
    assert ("product_image_read" in names) == (vision and not complete)


@pytest.mark.parametrize("via_python", [False, True])
def test_pricing_missing_size_discloses_tools_without_page_or_target(tmp_path, via_python):
    app, draft_id = seed_draft()
    seen = []
    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        seen.append(names)
        if len(seen) == 1:
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["draft_pricing_preview"]}', tool_call_id="find")}
        elif len(seen) == 2:
            call = python_call if via_python else DeltaToolCall
            yield {0: call(name="draft_pricing_preview", json_args=json.dumps({"draft_id": draft_id}), tool_call_id="price")}
        else:
            yield "已收到包装资料缺项。"
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    asyncio.run(ui.prepare_run(body("核价")).stream(lambda _: None))
    assert len(seen) == 3 and all(CONFIRM not in names for names in seen[:2])
    assert {CONFIRM, "draft_package_estimates_preview", "product_read"} <= seen[2]


def test_package_tools_stay_visible_across_followup_user_reply_without_oscillating(tmp_path):
    """缺尺寸草稿激活包装工具后，后续用户回复新消息（如回答其他缺口）不得撤回包装工具导致缓存震荡。"""
    app, draft_id = seed_draft()
    seen: list[set[str]] = []

    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        seen.append(names)
        if len(seen) == 1:
            yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["draft_pricing_preview"]}', tool_call_id="find")}
        elif len(seen) == 2:
            yield {0: DeltaToolCall(name="draft_pricing_preview", json_args=json.dumps({"draft_id": draft_id}), tool_call_id="price")}
        elif len(seen) == 3:
            yield "请先确认其他商品库存。"
        else:
            yield "已收到库存回复，继续处理包装估算。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    asyncio.run(ui.prepare_run(body("核价", message_id="m1")).stream(lambda _: None))
    asyncio.run(ui.prepare_run(body("100", message_id="m2")).stream(lambda _: None))
    assert len(seen) == 4
    assert {CONFIRM, "draft_package_estimates_preview", "product_read"} <= seen[2]
    assert {CONFIRM, "draft_package_estimates_preview", "product_read"} <= seen[3]


def proposal(app, draft_id):
    product = app.db.load_product_model(app.products.draft_record(draft_id)["product_id"])
    return product, {
        "draft_id": draft_id, "expected_product_updated_at": product["updated_at"], "estimates": [{
            "sku_id": "sku-0", "dimensions": {"length_cm": {"low": 30, "suggested": 32, "high": 35}},
            "basis": "该 SKU 图标注本体长 28 cm，预计两端合计预留 2–7 cm。",
        }],
    }


@pytest.mark.parametrize("decision", ["approve", "deny", "stale"])
def test_ask_mode_waits_for_package_review(tmp_path, decision):
    app, draft_id = seed_draft()
    original, args = proposal(app, draft_id)
    read = lambda: app.db.load_product_model(original["product_id"])

    @discovering_tools(CONFIRM)
    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) and part.tool_name == CONFIRM for msg in messages for part in msg.parts):
            yield "已收到处理结果。"
        else:
            yield {0: DeltaToolCall(name=CONFIRM, json_args=json.dumps(args), tool_call_id="estimate")}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: "ask"
    asyncio.run(ui.prepare_run(body("估算包装", target_draft_ids=[draft_id])).stream(lambda _: None))
    assert read() == original
    part, = [p for m in ui.dump_ui_messages(CONVERSATION)["messages"] for p in m["parts"] if p.get("state") == "approval-requested"]
    assert part["input"] == args
    if decision == "stale":
        app.products.update_product_sku_facts({"product_id": original["product_id"], "expected_updated_at": original["updated_at"],
                                              "changes": [{"sku_id": "sku-0", "package_dimensions": {"length_cm": 37}}]})
    before = read()
    part["state"] = "approval-responded"
    part["approval"]["approved"] = decision != "deny"
    payload = {"id": CONVERSATION, "trigger": "submit-message",
               "messages": [{"id": "review", "role": "assistant", "parts": [part]}]}
    asyncio.run(ui.prepare_run(json.dumps(payload).encode(), approval_token="test-token").stream(lambda _: None))
    saved = read()
    if decision != "approve":
        assert saved == before
        return
    row = saved["sku_items"][0]
    assert row["package_dimensions"] == {**original["sku_items"][0]["package_dimensions"], "length_cm": "32.0"}
    assert row["package_estimates"]["length_cm"] == {**args["estimates"][0]["dimensions"]["length_cm"], "basis": args["estimates"][0]["basis"]}
    assert saved["sku_items"][1:] == original["sku_items"][1:]
    assert not app.products.draft_record(draft_id)["sku_items"][0]["pricing"]["applied"]
    # 旧版编辑器不知道估算标记；表单回传不能抹去或伪造服务端的来源。
    legacy = deepcopy(saved)
    legacy["sku_items"][0].pop("package_estimates")
    legacy["sku_items"][1]["package_estimates"] = deepcopy(row["package_estimates"])
    saved = app.products.save_product_profile(legacy)
    assert saved["sku_items"][0]["package_estimates"] == row["package_estimates"]
    assert "package_estimates" not in saved["sku_items"][1]
    app.products.update_product_sku_facts({"product_id": saved["product_id"], "expected_updated_at": saved["updated_at"],
                                          "changes": [{"sku_id": "sku-0", "package_dimensions": {"length_cm": 30}}]})
    assert "package_estimates" not in read()["sku_items"][0]


@pytest.mark.parametrize("mode", ["auto", "full"])
def test_auto_and_full_modes_auto_approve_package_estimates(tmp_path, mode):
    app, draft_id = seed_draft()
    original, args = proposal(app, draft_id)
    read = lambda: app.db.load_product_model(original["product_id"])

    @discovering_tools(CONFIRM)
    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) and part.tool_name == CONFIRM for msg in messages for part in msg.parts):
            yield "包装尺寸估算已自动保存。"
        else:
            yield {0: DeltaToolCall(name=CONFIRM, json_args=json.dumps(args), tool_call_id="estimate")}

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: mode
    asyncio.run(ui.prepare_run(body("估算包装", target_draft_ids=[draft_id])).stream(lambda _: None))
    pending = [p for m in ui.dump_ui_messages(CONVERSATION)["messages"] for p in m["parts"] if p.get("state") == "approval-requested"]
    assert pending == []
    saved = read()
    row = saved["sku_items"][0]
    assert row["package_dimensions"] == {**original["sku_items"][0]["package_dimensions"], "length_cm": "32.0"}
    assert row["package_estimates"]["length_cm"] == {**args["estimates"][0]["dimensions"]["length_cm"], "basis": args["estimates"][0]["basis"]}
    assert not app.products.draft_record(draft_id)["sku_items"][0]["pricing"]["applied"]


def test_estimate_range_boundaries_and_invalid_inputs():
    app, draft_id = seed_draft()
    _, args = proposal(app, draft_id)
    item = PackageEstimatesRequest.model_validate(args).estimates[0]
    policy = {"max_edge_cm": "60", "max_sum_cm": "90", "max_g": "500"}
    def status(width, height, weight="0.5"):
        return _size_check(item, {"width_cm": width, "height_cm": height, "weight_kg": weight}, policy)
    assert "整个区间符合" in status(25, 30)
    assert "跨过" in status(26, 30)
    assert "整个区间超过" in status(31, 30)
    assert "毛重" in status(26, 30, "0.501")
    with pytest.raises(ValidationError):
        EdgeEstimate(low=32, suggested=31, high=35)
    with pytest.raises(ValidationError):
        PackageEstimatesRequest.model_validate({**args, "estimates": args["estimates"] * 2})


def test_preview_keeps_missing_facts_and_cannot_create_applicable_prices():
    app, draft_id = seed_draft()
    product, args = proposal(app, draft_id)
    draft = deepcopy(app.products.draft_record(draft_id))
    result = draft_package_estimates_preview(PackageFreightPreviewRequest.model_validate(args),
                                             PackageEstimateScope(app.products, app.tariffs.load))
    assert len(result.scenarios) == 2
    for endpoint in result.scenarios:
        assert not endpoint.quotes
        assert any("包装重量" in message for message in endpoint.errors)
    assert app.db.load_product_model(product["product_id"]) == product
    assert app.products.draft_record(draft_id) == draft
