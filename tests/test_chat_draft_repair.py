"""真实失败场景的业务回归：写入前校验、依赖失败和并发字段隔离。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.product_capabilities import prepare_product_images
from erp_web.schemas.product_capabilities import ProductImagesPrepareRequest
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.services.capability_errors import CapabilityInputRequired
from erp_web.services import copy_service
from tests.test_market_prepare_capabilities import _Products, _draft


def test_model_cannot_bypass_category_comparison_with_search_result_id():
    tool = build_global_chat_toolset(get_context()).bindings["category_match"]
    with pytest.raises(AiToolExecutionError) as error:
        tool.executor({"draft_id": "not-read", "target_platform": "mercadolibre",
                       "category_id": "projector-replacement-lamps"},
                      AiExecutionContext.create(timeout_seconds=20, budget_profile="test"))
    assert error.value.code == "CATEGORY_USER_SELECTION_REQUIRED"




def test_repeated_category_outage_is_known_rejection_before_domain_execution(monkeypatch):
    app = get_context()
    monkeypatch.setattr(app.agent_calls, "current_turn_receipts", lambda _: [{
        "tool_name": "category_match", "arguments": {"draft_id": str(index), "target_platform": "mercadolibre"},
        "output": {"error": {"code": "CATEGORY_FETCH_FAILED", "retryable": True}},
    } for index in range(3)])
    tool = build_global_chat_toolset(app).bindings["category_match"]
    with pytest.raises(AiToolExecutionError) as error:
        tool.executor({"draft_id": "not-read", "target_platform": "mercadolibre"},
                      AiExecutionContext.create(timeout_seconds=20, budget_profile="test"))
    assert error.value.code == "CATEGORY_SERVICE_UNAVAILABLE"
    assert error.value.retryable is False


def test_170_images_cannot_be_saved_before_result_validation():
    store = _Products()
    store.product["source"]["image_pool"] = [
        {"id": f"image-{i}", "url": f"https://example.com/{i}.jpg", "status": "ready"}
        for i in range(170)
    ]
    before = deepcopy(store.drafts)
    with pytest.raises(CapabilityInputRequired, match="超过 100"):
        prepare_product_images(ProductImagesPrepareRequest(draft_id="draft-1"), product_store=store)
    assert store.save_draft_calls == 0
    assert store.drafts == before


def test_existing_image_selection_is_preserved():
    draft = _draft("draft-1")
    draft["images"] = [{"asset_id": "image-1", "role": "main", "order": 0}]
    store = _Products([draft])
    store.product["source"]["image_pool"].append({
        "id": "extra", "url": "https://example.com/extra.jpg", "status": "ready",
    })
    result = prepare_product_images(ProductImagesPrepareRequest(draft_id="draft-1"), product_store=store)
    assert result.image_asset_ids == ["image-1"]




def test_copy_context_preserves_conflicting_facts():
    product = {"name": "专利款投影灯", "attributes": {"类型": "普通投影灯"},
               "source": {"attributes": {"是否有专利": "否"}}}
    summary = copy_service.product_summary(product)
    assert "是否有专利" in summary and "否" in summary
    assert "普通投影灯" in summary















def test_full_category_path_is_reviewed_before_accepting_a_discovery_label():
    from erp_web.schemas.category import CategoryCandidateLedger
    from erp_web.services.category_match_agent_service import CategoryMatchAgentOutput, CategoryMatchOutputValidator
    ledger = CategoryCandidateLedger()
    ledger.record_attempt("night light")
    ledger.add_result({"keyword": "night light", "source": "test", "candidates": [
        {"category_id": "part", "path_segments": ["Projector lamps", "Lamps"]},
        {"category_id": "light", "path_segments": ["Table lamps", "Night lights"]},
    ]})
    paths = {"part": "Electronics / Projectors / Replacement Parts / Lamps", "light": "Home / Lighting / Night lights"}
    validator = CategoryMatchOutputValidator(ledger,
        lambda category_id, **kwargs: {"category_path": paths[category_id]})
    ctx = SimpleNamespace(deps=SimpleNamespace(execution_context=AiExecutionContext.create(timeout_seconds=10, budget_profile="test")))
    result = CategoryMatchAgentOutput(selected_category_id="part", selected_category_path=["Projector lamps", "Lamps"],
        type_relationship="same_type", physical_comparison={"product_form": "USB 小夜灯", "category_form": "夜灯", "compatible": True},
        abstained=False, model_confidence=0.9, evidence=["带投影功能的小夜灯"])
    with pytest.raises(ModelRetry, match="Replacement Parts"):
        validator(ctx, result)
    result.selected_category_id = "light"
    result.selected_category_path = paths["light"].split(" / ")
    assert validator(ctx, result).selected_category_id == "light"


@pytest.mark.parametrize("separator", [";", "；", ",", "，"])
def test_collection_options_cannot_be_saved_as_one_joined_value(separator):
    from erp_web.schemas.category import category_attribute_value_is_valid
    definition = {"id": "purpose", "value_mode": "open_enum", "is_collection": True,
                  "options": ["для зубов", "интерактивные / развивающие"]}
    joined = separator.join(definition["options"])
    assert not category_attribute_value_is_valid(definition, {"values": [{"value": joined}]})
    assert category_attribute_value_is_valid(definition, {"values": [
        {"value": value} for value in definition["options"]]})
    definition["options"].append(joined)
    assert category_attribute_value_is_valid(definition, {"values": [{"value": joined}]})
