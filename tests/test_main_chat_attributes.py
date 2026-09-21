"""主对话读取、查询、写入属性的业务验收；模型和平台网络使用可控替身。"""

import asyncio
import json
from copy import deepcopy

import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.facades import agent_capability_facade
from erp_web.runtime_units import category_attribute_updates as validation
from erp_web.runtime_units.product_capabilities import (
    ProductCapabilityScope, draft_attributes_read, product_read, update_product_attributes,
)
from erp_web.runtime_units.product_write_capabilities import (
    ProductWriteCapabilityScope, draft_read,
)
from erp_web.schemas.product_capabilities import (
    DraftAttributesReadRequest, DraftSkuAttributesUpdateRequest, ProductAttributesUpdateRequest, ProductReadRequest,
)
from erp_web.schemas.product_write_capabilities import DraftReadRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_native_domain_workflow import setup_domain
from tests.test_native_agent_integration import service, body


def enum(value_id, value):
    return {"values": [{"dictionary_value_id": value_id, "value": value}]}


@pytest.fixture
def subject(monkeypatch):
    app, ids = setup_domain(monkeypatch)
    draft_id = ids[0]
    product = app.products.load_product_from_index("product-native-0")
    product["sku_items"] = [{"id": f"s{i}", "active": True, "name": f"规格{i}",
                             "options": {"颜色": color}, "package_dimensions": {"weight_kg": str(i + 1)}}
                            for i, color in enumerate(("黑色", "白色"))]
    product["drafts"]["ozon"]["sku_items"] = [
        {"sku_id": f"s{i}", "selected": True, "attributes_by_target": {"yandex:global": {"old": "保留"}}}
        for i in range(2)]
    product["drafts"]["ozon"]["category_id"] = "94765"
    for target in product["drafts"]["ozon"].get("target_sites", []):
        target["category_id"] = "94765"
    app.products.save_product(product)
    definitions = [
        {"id": "85", "name": "Бренд", "required": True, "value_mode": "strict_enum", "dictionary_id": 1},
        {"id": "4389", "name": "Страна", "required": True, "value_mode": "strict_enum", "dictionary_id": 2},
        *[{"id": f"optional-{i}", "name": f"可选{i}", "required": False} for i in range(22)],
        {"id": "7199", "name": "Материал", "required": False},
        {"id": "color", "name": "Цвет", "required": True, "variation_role": "variant"},
        {"id": "readonly", "name": "只读", "read_only": True},
    ]
    record = {"category_id": "94765", "platform": "ozon", "site": "global", "attributes": {
        "required": [attr for attr in definitions if attr.get("required")],
        "optional": [attr for attr in definitions if not attr.get("required")],
    }}
    def values(platform, category_id, attribute_id, **kwargs):
        return {"values": [{"id": "no-brand", "value": "Нет бренда"}] if attribute_id == "85" else [{"id": "china", "value": "Китай"}],
                "has_more": False, "next_cursor": ""}
    def page(platform, category_id, *, cursor="", limit=20, **kwargs):
        offset = int(cursor or 0)
        end = offset + limit
        return {"category_id": category_id, "attributes": definitions[offset:end],
                "next_cursor": str(end) if end < len(definitions) else "", "has_more": end < len(definitions)}
    monkeypatch.setattr(validation, "fetch_category_record", lambda *args, **kwargs: deepcopy(record))
    monkeypatch.setattr(validation, "fetch_category_attribute_values", values)
    monkeypatch.setattr(agent_capability_facade, "fetch_category_attribute_page", page)
    monkeypatch.setattr(agent_capability_facade, "fetch_category_attribute_values", values)
    return app, draft_id, record


def request(draft_id, updates, **extra):
    return ProductAttributesUpdateRequest(draft_id=draft_id, platform="ozon", site="global", category_id="94765", updates=updates, **extra)


