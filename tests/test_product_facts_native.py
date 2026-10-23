"""商品资料写入在原生对话中返回可验证的真实回执。"""
import asyncio
import json
from pydantic_ai.messages import TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from tests.test_draft_edit_capabilities import seed_draft
from tests.ai_code_mode_helpers import python_call, business_returns, available_python_functions
from tests.test_native_agent_integration import CONVERSATION, body, service

def test_confirmation_keeps_tools_and_write_receipt_reaches_followup_model(tmp_path):
    """旧权限 metadata 不会再隐藏工具；“是”后可写，追问时模型获得完整写回执。"""
    app, draft_id = seed_draft()
    toolset = build_global_chat_toolset(app)
    product = app.db.load_product_model(app.products.draft_record(draft_id)['product_id'])
    arguments = {'product_id': product['product_id'], 'expected_updated_at': product['updated_at'], 'changes': [
        {'sku_id': f'sku-{i}', 'package_dimensions': {'length_cm': 13, 'width_cm': 10, 'height_cm': 5}} for i in range(2)]}
    requests = []

    async def model(messages, info):
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        requests.append(latest)
        assert "async def product_sku_facts_update" in available_python_functions(info)
        assert "async def draft_sku_attributes_update" in available_python_functions(info)
        if latest == "重新读取草稿":
            yield "商品尺寸是 13 × 10 × 5 cm，是否应用到前两个 SKU？"
            return
        returns = business_returns(messages)
        if latest == "是" and not returns:
            assert any(isinstance(p, TextPart) and "13 × 10 × 5" in p.content for m in messages for p in m.parts)
            yield {0: python_call(name="product_sku_facts_update", json_args=json.dumps(arguments), tool_call_id="package-write")}
        else:
            receipt = returns[-1]
            assert receipt.tool_name == "product_sku_facts_update"
            assert receipt.content["changed_count"] == 2
            assert receipt.content["sku_ids"] == ["sku-0", "sku-1"]
            yield "已将 13 × 10 × 5 cm 写入两个指定 SKU，重量未修改。"

    ui = service(tmp_path, FunctionModel(stream_function=model), toolset)
    asyncio.run(ui.prepare_run(body("重新读取草稿", "read")).stream(lambda _: None))
    history = ui.chat_service.trusted_history(CONVERSATION)
    history[0].metadata = {**(history[0].metadata or {}), "allowed_write_tools": []}
    ui.chat_service.message_store.save(CONVERSATION, history)
    for text, identifier in [("是", "confirmation"), ("你没有把尺寸写入草稿吗", "followup")]:
        asyncio.run(ui.prepare_run(body(text, identifier, target_draft_ids=[draft_id])).stream(lambda _: None))
    assert requests == ["重新读取草稿", "是", "是", "你没有把尺寸写入草稿吗"]
    history = ui.chat_service.trusted_history(CONVERSATION)
    assert [p.tool_name for p in business_returns(history)] == ["product_sku_facts_update"]
    assert app.db.load_product_model(product["product_id"])["sku_items"][0]["package_dimensions"]["length_cm"] == "13.0"
