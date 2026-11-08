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
from tests.ai_code_mode_helpers import discovering_tools
from tests.test_draft_edit_capabilities import seed_draft
from tests.test_native_agent_integration import CONVERSATION, body, service


def proposal(app, draft_id):
    product = app.db.load_product_model(app.products.draft_record(draft_id)["product_id"])
    return product, {
        "draft_id": draft_id, "expected_product_updated_at": product["updated_at"], "estimates": [{
            "sku_id": "sku-0", "dimensions": {"length_cm": {"low": 30, "suggested": 32, "high": 35}},
            "basis": "该 SKU 图标注本体长 28 cm，预计两端合计预留 2–7 cm。",
        }],
    }


@pytest.mark.parametrize("decision", ["approve", "deny", "stale"])
def test_full_authorization_still_waits_for_package_review(tmp_path, decision):
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
    ui.chat_service.approval_mode_reader = lambda: "full"
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