def test_public_and_sku_writes_preserve_other_fields_and_report_actual_saved_values(subject):
    app, draft_id, _ = subject
    before = app.products.draft_record(draft_id)
    result = update_product_attributes(request(draft_id, {"85": enum("no-brand", "Нет бренда"), "7199": "Резина"}), product_store=app.products)
    assert result.missing_required_attribute_ids == ["4389"]
    assert result.attributes["7199"] == "Резина"
    repeated = update_product_attributes(request(draft_id, {"7199": "Резина"}), product_store=app.products)
    assert not repeated.changed
    sku_request = DraftSkuAttributesUpdateRequest(**request(draft_id, {"color": "Черный"}).model_dump(), sku_id="s0")
    sku = update_product_attributes(sku_request, product_store=app.products)
    assert sku.sku_id == "s0" and sku.attributes == {"color": "Черный"}
    saved = app.products.draft_record(draft_id)
    assert saved["sku_items"][1] == before["sku_items"][1]
    assert saved["sku_items"][0]["attributes_by_target"]["yandex:global"] == {"old": "保留"}
    assert saved["attributes"] == result.attributes
    assert saved["title"] == before["title"]
    assert app.products.load_product_from_index("product-native-0")["brand"] == "Champion"


@pytest.mark.parametrize("updates,code", [
    ({"unknown": "x"}, "ATTRIBUTE_OUTSIDE_SCOPE"),
    ({"readonly": "x"}, "ATTRIBUTE_OUTSIDE_SCOPE"),
    ({"color": "黑色"}, "ATTRIBUTE_OUTSIDE_SCOPE"),
    ({"85": "Нет бренда"}, "ATTRIBUTE_VALUE_INVALID"),
    ({"7199": "Резина", "85": enum("invented", "Нет бренда")}, "ATTRIBUTE_CANDIDATE_UNVERIFIED"),
    ({"85": enum("no-brand", "某品牌")}, "ATTRIBUTE_CANDIDATE_UNVERIFIED"),
    ({"85": {"values": [*enum("no-brand", "Нет бренда")["values"], "junk"]}}, "ATTRIBUTE_VALUE_INVALID"),
])
def test_invalid_field_or_candidate_never_partially_writes(subject, updates, code):
    app, draft_id, _ = subject
    before = app.products.draft_record(draft_id)
    with pytest.raises(BusinessCapabilityError) as exc:
        update_product_attributes(request(draft_id, updates), product_store=app.products)
    assert exc.value.code == code
    assert app.products.draft_record(draft_id) == before


def test_sku_write_rejects_common_fields_and_foreign_sku(subject):
    app, draft_id, _ = subject
    for sku_id, updates, code in [("s0", {"7199": "Резина"}, "ATTRIBUTE_OUTSIDE_SCOPE"),
                                  ("foreign", {"color": "Черный"}, "SKU_OUTSIDE_DRAFT")]:
        with pytest.raises(BusinessCapabilityError) as exc:
            update_product_attributes(DraftSkuAttributesUpdateRequest(**request(draft_id, updates).model_dump(), sku_id=sku_id), product_store=app.products)
        assert exc.value.code == code


def test_category_change_during_platform_lookup_is_rejected(subject, monkeypatch):
    app, draft_id, _ = subject
    def changed(*args, **kwargs):
        draft = app.db.load_draft_model(draft_id)
        draft["category_id"] = draft["target_sites"][0]["category_id"] = "changed"
        app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
        return {"values": [{"id": "no-brand", "value": "Нет бренда"}]}
    monkeypatch.setattr(validation, "fetch_category_attribute_values", changed)
    with pytest.raises(BusinessCapabilityError) as exc:
        update_product_attributes(request(draft_id, {"85": enum("no-brand", "Нет бренда")}), product_store=app.products)
    assert exc.value.code == "CATEGORY_CHANGED"
    assert "85" not in app.products.draft_record(draft_id)["attributes"]


