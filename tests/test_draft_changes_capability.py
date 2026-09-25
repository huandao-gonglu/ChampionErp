"""成组提交验证原子保存、并发保护、字段范围及原生脚本回执。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units import category_attribute_updates as validation
from erp_web.runtime_units.draft_changes_capability import apply_draft_changes
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.draft_changes import DraftChangesApplyRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.ai_code_mode_helpers import business_returns
from tests.test_draft_edit_capabilities import execution
from tests.test_main_chat_attributes import subject, enum
from tests.test_native_agent_integration import service, body, CONVERSATION


def request(app, draft_id, changes, **kwargs):
    return DraftChangesApplyRequest(draft_id=draft_id, expected_updated_at=app.products.draft_record(draft_id)["updated_at"],
        platform="ozon", site="global", category_id="94765", changes=changes, **kwargs)


def run(app, req, **kwargs):
    context = replace(execution(req.draft_id), permissions=frozenset({"product.write", "draft.write"}), **kwargs)
    return apply_draft_changes(req, product_store=app.products, execution=context)


def test_mixed_changes_save_once_and_preserve_unrelated_fields(subject, monkeypatch):
    app, draft_id, _ = subject
    before = app.products.draft_record(draft_id)
    product = app.db.load_product_model(before["product_id"])
    save = Mock(wraps=app.products.save_draft_content)
    definitions = Mock(wraps=validation.fetch_category_record)
    monkeypatch.setattr(app.products, "save_draft_content", save)
    monkeypatch.setattr(validation, "fetch_category_record", definitions)
    changes = [
        {"sku_id": "s0", "attributes": {"color": "甲"}, "stock": 0, "package_dimensions": {"length_cm": 12}},
        {"attributes": {"7199": "材料"}},
        {"sku_id": "s1", "attributes": {"color": "乙"}, "stock": 13, "package_dimensions": {"width_cm": 7}},
    ]
    result = run(app, request(app, draft_id, changes))
    assert result.changed_count == 3 and result.previous_updated_at == before["updated_at"]
    assert result.updated_at != before["updated_at"]
    assert save.call_count == definitions.call_count == 1
    saved = app.products.draft_record(draft_id)
    for index, (color, stock) in enumerate((("甲", "0"), ("乙", "13"))):
        row = saved["sku_items"][index]
        assert row["attributes_by_target"]["ozon:global"] == {"color": color}
        assert row["attributes_by_target"]["yandex:global"] == {"old": "保留"}
        assert row["stock"] == stock
        assert row["pricing"] == before["sku_items"][index]["pricing"]
        assert "weight_kg" not in row["overrides"]["package_dimensions"]
    assert result.changes[0].stock == "0" and result.changes[0].package_dimensions == {"length_cm": "12.0"}
    assert saved["attributes"]["7199"] == "材料"
    assert saved["title"] == before["title"]
    assert app.db.load_product_model(before["product_id"])["sku_items"] == product["sku_items"]
    repeated = run(app, request(app, draft_id, changes))
    assert not repeated.changed and repeated.updated_at == result.updated_at
    assert save.call_count == 1


@pytest.mark.parametrize("invalid,code", [
    ({"sku_id": "s1", "attributes": {"readonly": "错"}}, "ATTRIBUTE_OUTSIDE_SCOPE"),
    ({"sku_id": "foreign", "stock": 9}, "SKU_OUTSIDE_DRAFT"),
    ({"attributes": {"85": enum("invented", "假值")}}, "ATTRIBUTE_CANDIDATE_UNVERIFIED"),
])
def test_one_invalid_change_prevents_entire_commit(subject, invalid, code):
    app, draft_id, _ = subject
    before = app.products.draft_record(draft_id)
    with pytest.raises(BusinessCapabilityError) as error:
        run(app, request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": "正确"}}, invalid]))
    assert error.value.code == code
    assert app.products.draft_record(draft_id) == before


def test_stale_version_and_write_during_validation_never_overwrite(subject, monkeypatch):
    app, draft_id, record = subject
    req = request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": "目标"}}])
    def concurrent(*args, **kwargs):
        draft = app.products.draft_record(draft_id)
        draft["title"] = "并发编辑的新标题"
        app.products.save_draft_content(draft)
        return deepcopy(record)
    monkeypatch.setattr(validation, "fetch_category_record", concurrent)
    with pytest.raises(BusinessCapabilityError) as error:
        run(app, req)
    assert error.value.code == "DRAFT_VERSION_CONFLICT"
    saved = app.products.draft_record(draft_id)
    assert saved["title"] == "并发编辑的新标题"
    assert "color" not in saved["sku_items"][0]["attributes_by_target"].get("ozon:global", {})
    monkeypatch.setattr(validation, "fetch_category_record", Mock(side_effect=AssertionError("旧版本不应继续查平台")))
    with pytest.raises(BusinessCapabilityError, match="草稿已被修改"):
        run(app, req)


def test_permission_and_selected_draft_boundaries(subject):
    app, draft_id, _ = subject
    req = request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": "目标"}}])
    with pytest.raises(BusinessCapabilityError) as error:
        apply_draft_changes(req, product_store=app.products, execution=execution(draft_id))
    assert error.value.code == "TOOL_PERMISSION_DENIED"
    other_id = app.products.duplicate_draft_from_index(draft_id)[0]["draft"]["draft_id"]
    tool = build_global_chat_toolset(app).bindings["draft_changes_apply"]
    with pytest.raises(AiToolExecutionError) as error:
        tool.executor(request(app, other_id, [{"sku_id": "s0", "stock": 2}]).model_dump(exclude_none=True), execution(draft_id))
    assert error.value.code == "DRAFT_OUTSIDE_AUTHORIZED_SCOPE"


@pytest.mark.parametrize("case", ["unselected", "inactive", "published", "sku_publication"])
def test_uneditable_sku_rejects_entire_group(subject, case):
    app, draft_id, _ = subject
    draft = app.products.draft_record(draft_id)
    if case == "unselected": draft["sku_items"][1]["selected"] = False
    elif case == "published": draft["target_sites"][0]["publish_status"] = "published"
    elif case == "sku_publication": app.db.update_sku_publication(draft_id, "s1", "ozon:global", {"remote_item_id": "remote", "status": "published"})
    else:
        product = app.products.load_product_from_index(draft["product_id"])
        product["sku_items"][1]["active"] = False
        app.products.save_product(product)
    app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
    before = app.products.draft_record(draft_id)
    with pytest.raises(BusinessCapabilityError):
        run(app, request(app, draft_id, [{"sku_id": "s0", "stock": 1}, {"sku_id": "s1", "stock": 2}]))
    assert app.products.draft_record(draft_id) == before


def test_cancel_after_network_validation_never_commits(subject, monkeypatch):
    app, draft_id, record = subject
    before = app.products.draft_record(draft_id)
    canceled = False
    def check():
        if canceled: raise RuntimeError("用户取消")
    def fetch(*args, **kwargs):
        nonlocal canceled
        canceled = True
        return deepcopy(record)
    monkeypatch.setattr(validation, "fetch_category_record", fetch)
    with pytest.raises(RuntimeError, match="用户取消"):
        run(app, request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": "新"}}]), cancellation_check=check)
    assert app.products.draft_record(draft_id) == before


@pytest.mark.parametrize("changes", [
    [{"sku_id": "s0", "stock": -1}], [{"sku_id": "s0", "stock": True}],
    [{"sku_id": "s0", "package_dimensions": {"length_cm": 0}}], [{"stock": 5}],
    [{"sku_id": "s0", "stock": 1}, {"sku_id": "s0", "stock": 2}],
])
def test_invalid_shapes_are_rejected_before_executor(subject, changes):
    app, draft_id, _ = subject
    with pytest.raises(ValidationError): request(app, draft_id, changes)


def test_native_python_group_returns_actual_receipt(subject, tmp_path):
    app, draft_id, _ = subject
    async def model(messages, info):
        if not business_returns(messages):
            code = f'''data = await draft_attributes_read(draft_id={draft_id!r}, platform="ozon", site="global", scope="sku")
changes: list[DraftChange] = [{{"sku_id": row["sku_id"], "stock": index + 3}} for index, row in enumerate(data["skus"])]
result = await draft_changes_apply(draft_id={draft_id!r}, platform="ozon", site="global", expected_updated_at=data["updated_at"], changes=changes)
{{"saved": result["changed_count"]}}'''
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id="group")}
        else:
            assert business_returns(messages)[-1].content["changed_count"] == 2
            yield "两个 SKU 库存已保存。"
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    app._agent_calls = ui.call_store
    asyncio.run(ui.prepare_run(body("将两个 SKU 库存改为 3 和 4", target_draft_ids=[draft_id])).stream(lambda _: None))
    history = ui.chat_service.trusted_history(CONVERSATION)
    result = business_returns(history)[-1].content
    assert result["changes"][0]["stock"] == "3"
    assert result["changes"][1]["stock"] == "4"
    assert [row["stock"] for row in app.products.draft_record(draft_id)["sku_items"]] == ["3", "4"]


def test_198_skus_share_platform_validation_and_one_persistence(subject, monkeypatch):
    app, draft_id, record = subject
    product = app.products.load_product_from_index("product-native-0")
    product["sku_items"] = [{"id": f"s{i}", "active": True} for i in range(198)]
    product["drafts"]["ozon"]["sku_items"] = [
        {"sku_id": f"s{i}", "selected": True, "attributes_by_target": {"ozon:global": {"color": "原值"}}}
        for i in range(198)
    ]
    app.products.save_product(product)
    color = next(item for item in record["attributes"]["required"] if item["id"] == "color")
    color.update(value_mode="strict_enum", dictionary_id=7)
    candidates = Mock(return_value={"values": [{"id": "black", "value": "Черный"}], "has_more": False})
    definitions = Mock(return_value=record)
    save = Mock(wraps=app.products.save_draft_content)
    monkeypatch.setattr(validation, "fetch_category_attribute_values", candidates)
    monkeypatch.setattr(validation, "fetch_category_record", definitions)
    monkeypatch.setattr(app.products, "save_draft_content", save)
    result = run(app, request(app, draft_id, [{"sku_id": f"s{i}", "attributes": {"color": enum("black", "Черный")}} for i in range(198)]))
    assert result.changed_count == len(result.changes) == 198
    assert save.call_count == definitions.call_count == candidates.call_count == 1
    assert all(row["attributes_by_target"]["ozon:global"]["color"] == enum("black", "Черный")
               for row in app.products.draft_record(draft_id)["sku_items"])


def test_group_can_clear_attributes_without_losing_other_targets(subject):
    app, draft_id, _ = subject
    run(app, request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": "黑"}}, {"attributes": {"7199": "材料"}}]))
    result = run(app, request(app, draft_id, [{"sku_id": "s0", "attributes": {"color": None}}, {"attributes": {"7199": None}}]))
    assert result.changed_count == 2
    assert result.changes[0].attributes == {} and result.changes[0].changed_keys == ["color"]
    assert result.changes[1].attributes == {} and result.changes[1].changed_keys == ["7199"]
    assert app.products.draft_record(draft_id)["sku_items"][0]["attributes_by_target"]["yandex:global"] == {"old": "保留"}
