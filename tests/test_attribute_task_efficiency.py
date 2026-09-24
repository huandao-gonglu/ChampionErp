"""属性任务的输入规模、短枚举校验及原生多目标执行回归。"""

import asyncio
import json

import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units import category_attribute_updates as validation
from erp_web.runtime_units.product_capabilities import ProductCapabilityScope, draft_attributes_read, update_product_attributes
from erp_web.runtime_units.product_write_capabilities import ProductWriteCapabilityScope, draft_read
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.product_capabilities import DraftAttributesReadRequest
from erp_web.schemas.product_write_capabilities import DraftReadRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_main_chat_attributes import subject, enum, request
from tests.test_native_agent_integration import service, body


@pytest.mark.parametrize("value", ["1", "0"])
def test_short_ozon_enum_is_verified_across_dictionary_pages(subject, monkeypatch, value):
    app, draft_id, record = subject
    record["attributes"]["optional"].append({"id": "8642", "name": "Число полотен", "value_mode": "strict_enum"})
    calls = []

    def candidates(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["query"] == ""
        if not kwargs["cursor"]:
            return {"values": [{"id": "other", "value": "2"}], "has_more": True, "next_cursor": "next"}
        return {"values": [{"id": "44750", "value": value}], "has_more": False}

    monkeypatch.setattr(validation, "fetch_category_attribute_values", candidates)
    result = update_product_attributes(request(draft_id, {"8642": enum("44750", value)}), product_store=app.products)
    assert result.attributes["8642"] == enum("44750", value)
    assert len(calls) == 2
    before = app.products.draft_record(draft_id)
    with pytest.raises(BusinessCapabilityError, match="无法确认"):
        update_product_attributes(request(draft_id, {"8642": enum("invented", value)}), product_store=app.products)
    assert app.products.draft_record(draft_id) == before


def test_local_enum_query_error_is_not_retryable(subject, monkeypatch):
    app, draft_id, _ = subject

    def invalid(*args, **kwargs):
        raise ValueError("查询参数无效")

    monkeypatch.setattr(validation, "fetch_category_attribute_values", invalid)
    with pytest.raises(BusinessCapabilityError) as error:
        update_product_attributes(request(draft_id, {"85": enum("no-brand", "Нет бренда")}), product_store=app.products)
    assert error.value.code == "ATTRIBUTE_CANDIDATE_QUERY_INVALID"
    assert error.value.retryable is False


def test_query_parameter_error_and_network_error_have_different_retry_semantics():
    from erp_web.runtime_units.category_query_capabilities import _live_api_error

    invalid = _live_api_error(ValueError("搜索词过短"))
    unavailable = _live_api_error(TimeoutError("接口超时"))
    assert invalid.code == "CATEGORY_QUERY_INVALID" and not invalid.retryable
    assert unavailable.code == "CATEGORY_LIVE_API_FAILED" and unavailable.retryable


def test_managed_field_rejected_before_any_enum_lookup(subject, monkeypatch):
    app, draft_id, record = subject
    record["attributes"]["optional"].append({"id": "9048", "name": "Название модели (для объединения в одну карточку)"})
    monkeypatch.setattr(validation, "fetch_category_attribute_values", lambda *a, **k: pytest.fail("托管字段应在候选查询之前拒绝。"))
    with pytest.raises(BusinessCapabilityError) as error:
        update_product_attributes(request(draft_id, {"85": enum("no-brand", "Нет бренда"), "9048": "wrong"}), product_store=app.products)
    assert error.value.code == "ATTRIBUTE_MANAGED_BY_DRAFT"
    assert error.value.retryable is False


def test_common_attribute_context_size_does_not_grow_with_sku_count(subject):
    app, draft_id, _ = subject
    product = app.products.load_product_from_index("product-native-0")
    product["source"]["attributes"] = {"材质": "亚克力", "钻型": "珍珠钻"}
    product["attributes"] = {"补充": "手工"}
    product["drafts"]["ozon"]["target_sites"].append({"platform": "yandex", "site": "global", "category_id": "64096378", "attributes": {"existing": "保留"}})
    app.products.save_product(product)
    small = draft_attributes_read(DraftAttributesReadRequest(draft_id=draft_id), ProductCapabilityScope(app.products))
    product = app.products.load_product_from_index("product-native-0")
    product["sku_items"] = [{"id": f"sku-{i}", "active": True, "name": f"图案{i} 20X20CM / 30X30CM", "options": {"规格": "30X30CM"}} for i in range(198)]
    product["drafts"]["ozon"]["sku_items"] = [{"sku_id": f"sku-{i}", "selected": True} for i in range(198)]
    app.products.save_product(product)
    compact = draft_attributes_read(DraftAttributesReadRequest(draft_id=draft_id), ProductCapabilityScope(app.products))
    old = draft_read(DraftReadRequest(draft_id=draft_id), ProductWriteCapabilityScope(app.products))
    assert compact.sku_count == 198 and compact.skus == [] and compact.next_offset is None
    assert len(compact.targets) == 2
    assert compact.product.source_attributes == small.product.source_attributes
    assert compact.product.attributes == {"补充": "手工"}
    assert len(compact.model_dump_json()) < len(small.model_dump_json()) + 100
    assert len(compact.model_dump_json()) < len(old.model_dump_json()) / 5
    assert "image_pool" not in compact.model_dump_json()
    assert "图案197" not in compact.model_dump_json()


def test_query_scope_excludes_managed_fields_without_losing_pagination(subject):
    app, _, record = subject
    from erp_web.runtime_units.category_query_capabilities import CategoryQueryCapabilityScope, category_attributes_query
    from erp_web.schemas.category_query_capabilities import CategoryAttributesQueryRequest

    rows = [{"id": "9048", "name": "Название модели (для объединения в одну карточку)", "required": True},
            {"id": "color", "name": "颜色", "variation_role": "variant"},
            {"id": "readonly", "name": "只读", "read_only": True},
            {"id": "85", "name": "Бренд", "required": True}]
    def page(*args, cursor="", **kwargs):
        return {"attributes": rows[3:] if cursor else rows[:3], "has_more": not bool(cursor), "next_cursor": "next" if not cursor else ""}
    scope = CategoryQueryCapabilityScope(lambda **k: [], page, lambda **k: {}, lambda **k: {}, lambda b: ({}, None, 200), lambda b: ({}, None, 200))
    execution = AiExecutionContext.create(timeout_seconds=30, budget_profile="test")
    first = category_attributes_query(CategoryAttributesQueryRequest(platform="ozon", category_id="94765"), scope, execution)
    assert not first.attributes and first.has_more
    assert [row["write_scope"] for row in first.excluded_attributes] == ["managed", "sku", "read_only"]
    second = category_attributes_query(CategoryAttributesQueryRequest(platform="ozon", category_id="94765", cursor=first.next_cursor), scope, execution)
    assert second.attributes[0]["id"] == "85" and second.attributes[0]["write_scope"] == "common"
    assert not second.has_more


def test_native_two_target_common_attributes_finish_in_five_model_requests(subject, tmp_path):
    """验证生产工具可在五轮内完成；可控模型的轮数不等同于真实模型测速。"""
    app, draft_id, _ = subject
    draft = app.db.load_draft_model(draft_id)
    draft["target_sites"].append({"platform": "yandex", "site": "global", "category_id": "94765", "attributes": {}})
    app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
    targets = [{"platform": platform, "site": "global", "category_id": "94765"} for platform in ("ozon", "yandex")]
    rounds = [
        [("draft_attributes_read", {"draft_id": draft_id})],
        [("category_attributes_query", target) for target in targets],
        [("category_attribute_values_query", {**target, "attribute_id": attr}) for target in targets for attr in ("85", "4389")],
        [("product_attributes_update", {**target, "draft_id": draft_id, "updates": {"85": enum("no-brand", "Нет бренда"), "4389": enum("china", "Китай"), "7199": "Резина"}}) for target in targets],
    ]
    calls = []

    async def model(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        assert all(part.content.get("ok") is not False for part in returns)
        round_index = len(calls)
        calls.append(len(returns))
        if round_index < len(rounds):
            yield {i: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"round-{round_index}-{i}") for i, (name, args) in enumerate(rounds[round_index])}
        else:
            assert len(returns) == 9
            assert all(part.content["missing_required_attribute_ids"] == [] for part in returns[-2:])
            yield "两个目标各保存品牌、产地、材质；其余缺资料项和 SKU 属性未填写。"

    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    asyncio.run(ui.prepare_run(body("填写这个草稿的公共属性", target_draft_ids=[draft_id])).stream(lambda _: None))
    assert len(calls) == 5
    saved = app.db.load_draft_model(draft_id)
    assert all(target["attributes"]["7199"] == "Резина" for target in saved["target_sites"])