def test_paginated_read_preserves_each_sku_facts_and_all_saved_attributes(subject):
    app, draft_id, _ = subject
    scope = ProductCapabilityScope(app.products)
    first = draft_attributes_read(DraftAttributesReadRequest(draft_id=draft_id, limit=1), scope)
    second = draft_attributes_read(DraftAttributesReadRequest(draft_id=draft_id, offset=first.next_offset, limit=1), scope)
    assert first.sku_count == 2 and second.next_offset is None
    assert first.skus[0]["options"] == {"颜色": "黑色"}
    assert second.skus[0]["options"] == {"颜色": "白色"}
    assert first.skus[0]["package_dimensions"] != second.skus[0]["package_dimensions"]


@pytest.mark.parametrize("missing_dimension", ["", "0.0"])
def test_read_tools_preserve_shared_dimensions_without_filling_missing_sku_dimensions(
    subject, missing_dimension,
):
    """复现主档为空、草稿有尺寸、SKU 未填的现场，三个读取入口都保留事实层级。"""
    app, draft_id, _ = subject
    dimensions = {"length_cm": "13", "width_cm": "10", "height_cm": "5", "weight_kg": "0.65"}
    product = app.products.load_product_from_index("product-native-0")
    product["dimensions"] = ""
    product["source"]["dimensions"] = {}
    for sku in product["sku_items"]:
        sku["package_dimensions"].update({
            key: missing_dimension for key in ("length_cm", "width_cm", "height_cm")
        })
    draft = product["drafts"]["ozon"]
    draft["package_dimensions"] = dimensions
    draft["sku_items"][1]["overrides"] = {"package_dimensions": {"length_cm": "26"}}
    app.products.save_product(product)
    before = app.products.load_product_from_index("product-native-0")
    scope = ProductCapabilityScope(app.products)

    draft_result = draft_read(
        DraftReadRequest(draft_id=draft_id), ProductWriteCapabilityScope(app.products),
    ).model_dump(mode="json")
    product_result = product_read(
        ProductReadRequest(draft_id=draft_id, platform="ozon", site="global"), scope,
    ).model_dump(mode="json")
    assert draft_result["draft"]["package_dimensions"] == dimensions
    assert product_result["draft"]["package_dimensions"] == dimensions
    assert product_result["product"]["dimensions"] == ""

    for offset in (0, 1):
        result = draft_attributes_read(
            DraftAttributesReadRequest(draft_id=draft_id, offset=offset, limit=1), scope,
        ).model_dump(mode="json")
        assert result["package_dimensions"] == dimensions
        sku_dimensions = result["skus"][0]["package_dimensions"]
        assert sku_dimensions["length_cm"] == (missing_dimension if offset == 0 else "26")
        assert sku_dimensions["width_cm"] == sku_dimensions["height_cm"] == missing_dimension
        assert sku_dimensions["weight_kg"] == str(offset + 1)
    assert app.products.load_product_from_index("product-native-0") == before


def test_native_main_chat_reads_queries_and_writes_without_a_focused_agent(subject, tmp_path):
    app, draft_id, _ = subject
    seen = []
    target = {"platform": "ozon", "site": "global", "category_id": "94765"}
    steps = [
        ("product_read", {"draft_id": draft_id}),
        ("draft_attributes_read", {"draft_id": draft_id}),
        ("category_attributes_query", {**target, "limit": 20}),
        ("category_attributes_query", {**target, "limit": 20, "cursor": "20"}),
        ("category_attribute_values_query", {**target, "attribute_id": "85", "query": "Нет бренда"}),
        ("category_attribute_values_query", {**target, "attribute_id": "4389", "query": "Китай"}),
        ("product_attributes_update", {"draft_id": draft_id, **target, "updates": {"85": enum("no-brand", "Нет бренда"), "4389": enum("china", "Китай"), "7199": "Резина"}}),
        ("draft_sku_attributes_update", {"draft_id": draft_id, **target, "sku_id": "s0", "updates": {"color": "Черный"}}),
        ("draft_attributes_read", {"draft_id": draft_id}),
    ]
    async def model(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returns:
            assert returns[-1].content.get("ok") is not False, returns[-1].content
        seen[:] = [part.tool_name for part in returns]
        if len(returns) < len(steps):
            name, args = steps[len(returns)]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"step-{len(returns)}")}
        else:
            assert returns[3].content["attributes"][4]["id"] == "7199"
            saved = returns[-1].content
            assert saved["attributes"]["85"] == enum("no-brand", "Нет бренда")
            assert saved["skus"][0]["attributes"]["color"] == "Черный"
            yield "已保存公共品牌、产地、材质和指定 SKU 颜色；其他缺资料项保持未填。"
    ui = service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    asyncio.run(ui.prepare_run(body("填写公共属性和 s0 的颜色，保留类目", target_draft_ids=[draft_id])).stream(lambda _: None))
    assert seen == [name for name, _ in steps]
    assert app.products.draft_record(draft_id)["attributes"]["7199"] == "Резина"


