"""真实失败场景的业务回归：范围、目标覆盖、写入前校验和并发字段隔离。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.product_capabilities import prepare_product_images
from erp_web.schemas.product_capabilities import ProductImagesPrepareRequest
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.copy import CopyQualityReview
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.capability_errors import CapabilityInputRequired
from erp_web.services import copy_service
from tests.test_market_prepare_capabilities import _Products, _draft


def test_unrequested_compound_operation_is_blocked_before_writes():
    app = get_context()
    tool = build_global_chat_toolset(app).bindings["draft_prepare_for_market"]
    execution = AiExecutionContext.create(
        timeout_seconds=20, budget_profile="test", allow_write=True,
        permissions={"product.write"}, business_scope={
            "allowed_write_tools": json.dumps(["category_match", "category_match"]),
        },
    )
    with pytest.raises(AiToolExecutionError, match="超出用户本轮要求"):
        tool.executor({"draft_id": "not-read", "target_platform": "ozon"}, execution)




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


@pytest.mark.parametrize("retry,timeout", [(0, 300), (2, 300), (0, 30)])
def test_all_targets_are_checked_without_a_model_query(retry, timeout):
    support = SimpleNamespace(all_drafts=True, allowed_write_tools={"category_match"}, target_draft_ids=(),
        target_reader=lambda: [{"draft_id": "d", "raw": {"target_sites": [
            {"platform": "ozon", "site": "global", "category_id": ""},
        ]}}])
    ctx = SimpleNamespace(messages=[], run_id="run", retry=retry, deps=SimpleNamespace(
        tool_runtime=SimpleNamespace(run_support=support),
        execution_context=AiExecutionContext.create(timeout_seconds=timeout, budget_profile="test"),
    ))
    with pytest.raises(ModelRetry, match="d/ozon/global"):
        GlobalAgentChatService.validate_target_coverage(ctx, "全部完成")


def test_all_targets_are_checked_including_secondary_platform():
    support = SimpleNamespace(all_drafts=True, allowed_write_tools={"category_match"})
    messages = [ModelRequest(parts=[ToolReturnPart("drafts_query", {"items": [{
        "draft_id": "d", "targets": [{"platform": p, "site": "global", "category_id": ""}
                                      for p in ("yandex", "ozon")],
    }]}, tool_call_id="query")], run_id="run"), ModelResponse(parts=[ToolCallPart(
        "category_match", {"draft_id": "d", "target_platform": "yandex", "site": "global"},
        tool_call_id="match",
    )], run_id="run")]
    ctx = SimpleNamespace(messages=messages, run_id="run", retry=0, deps=SimpleNamespace(
        tool_runtime=SimpleNamespace(run_support=support),
        execution_context=AiExecutionContext.create(timeout_seconds=300, budget_profile="test"),
    ))
    with pytest.raises(ModelRetry, match="d/ozon/global"):
        GlobalAgentChatService.validate_target_coverage(ctx, "全部完成")


def test_unexecuted_tool_call_does_not_count_as_target_coverage():
    support = SimpleNamespace(all_drafts=True, allowed_write_tools={"category_match"},
        target_draft_ids=(), target_reader=lambda: [{"draft_id": "d", "raw": {
            "target_sites": [{"platform": "ozon", "site": "global"}]}}])
    messages = [ModelResponse(parts=[ToolCallPart("category_match", {
        "draft_id": "d", "target_platform": "ozon"}, tool_call_id="unavailable")], run_id="run")]
    ctx = SimpleNamespace(messages=messages, run_id="run", retry=0, deps=SimpleNamespace(
        tool_runtime=SimpleNamespace(run_support=support),
        execution_context=AiExecutionContext.create(timeout_seconds=300, budget_profile="test")))
    with pytest.raises(ModelRetry, match="d/ozon/global"):
        GlobalAgentChatService.validate_target_coverage(ctx, "全部完成")
    messages.append(ModelRequest(parts=[ToolReturnPart("category_match", {
        "ok": False, "error": {"code": "CAPABILITY_INPUT_REQUIRED"}}, tool_call_id="unavailable")], run_id="run"))
    assert GlobalAgentChatService.validate_target_coverage(ctx, "已执行，缺少品牌资料") == "已执行，缺少品牌资料"


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




def test_copy_context_preserves_conflicting_facts_and_reports_review_problems(monkeypatch):
    product = {"name": "专利款投影灯", "selling_points": ["商家好评率 99%"],
               "source": {"attributes": {"是否有专利": "否"}}}
    summary = copy_service.product_summary(product)
    assert "是否有专利" in summary and "否" in summary
    assert "好评率" not in summary
    monkeypatch.setattr(copy_service.ai_gateway, "chat_structured", lambda *a, **k:
                        CopyQualityReview(language_matches=False, unsupported_claims=["无依据的专利声明"], explanation="目标是葡语，实际为西语"))
    review = copy_service.review_copy_quality(
        ".", {}, summary, "pt-BR", {"title": "Tazón"}, timeout_seconds=30,
    )
    assert review.language_matches is False
    assert review.unsupported_claims == ["无依据的专利声明"]
    assert review.explanation == "目标是葡语，实际为西语"














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
