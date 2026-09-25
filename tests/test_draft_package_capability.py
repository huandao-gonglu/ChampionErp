"""包装资料经过代码工具目录、原生对话和真实 Store 的回归。"""

import asyncio
import json

import pytest
from pydantic_ai.messages import TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.schemas.ai_tools import AiToolExecutionError
from tests.test_draft_edit_capabilities import execution, seed_draft
from tests.ai_code_mode_helpers import python_call, business_returns, available_python_functions
from tests.test_native_agent_integration import CONVERSATION, body, service


@pytest.fixture
def subject():
    app, draft_id = seed_draft()
    draft = app.db.load_draft_model(draft_id)
    product = app.products.load_product_from_index(draft["product_id"])
    for i, sku in enumerate(product["sku_items"]):
        sku["package_dimensions"] = {"length_cm": "0", "width_cm": "0", "height_cm": "0", "weight_kg": str(i + 1)}
    product["drafts"]["mercadolibre"]["package_dimensions"] = {
        "length_cm": "13", "width_cm": "10", "height_cm": "5", "weight_kg": "1",
    }
    app.products.save_product(product)
    draft = app.db.load_draft_model(draft_id)
    draft["target_sites"].append({
        "platform": "yandex", "site": "global", "language": "ru-RU", "category_id": "123",
        "attributes": {"material": "橡胶"},
    })
    app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
    return app, draft_id, build_global_chat_toolset(app)


def arguments(draft_id, **changes):
    return {"draft_id": draft_id, "sku_ids": ["sku-0", "sku-1"],
            "package_dimensions": {"length_cm": 13, "width_cm": 10, "height_cm": 5}, **changes}


def test_package_write_is_local_preserves_weights_and_targets_and_is_repeatable(subject):
    app, draft_id, toolset = subject
    before = app.db.load_draft_model(draft_id)
    product = app.db.load_product_model(before["product_id"])
    tool = toolset.bindings["draft_sku_package_update"]
    first = tool.executor(arguments(draft_id), execution(draft_id))
    assert first["changed"] is True and first["changed_count"] == 2
    assert first["sku_ids"] == ["sku-0", "sku-1"]
    assert first["package_dimensions"] == {"length_cm": 13, "width_cm": 10, "height_cm": 5}
    saved = app.db.load_draft_model(draft_id)
    for row in saved["sku_items"][:2]:
        assert row["overrides"]["package_dimensions"] == {
            "length_cm": "13.0", "width_cm": "10.0", "height_cm": "5.0",
        }
        assert row["overrides"]["cost_cny"] == before["sku_items"][int(row["sku_id"][-1])]["overrides"]["cost_cny"]
    assert saved["sku_items"][2:] == before["sku_items"][2:]
    assert saved["package_dimensions"] == before["package_dimensions"]
    assert [(t["platform"], t["site"], t["category_id"], t["attributes"]) for t in saved["target_sites"]] == [
        (t["platform"], t["site"], t["category_id"], t["attributes"]) for t in before["target_sites"]
    ]
    assert app.db.load_product_model(before["product_id"])["sku_items"] == product["sku_items"]
    read = toolset.bindings["draft_attributes_read"].executor(
        {"draft_id": draft_id, "platform": "yandex", "site": "global", "scope": "sku"}, execution(draft_id),
    )
    assert [row["package_dimensions"]["weight_kg"] for row in read["skus"][:2]] == ["1", "2"]
    assert [float(row["package_dimensions"]["length_cm"]) for row in read["skus"][:2]] == [13, 13]
    assert tool.executor(arguments(draft_id), execution(draft_id))["changed"] is False
    assert app.db.load_draft_model(draft_id) == saved


@pytest.mark.parametrize("patch", [{}, {"length_cm": 0}, {"height_cm": -1}, {"width_cm": float("inf")},
                                  {"weight_kg": float("nan")}, {"width_cm": None}, {"length_cm": True},
                                  {"length_cm": 13, "brand": "错误字段"}])
def test_invalid_package_patch_never_writes(subject, patch):
    app, draft_id, _ = subject
    before = app.db.load_draft_model(draft_id)
    _, error, status = app.products.update_draft_sku_package(draft_id, ["sku-0"], patch)
    assert status == 400 and error["error_code"] == "DRAFT_SKU_PACKAGE_INVALID"
    assert app.db.load_draft_model(draft_id) == before


@pytest.mark.parametrize("case", ["unknown", "unselected", "inactive", "published", "duplicate"])
def test_package_write_checks_entire_sku_scope_before_mutation(subject, case):
    app, draft_id, _ = subject
    draft = app.db.load_draft_model(draft_id)
    ids = ["sku-0", "sku-1"]
    if case == "unknown":
        ids.append("foreign")
    elif case == "unselected":
        draft["sku_items"][1]["selected"] = False
    elif case == "published":
        draft["target_sites"][1]["publish_status"] = "published"
    elif case == "duplicate":
        ids.append("sku-0")
    else:
        product = app.products.load_product_from_index(draft["product_id"])
        product["sku_items"][1]["active"] = False
        app.products.save_product(product)
    app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
    before = app.db.load_draft_model(draft_id)
    _, error, status = app.products.update_draft_sku_package(draft_id, ids, {"length_cm": 13})
    assert error and status in {400, 409}
    assert app.db.load_draft_model(draft_id) == before


def test_package_write_cannot_escape_selected_draft_scope(subject):
    app, draft_id, toolset = subject
    other_id = app.products.duplicate_draft_from_index(draft_id)[0]["draft"]["draft_id"]
    before = app.db.load_draft_model(other_id)
    with pytest.raises(AiToolExecutionError) as error:
        toolset.bindings["draft_sku_package_update"].executor(arguments(other_id), execution(draft_id))
    assert error.value.code == "DRAFT_OUTSIDE_AUTHORIZED_SCOPE"
    assert app.db.load_draft_model(other_id) == before


def test_confirmation_keeps_tools_and_write_receipt_reaches_followup_model(subject, tmp_path):
    """旧权限 metadata 不会再隐藏工具；“是”后可写，追问时模型获得完整写回执。"""
    app, draft_id, toolset = subject
    requests = []

    async def model(messages, info):
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        requests.append(latest)
        assert "async def draft_sku_package_update" in available_python_functions(info)
        assert "async def draft_sku_attributes_update" in available_python_functions(info)
        if latest == "重新读取草稿":
            yield "草稿共用尺寸是 13 × 10 × 5 cm，是否应用到前两个 SKU？"
            return
        returns = business_returns(messages)
        if latest == "是" and not returns:
            assert any(isinstance(p, TextPart) and "13 × 10 × 5" in p.content for m in messages for p in m.parts)
            yield {0: python_call(name="draft_sku_package_update", json_args=json.dumps(arguments(draft_id)), tool_call_id="package-write")}
        else:
            receipt = returns[-1]
            assert receipt.tool_name == "draft_sku_package_update"
            assert receipt.content["changed_count"] == 2
            assert receipt.content["package_dimensions"] == {"length_cm": 13, "width_cm": 10, "height_cm": 5}
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
    assert [p.tool_name for p in business_returns(history)] == ["draft_sku_package_update"]
    assert app.db.load_draft_model(draft_id)["sku_items"][0]["overrides"]["package_dimensions"]["length_cm"] == "13.0"