def test_concurrent_edit_to_same_attribute_is_not_overwritten(subject, monkeypatch):
    app, draft_id, _ = subject
    def changed(*args, **kwargs):
        draft = app.db.load_draft_model(draft_id)
        draft["attributes"]["85"] = enum("manual", "人工品牌")
        draft["target_sites"][0]["attributes"]["85"] = enum("manual", "人工品牌")
        app.db.upsert_draft_model(draft["product_id"], draft["platform"], draft)
        return {"values": [{"id": "no-brand", "value": "Нет бренда"}]}
    monkeypatch.setattr(validation, "fetch_category_attribute_values", changed)
    with pytest.raises(BusinessCapabilityError) as exc:
        update_product_attributes(request(draft_id, {"85": enum("no-brand", "Нет бренда")}), product_store=app.products)
    assert exc.value.code == "DRAFT_ATTRIBUTES_CHANGED"
    assert app.products.draft_record(draft_id)["attributes"]["85"] == enum("manual", "人工品牌")


def test_mercado_brand_updates_canonical_draft_field_and_keeps_platform_identity(monkeypatch):
    from tests.test_product_capability_service import _Products
    from erp_web.services.mercadolibre_attribute_contract import compile_mercadolibre_attributes
    from erp_web.runtime_units.category_definition_support import definition_from_legacy_attributes
    products = _Products()
    products.draft["brand"] = "来源品牌"
    attr = {"id": "BRAND", "name": "Brand", "required": True, "value_type": "string", "value_mode": "strict_enum", "has_more_values": True}
    monkeypatch.setattr(validation, "fetch_category_record", lambda *args, **kwargs: {"category_id": "CAT-1", "attributes": {"required": [attr]}})
    monkeypatch.setattr(validation, "fetch_category_attribute_values", lambda *args, **kwargs: {"values": [{"id": "123", "value": "Generic"}]})
    result = update_product_attributes(ProductAttributesUpdateRequest(draft_id="draft-1", platform="mercadolibre", site="MLM", category_id="CAT-1", updates={"BRAND": enum("123", "Generic")}), product_store=products)
    assert products.draft["brand"] == "Generic"
    assert result.draft_fields == {"brand": "Generic"}
    definition = definition_from_legacy_attributes(platform="mercadolibre", site="MLM", category_id="CAT-1", category_path="Fans", required=[attr], optional=[])
    compiled = compile_mercadolibre_attributes(products.draft, definition, listing_model="traditional")
    assert not compiled.issues
    assert compiled.attributes[0]["value_id"] == "123"
    with pytest.raises(BusinessCapabilityError) as exc:
        update_product_attributes(ProductAttributesUpdateRequest(draft_id="draft-1", platform="mercadolibre", site="MLM", category_id="CAT-1", updates={"SELLER_SKU": "invalid-owner"}), product_store=products)
    assert exc.value.code == "ATTRIBUTE_MANAGED_BY_DRAFT"
