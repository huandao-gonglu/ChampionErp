"""验证多草稿批处理提速优化：批量能力、低思考默认策略、O(log N) 祖先查询与 SKU 索引优化。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.runtime_units import category_capabilities as cat_cap
from erp_web.runtime_units import copy_generation
from erp_web.runtime_units import product_capabilities as prod_cap
from erp_web.runtime_units import publish_capabilities as pub_cap
from erp_web.runtime_units.publish_validation_session import PublishValidationSession
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchBatchRequest,
    CategoryMatchCapabilityResult,
)
from erp_web.schemas.product_capabilities import (
    DraftSkuVariantsCheckBatchRequest,
    DraftSkuVariantsCheckResult,
)
from erp_web.schemas.publish_capabilities import (
    ProductPublishSummary,
    ProductPublishValidateBatchRequest,
    ProductPublishValidationResult,
)
from erp_web.services import ai_model_factory
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputOption,
    CapabilityInputRequired,
)
from erp_web.services.category_match_agent_service import CATEGORY_MATCH_AGENT_PROFILE


def _model_cfg(
    *,
    model_id: str,
    model_name: str,
    provider_id: str = "openai",
    api_style: str = "openai_compatible",
    base_url: str = "https://v2.pincc.ai",
    thinking_enabled: bool | None = None,
    extra: dict | None = None,
) -> dict:
    cfg: dict = {
        "id": model_id,
        "name": model_id,
        "connection_type": "api",
        "provider_id": provider_id,
        "api_style": api_style,
        "base_url": base_url,
        "api_key": "sk-test",
        "model": model_name,
        "capabilities": ["chat", "json", "tool_calling"],
        "enabled": True,
    }
    if thinking_enabled is not None:
        cfg["thinking_enabled"] = thinking_enabled
    if extra is not None:
        cfg["extra"] = extra
    return cfg


def test_low_thinking_defaults_respect_model_profile_and_user_overrides(tmp_path):
    app_cfg = {
        "ai_models": [
            _model_cfg(model_id="gpt6", model_name="gpt-6.2-sol", api_style="openai_compatible"),
            _model_cfg(model_id="dsflash", model_name="deepseek-flash", api_style="openai_responses"),
            _model_cfg(model_id="gpt52", model_name="gpt-5.2", api_style="openai_responses"),
            _model_cfg(model_id="gpt4o", model_name="gpt-4o", api_style="openai_compatible"),
            _model_cfg(
                model_id="gpt6_explicit_on",
                model_name="gpt-6.2-sol",
                api_style="openai_compatible",
                thinking_enabled=True,
            ),
        ],
        "ai_use_case_bindings": {
            "category.product_match": {"model_id": "gpt6"},
            "copy.generate": {"model_id": "dsflash"},
            "global.chat": {"model_id": "gpt6"},
        },
    }

    # category.product_match 绑 gpt-6.2-sol（always_thinking）自动降为 low。
    cat_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "category.product_match",
    )
    assert cat_binding.model_settings.get("thinking") == "low"

    # copy.generate 绑 deepseek-flash（默认思考但未声明 effort=none）自动降为 low。
    copy_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "copy.generate",
    )
    assert copy_binding.model_settings.get("thinking") == "low"

    # global.chat 不在低思考默认列表中，保持未注入。
    chat_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "global.chat",
    )
    assert "thinking" not in chat_binding.model_settings

    # 支持 effort=none 的模型（gpt-5.2）在 copy.generate 默认关闭 thinking。
    gpt52_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "copy.generate", model_id="gpt52",
    )
    assert gpt52_binding.model_settings.get("thinking") is False

    # 非思考模型（gpt-4o）绝不注入 thinking 字段。
    gpt4o_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "copy.generate", model_id="gpt4o",
    )
    assert "thinking" not in gpt4o_binding.model_settings

    # 模型级 thinking_enabled 显式设置优先于 use-case 默认值。
    explicit_model_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, app_cfg, "category.product_match", model_id="gpt6_explicit_on",
    )
    assert explicit_model_binding.model_settings.get("thinking") is True

    # 功能绑定 generation.reasoning 显式设置优先于 use-case 默认值。
    override_cfg = {
        **app_cfg,
        "ai_use_case_bindings": {
            **app_cfg["ai_use_case_bindings"],
            "category.product_match": {
                "model_id": "gpt6",
                "generation": {"reasoning": {"mode": "enabled", "effort": "high"}},
            },
        },
    }
    override_binding = ai_model_factory.create_pydantic_model_binding_for_use_case(
        tmp_path, override_cfg, "category.product_match",
    )
    assert override_binding.model_settings.get("thinking") == "high"


def test_batch_copy_workers_and_category_match_budget():
    assert copy_generation._BATCH_COPY_MAX_WORKERS == 16
    assert cat_cap._CATEGORY_MATCH_BATCH_MAX_WORKERS == 6
    assert CATEGORY_MATCH_AGENT_PROFILE.max_model_requests == 6
    assert CATEGORY_MATCH_AGENT_PROFILE.max_tool_calls == 4


def test_category_match_batch_sliding_window_and_classifications(monkeypatch):
    started_order: list[str] = []
    finished_order: list[str] = []
    slow_release = threading.Event()

    def fake_match_category(req, *, product_store, matcher, category_record_loader):
        del product_store, matcher, category_record_loader
        started_order.append(req.draft_id)
        if req.draft_id == "d-slow":
            slow_release.wait(timeout=2.0)
            finished_order.append(req.draft_id)
            return CategoryMatchCapabilityResult(
                draft_id=req.draft_id,
                platform="yandex",
                site="global",
                category_id="100",
                category_path="Root / Slow",
                model_confidence=0.9,
                changed=True,
                updated_at="2026-11-28T02:35:10Z",
            )
        if req.draft_id == "d-unresolved":
            finished_order.append(req.draft_id)
            raise CapabilityInputRequired(
                "CATEGORY_MATCH_UNRESOLVED",
                "请人工选择类目",
                key="category_id",
                label="平台类目",
                reason="存在两个相近类目",
                options=[CapabilityInputOption(value="200", label="候选 A（200）")],
            )
        if req.draft_id == "d-forbidden":
            finished_order.append(req.draft_id)
            raise BusinessCapabilityError(
                "CATEGORY_FORBIDDEN_NO_ALTERNATIVE",
                "仅匹配到禁售类目",
                retryable=False,
            )
        finished_order.append(req.draft_id)
        if req.draft_id == "d-fast-2":
            # 滑动窗口：d-fast-2 不必等待 d-slow 就能跑完并释放 d-slow。
            slow_release.set()
        return CategoryMatchCapabilityResult(
            draft_id=req.draft_id,
            platform="yandex",
            site="global",
            category_id=f"cat-{req.draft_id}",
            category_path=f"Root / {req.draft_id}",
            model_confidence=0.95,
            changed=True,
            updated_at=f"2026-11-28T02:35:11Z-{req.draft_id}",
        )

    monkeypatch.setattr(cat_cap, "match_category", fake_match_category)
    result = cat_cap.match_categories_for_drafts(
        CategoryMatchBatchRequest(
            draft_ids=["d-slow", "d-fast-1", "d-unresolved", "d-forbidden", "d-fast-2"],
            target_platform="yandex",
            site="global",
        ),
        product_store=SimpleNamespace(),
        matcher=lambda *a, **kw: {},
        max_workers=4,
    )
    assert result.total == 5
    assert result.matched_count == 3
    assert result.unresolved_count == 1
    assert result.forbidden_count == 1
    assert result.failed_count == 0
    # 结果严格按输入顺序返回，且 d-fast-2 在 d-slow 之前已完成（证明非同步屏障）。
    assert [item.draft_id for item in result.items] == [
        "d-slow", "d-fast-1", "d-unresolved", "d-forbidden", "d-fast-2",
    ]
    assert finished_order.index("d-fast-2") < finished_order.index("d-slow")
    unresolved = result.items[2]
    assert unresolved.status == "unresolved" and unresolved.needs_confirmation is True
    assert unresolved.candidate_options[0].value == "200"
    assert result.items[3].status == "forbidden"
    assert result.items[0].updated_at == "2026-11-28T02:35:10Z"
    assert result.items[1].updated_at == "2026-11-28T02:35:11Z-d-fast-1"


def test_category_match_batch_circuit_breaks_repeated_retryable_failures(monkeypatch):
    calls = 0

    def failing_match(req, *, product_store, matcher, category_record_loader):
        nonlocal calls
        del req, product_store, matcher, category_record_loader
        calls += 1
        raise BusinessCapabilityError("UPSTREAM_TIMEOUT", "超时", retryable=True)

    monkeypatch.setattr(cat_cap, "match_category", failing_match)
    result = cat_cap.match_categories_for_drafts(
        CategoryMatchBatchRequest(
            draft_ids=["d1", "d2", "d3", "d4", "d5"],
            target_platform="yandex",
            site="global",
        ),
        product_store=SimpleNamespace(),
        matcher=lambda *a, **kw: {},
        max_workers=1,
    )
    assert calls == 3
    assert result.failed_count == 5
    assert [item.error_code for item in result.items] == [
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_TIMEOUT",
        "CATEGORY_SERVICE_UNAVAILABLE",
        "CATEGORY_SERVICE_UNAVAILABLE",
    ]


def test_product_publish_validate_batch_shares_session_and_isolates_errors(monkeypatch):
    seen_sessions: list[PublishValidationSession] = []

    def fake_evaluate(req, *, context=None, validation_session=None):
        del context
        assert validation_session is not None
        seen_sessions.append(validation_session)
        if req.draft_id == "d-err":
            raise BusinessCapabilityError("DRAFT_NOT_FOUND", "草稿不存在")
        passed = req.draft_id == "d-pass"
        res = ProductPublishValidationResult(
            passed=passed,
            summary=ProductPublishSummary(
                draft_id=req.draft_id,
                platform="yandex",
                site="global",
                store_identity="yandex:test",
            ),
            errors=[],
            warnings=[],
            validation_digest="digest-1" if passed else "",
        )
        return SimpleNamespace(result=res)

    monkeypatch.setattr(pub_cap, "evaluate_publish_validation", fake_evaluate)
    batch = pub_cap.validate_products_publish_batch(
        ProductPublishValidateBatchRequest(
            draft_ids=["d-pass", "d-fail", "d-err", "d-pass"],
            platform="yandex",
            site="global",
        ),
        context=SimpleNamespace(),
    )
    assert batch.total == 3
    assert batch.passed_count == 1
    assert batch.failed_count == 1
    assert batch.error_count == 1
    assert [item.draft_id for item in batch.items] == ["d-pass", "d-fail", "d-err"]
    assert batch.items[2].error_code == "DRAFT_NOT_FOUND"
    # 同一批内共享同一个 PublishValidationSession。
    assert len({id(s) for s in seen_sessions}) == 1


def test_draft_sku_variants_check_batch_passes_per_draft_yandex_fields(monkeypatch):
    seen_fields: dict[str, list[str]] = {}

    def fake_check(req, scope, execution):
        del scope, execution
        seen_fields[req.draft_id] = list(req.yandex_variant_field_ids)
        if req.draft_id == "d-err":
            raise BusinessCapabilityError("CATEGORY_REQUIRED", "缺少类目")
        return DraftSkuVariantsCheckResult(
            updated_at="2026-11-27T00:00:00Z",
            sku_count=3 if req.draft_id != "d-single" else 1,
            grouping_mode="combined",
            status="not_applicable" if req.draft_id == "d-single" else "distinct",
        )

    monkeypatch.setattr(prod_cap, "_check_draft_sku_variants", fake_check)
    result = prod_cap.draft_sku_variants_check_batch(
        DraftSkuVariantsCheckBatchRequest(
            draft_ids=["d-1", "d-single", "d-err"],
            platform="yandex",
            site="global",
            yandex_variant_field_ids_by_draft={"d-1": ["14871214"]},
        ),
        scope=SimpleNamespace(),
        execution=AiExecutionContext.create(timeout_seconds=30, budget_profile="global.chat"),
    )
    assert result.total == 3
    assert result.distinct_count == 1
    assert result.not_applicable_count == 1
    assert result.error_count == 1
    assert seen_fields == {"d-1": ["14871214"], "d-single": [], "d-err": []}


def test_publish_validation_session_uses_point_ancestor_lookup_and_for_sku_group_shares_caches(monkeypatch):
    ancestor_calls: list[str] = []
    provider = get_context().category_catalog.provider_for("yandex")
    monkeypatch.setattr(
        provider,
        "cached_category_ancestor_ids",
        lambda category_id, **kw: (ancestor_calls.append(str(category_id)) or ("90401", "100")),
    )
    monkeypatch.setattr(
        provider,
        "cached_category_ancestor_map",
        lambda **kw: pytest.fail("PublishValidationSession 不应再全量加载 Yandex 祖先树"),
    )
    root_session = PublishValidationSession()
    child_a = root_session.for_sku_group()
    child_b = root_session.for_sku_group()
    assert child_a.sku_inputs_checked is True
    assert child_a.images is not child_b.images
    assert child_a.yandex_ancestor_ids("200") == ("90401", "100")
    assert child_b.yandex_ancestor_ids("200") == ("90401", "100")
    assert ancestor_calls == ["200"]


def test_user_input_only_resume_resets_usage_limits_while_approval_keeps_accumulated_usage():
    from pydantic_ai import DeferredToolRequests
    from pydantic_ai.messages import ToolCallPart
    from erp_web.services.global_agent_chat_service import _is_user_input_only_deferred

    user_only = DeferredToolRequests(
        calls=[ToolCallPart(tool_name="request_user_input", args={"title": "t", "question": "q"}, tool_call_id="c1")],
    )
    approval_only = DeferredToolRequests(
        approvals=[ToolCallPart(tool_name="product_publish_request", args={"draft_id": "d1"}, tool_call_id="c2")],
    )
    mixed = DeferredToolRequests(
        calls=[ToolCallPart(tool_name="request_user_input", args={"title": "t", "question": "q"}, tool_call_id="c1")],
        approvals=[ToolCallPart(tool_name="product_publish_request", args={"draft_id": "d1"}, tool_call_id="c2")],
    )
    job_call = DeferredToolRequests(
        calls=[ToolCallPart(tool_name="online_products_sync", args={}, tool_call_id="c3")],
    )
    assert _is_user_input_only_deferred(user_only) is True
    assert _is_user_input_only_deferred(approval_only) is False
    assert _is_user_input_only_deferred(mixed) is False
    assert _is_user_input_only_deferred(job_call) is False


def test_python_tool_budget_returns_structured_error_without_monty_scheduler_stall(tmp_path, monkeypatch):
    import asyncio
    from dataclasses import replace
    from pydantic_ai.models.function import FunctionModel
    from erp_web.services import global_agent_chat_service
    from tests.test_ai_code_mode import binding, body, code_delta, service, tools

    monkeypatch.setattr(
        global_agent_chat_service,
        "GLOBAL_CHAT_PROFILE",
        replace(global_agent_chat_service.GLOBAL_CHAT_PROFILE, max_tool_calls=10),
    )
    code1 = """import asyncio
for i in range(6):
    await update_row(draft_id=f'pre-{i}')
'ok'
"""
    code2 = """import asyncio
W = {}; F = {}
async def wr(i):
    r = await update_row(draft_id=str(i))
    if r.get('ok') is False:
        F[i] = (r.get('error') or {}).get('code')
    else:
        W[i] = r
await asyncio.gather(*[wr(i) for i in range(8)])
{'W': sorted(W.keys()), 'F': F}
"""
    executed: list[str] = []
    retries: list[str] = []
    returned: list[object] = []
    steps = iter([code_delta(code1, "s1"), code_delta(code2, "s2")])

    async def model(messages, info):
        for msg in messages:
            for part in msg.parts:
                if getattr(part, "part_kind", "") == "retry-prompt":
                    retries.append(str(part.content))
                elif getattr(part, "part_kind", "") == "tool-return" and getattr(part, "tool_name", "") == "run_code":
                    returned.append(part.content)
        step = next(steps, None)
        if step is None:
            yield "已完成 2 份，其余触及额度。"
        else:
            yield {0: step}

    def slow_write(args, ctx):
        if not args["draft_id"].startswith("pre-"):
            time.sleep(0.08)
        executed.append(args["draft_id"])
        return {"ok": True, "id": args["draft_id"]}

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(binding("update_row", slow_write, write=True)),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))

    assert executed[:6] == ["pre-0", "pre-1", "pre-2", "pre-3", "pre-4", "pre-5"]
    assert sorted(executed[6:]) == ["0", "1"]
    assert not any("asyncio scheduler stalled" in msg for msg in retries)
    assert any(
        isinstance(item, dict)
        and item.get("W") == [0, 1]
        and all(code == "TOOL_BUDGET_EXHAUSTED" for code in (item.get("F") or {}).values())
        and len(item.get("F") or {}) == 6
        for item in returned
    )


def test_draft_attributes_read_batch_returns_product_weight_and_isolates_errors(monkeypatch):
    from erp_web.schemas.product_capabilities import (
        DraftAttributesReadBatchRequest,
        DraftAttributesReadResult,
        DraftAttributeTarget,
        ProductFacts,
    )

    def fake_read(req, scope):
        del scope
        if req.draft_id == "d-missing":
            raise BusinessCapabilityError("DRAFT_NOT_FOUND", "草稿不存在")
        return DraftAttributesReadResult(
            draft_id=req.draft_id,
            updated_at="2026-11-27T00:00:00Z",
            product=ProductFacts(
                product_id=f"p-{req.draft_id}",
                name="测试商品",
                weight_kg="8000",
                updated_at="2026-11-27T00:00:00Z",
            ),
            targets=[DraftAttributeTarget(platform="yandex", site="global", category_id="100", attributes={})],
            skus=[],
            sku_count=3,
        )

    monkeypatch.setattr(prod_cap, "_read_draft_attributes", fake_read)
    batch = prod_cap.draft_attributes_read_batch(
        DraftAttributesReadBatchRequest(
            draft_ids=["d-1", "d-missing", "d-1"],
            platform="yandex",
            site="global",
            scope="sku",
        ),
        scope=SimpleNamespace(),
    )
    assert batch.total == 2
    assert batch.ok_count == 1
    assert batch.error_count == 1
    assert batch.items[0].ok is True and batch.items[0].result.product.weight_kg == "8000"
    assert batch.items[1].ok is False and batch.items[1].error_code == "DRAFT_NOT_FOUND"


def test_product_publish_validate_batch_omits_sku_items_by_default_and_supports_opt_in(monkeypatch):
    from erp_web.schemas.publish_capabilities import ProductPublishSkuSummary

    def fake_evaluate(req, *, context=None, validation_session=None):
        del context, validation_session
        res = ProductPublishValidationResult(
            passed=False,
            summary=ProductPublishSummary(
                draft_id=req.draft_id,
                platform="yandex",
                site="global",
                store_identity="yandex:test",
                sku_items=(
                    ProductPublishSkuSummary(sku_id="s1", sku="SKU-1"),
                    ProductPublishSkuSummary(sku_id="s2", sku="SKU-2"),
                ),
            ),
            errors=[],
            warnings=[],
        )
        return SimpleNamespace(result=res)

    monkeypatch.setattr(pub_cap, "evaluate_publish_validation", fake_evaluate)
    compact = pub_cap.validate_products_publish_batch(
        ProductPublishValidateBatchRequest(draft_ids=["d-1"], platform="yandex", site="global"),
        context=SimpleNamespace(),
    )
    assert compact.items[0].sku_count == 2
    assert compact.items[0].validation.summary.sku_items == ()

    detailed = pub_cap.validate_products_publish_batch(
        ProductPublishValidateBatchRequest(
            draft_ids=["d-1"],
            platform="yandex",
            site="global",
            include_sku_summaries=True,
        ),
        context=SimpleNamespace(),
    )
    assert detailed.items[0].sku_count == 2
    assert len(detailed.items[0].validation.summary.sku_items) == 2


def test_draft_pricing_batch_shares_session_picks_independent_warehouses_and_isolates_errors(monkeypatch):
    """多草稿批量核价共享单一 PricingSession 合批算价、各草稿独立选仓，并隔离单项缺资料错误。"""
    from copy import deepcopy
    from erp_web.product_model import default_product_model
    from erp_web.runtime_units import draft_pricing as dp
    from erp_web.runtime_units.draft_pricing_capabilities import (
        DraftPricingCapabilityScope,
        draft_pricing_apply_batch,
        draft_pricing_preview_batch,
    )
    from erp_web.runtime_units.pricing_runtime import PricingSession
    from erp_web.schemas.draft_pricing import (
        DraftPricingBatchApplyItem,
        DraftPricingBatchApplyRequest,
        DraftPricingBatchPreviewRequest,
    )
    from erp_web.services.pricing_shipping import PricingShipping
    from tests.pricing_test_utils import official_commission
    from tests.runtime_test_utils import seed_store_currency
    from tests.yandex_logistics_support import PLAN, install_logistics

    plan_31 = {**deepcopy(PLAN), "publish_warehouse_id": 31, "route_id": "route-31", "partner_name": "仓31"}
    plan_32 = {**deepcopy(PLAN), "publish_warehouse_id": 32, "route_id": "route-32", "partner_name": "仓32"}
    install_logistics(monkeypatch, plans=[plan_31, plan_32])
    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.PricingCommissions", lambda _cfg, **_kw: official_commission)
    seed_store_currency("yandex", "RUB", mode="manual")

    class MultiWarehouseShipping(PricingShipping):
        def __call__(self, common, target):
            if self.yandex_plans is None:
                self.yandex_plans = [deepcopy(plan_31), deepcopy(plan_32)]
            weight_kg = float(common["weight_kg"])
            # 轻货（<=0.5kg）在仓 31 更便宜；重货（>0.5kg）在仓 32 更便宜。
            candidates = []
            for plan in self.yandex_plans:
                wh = plan["publish_warehouse_id"]
                amount = "4" if (wh == 31 and weight_kg <= 0.5) or (wh == 32 and weight_kg > 0.5) else "12"
                candidates.append({"amount_cny": amount, "fulfillment_plan": deepcopy(plan)})
            best = min(candidates, key=lambda item: (float(item["amount_cny"]), item["fulfillment_plan"]["publish_warehouse_id"]))
            return {
                "amount": best["amount_cny"],
                "currency": "CNY",
                "minimum_price_cny": "0",
                "billable_g": str(weight_kg * 1000),
                "evidence": {"fulfillment_plan": deepcopy(best["fulfillment_plan"])},
                "candidates": candidates,
            }

    monkeypatch.setattr("erp_web.runtime_units.pricing_runtime.PricingShipping", MultiWarehouseShipping)

    created_sessions = 0
    calculate_many_batch_sizes: list[int] = []

    class TrackingPricingSession(PricingSession):
        def __init__(self) -> None:
            nonlocal created_sessions
            created_sessions += 1
            super().__init__()

        def calculate_many(self, inputs):
            calculate_many_batch_sizes.append(len(inputs))
            return super().calculate_many(inputs)

    monkeypatch.setattr(dp, "PricingSession", TrackingPricingSession)

    app = get_context()

    def _seed_yandex_draft(product_id: str, draft_id: str, skus: list[dict]) -> None:
        product = default_product_model()
        product.update(product_id=product_id, name=f"商品 {draft_id}", sku_items=skus)
        product["drafts"] = {
            "yandex": {
                "draft_id": draft_id,
                "title": f"草稿 {draft_id}",
                "description": "描述",
                "target_sites": [{"platform": "yandex", "site": "global", "language": "ru-RU"}],
                "sku_items": [
                    {"sku_id": sku["id"], "sku": f"SELL-{draft_id}-{idx}", "selected": True, "stock": "5"}
                    for idx, sku in enumerate(skus)
                ],
                "pricing": {
                    "common": {
                        "domestic_freight_cny": 1,
                        "packaging_cost_cny": 2,
                        "other_cost_cny": 0,
                        "exchange_rate_mode": "manual",
                        "usd_cny_rate": 7,
                        "mxn_usd_rate": 20,
                        "rub_cny_rate": 12,
                    },
                    "targets": {
                        "yandex:global": {"shipping_quote_mode": "auto", "target_margin_percent": 30},
                    },
                },
            }
        }
        app.products.save_product(product)

    _seed_yandex_draft("prod-batch-a", "draft-batch-a", [
        {"id": "a-1", "name": "轻款1", "active": True, "cost_cny": "10", "package_dimensions": {"length_cm": "15", "width_cm": "10", "height_cm": "8", "weight_kg": "0.3"}},
        {"id": "a-2", "name": "轻款2", "active": True, "cost_cny": "12", "package_dimensions": {"length_cm": "15", "width_cm": "10", "height_cm": "8", "weight_kg": "0.4"}},
    ])
    _seed_yandex_draft("prod-batch-b", "draft-batch-b", [
        {"id": "b-1", "name": "重款1", "active": True, "cost_cny": "20", "package_dimensions": {"length_cm": "25", "width_cm": "20", "height_cm": "15", "weight_kg": "1.2"}},
        {"id": "b-2", "name": "重款2", "active": True, "cost_cny": "25", "package_dimensions": {"length_cm": "25", "width_cm": "20", "height_cm": "15", "weight_kg": "1.5"}},
    ])
    _seed_yandex_draft("prod-batch-c", "draft-batch-c", [
        {"id": "c-1", "name": "缺宽", "active": True, "cost_cny": "15", "package_dimensions": {"length_cm": "15", "width_cm": "", "height_cm": "8", "weight_kg": "0.3"}},
    ])

    scope = DraftPricingCapabilityScope(app.products)
    preview = draft_pricing_preview_batch(
        DraftPricingBatchPreviewRequest(draft_ids=["draft-batch-a", "draft-batch-b", "draft-batch-c", "draft-batch-a"]),
        scope,
    )
    assert preview.total == 3
    assert preview.succeeded_count == 2
    assert preview.failed_count == 1
    assert preview.applied is False
    # 全批只创建 1 个 PricingSession，首次 calculate_many 一次性合并 2 份有效草稿的 4 个 SKU。
    assert created_sessions == 1
    assert calculate_many_batch_sizes == [4]

    item_a, item_b, item_c = preview.items
    assert item_a.ok and item_a.preview_id and item_a.sku_count == 2
    assert item_b.ok and item_b.preview_id and item_b.sku_count == 2
    assert not item_c.ok and item_c.error_code == "PRICING_INPUT_INVALID" and "包装边长" in item_c.error

    # 验证 Draft A 选仓 31、Draft B 独立选仓 32（unify_yandex_warehouse 不污染共享 session）。
    cached_a = app.pricing_cache.previews.get(item_a.preview_id)
    cached_b = app.pricing_cache.previews.get(item_b.preview_id)
    assert cached_a is not None and cached_b is not None
    wh_a = cached_a["batch"]["items"][0]["result"]["results"][0]["calculation_basis"]["shipping_evidence"]["fulfillment_plan"]["publish_warehouse_id"]
    wh_b = cached_b["batch"]["items"][0]["result"]["results"][0]["calculation_basis"]["shipping_evidence"]["fulfillment_plan"]["publish_warehouse_id"]
    assert wh_a == 31
    assert wh_b == 32

    # 批量应用：包含 2 份有效预览与 1 份缺包装资料草稿，有效草稿各自原子保存且返回新 updated_at。
    exec_ctx = AiExecutionContext.create(timeout_seconds=30, budget_profile="test")
    applied = draft_pricing_apply_batch(
        DraftPricingBatchApplyRequest(
            items=[
                DraftPricingBatchApplyItem(draft_id="draft-batch-a", preview_id=item_a.preview_id, expected_updated_at=item_a.updated_at),
                DraftPricingBatchApplyItem(draft_id="draft-batch-b", preview_id=item_b.preview_id, expected_updated_at=item_b.updated_at),
                DraftPricingBatchApplyItem(draft_id="draft-batch-c", preview_id="missing-preview"),
            ]
        ),
        scope,
        exec_ctx,
    )
    assert applied.total == 3
    assert applied.succeeded_count == 2
    assert applied.failed_count == 1
    assert applied.applied is False  # 部分失败不得标记整批 applied=True
    assert applied.items[0].ok and applied.items[0].applied and applied.items[0].updated_at
    assert applied.items[1].ok and applied.items[1].applied and applied.items[1].updated_at
    assert not applied.items[2].ok and applied.items[2].error_code == "PRICING_INPUT_INVALID"

    # 已消耗的 preview_id 再次应用返回 PRICING_PREVIEW_REQUIRED。
    reused = draft_pricing_apply_batch(
        DraftPricingBatchApplyRequest(
            items=[DraftPricingBatchApplyItem(draft_id="draft-batch-a", preview_id=item_a.preview_id)]
        ),
        scope,
        exec_ctx,
    )
    assert reused.failed_count == 1 and reused.items[0].error_code == "PRICING_PREVIEW_REQUIRED"

    saved_a = app.db.load_draft_model("draft-batch-a")
    saved_b = app.db.load_draft_model("draft-batch-b")
    assert all(row["pricing"]["applied"] for row in saved_a["sku_items"])
    assert all(row["pricing"]["applied"] for row in saved_b["sku_items"])
    assert saved_a["sku_items"][0]["pricing"]["targets"]["yandex:global"]["calculation_basis"]["shipping_evidence"]["fulfillment_plan"]["publish_warehouse_id"] == 31
    assert saved_b["sku_items"][0]["pricing"]["targets"]["yandex:global"]["calculation_basis"]["shipping_evidence"]["fulfillment_plan"]["publish_warehouse_id"] == 32

    # 重复 draft_id 在应用前直接报错拒绝。
    with pytest.raises(BusinessCapabilityError) as dup_exc:
        draft_pricing_apply_batch(
            DraftPricingBatchApplyRequest(
                items=[
                    DraftPricingBatchApplyItem(draft_id="draft-batch-a", preview_id="p1"),
                    DraftPricingBatchApplyItem(draft_id="draft-batch-a", preview_id="p2"),
                ]
            ),
            scope,
            exec_ctx,
        )
    assert dup_exc.value.code == "PRICING_BATCH_DUPLICATE_DRAFT"


def test_category_attributes_query_batch_paginates_compacts_and_isolates_errors():
    """批量类目属性查询自动翻页、合并 common+sku 可写字段、省略冗长说明并隔离单项失败。"""
    from erp_web.runtime_units.category_query_capabilities import (
        CategoryQueryCapabilityScope,
        category_attributes_query,
        category_attributes_query_batch,
    )
    from erp_web.schemas.category_query_capabilities import (
        CategoryAttributesQueryBatchRequest,
        CategoryAttributesQueryRequest,
    )

    pages = {
        ("cat-ok", ""): {
            "platform": "yandex",
            "site": "global",
            "category_id": "cat-ok",
            "category_path": "Все товары / Посуда / Миски",
            "attributes": [
                {
                    "id": "100",
                    "name": "Материал",
                    "name_zh": "材质",
                    "description": "很长的俄语说明" * 50,
                    "description_zh": "很长的中文说明" * 50,
                    "required": True,
                    "value_type": "enum",
                    "value_mode": "strict_enum",
                    "allow_custom_values": False,
                    "read_only": False,
                    "is_dictionary": True,
                    "unit_options": [{"id": "u1", "name": "см"}],
                    "options": [
                        {"value": f"opt-{i}", "dictionary_value_id": str(i), "label_zh": f"选项{i}"}
                        for i in range(25)
                    ],
                    "has_more_values": False,
                },
                {
                    "id": "999",
                    "name": "Только чтение",
                    "read_only": True,
                },
            ],
            "next_cursor": "page2",
            "has_more": True,
        },
        ("cat-ok", "page2"): {
            "platform": "yandex",
            "site": "global",
            "category_id": "cat-ok",
            "category_path": "Все товары / Посуда / Миски",
            "attributes": [
                {
                    "id": "14871214",
                    "name": "Цвет товара для карточки",
                    "name_zh": "商品颜色",
                    "variation_role": "variant",
                    "value_type": "string",
                    "value_mode": "free_text",
                    "allow_custom_values": True,
                },
            ],
            "next_cursor": "",
            "has_more": False,
        },
    }

    def fake_loader(platform, category_id, *, site="", cursor="", limit=50, timeout_seconds=None):
        del platform, site, limit
        assert timeout_seconds is not None and timeout_seconds > 0
        if category_id == "cat-err":
            raise RuntimeError("upstream timeout")
        return pages[(category_id, cursor)]

    scope = CategoryQueryCapabilityScope(
        searcher=lambda *a, **kw: [],
        attributes_loader=fake_loader,
        attribute_values_loader=lambda *a, **kw: {},
        record_loader=lambda *a, **kw: {},
        draft_context_loader=lambda body: ({}, None, 200),
        product_loader=lambda body: ({}, None, 200),
    )
    exec_ctx = AiExecutionContext.create(timeout_seconds=30, budget_profile="test")

    batch = category_attributes_query_batch(
        CategoryAttributesQueryBatchRequest(
            platform="yandex",
            site="global",
            category_ids=["cat-ok", "cat-err", "cat-ok"],
            max_options_per_attribute=5,
        ),
        scope,
        exec_ctx,
    )
    assert batch.total == 2
    assert batch.ok_count == 1
    assert batch.error_count == 1

    ok_item, err_item = batch.items
    assert ok_item.ok and ok_item.category_id == "cat-ok" and ok_item.attribute_count == 2
    assert ok_item.excluded_attributes == ()
    by_id = {attr["id"]: attr for attr in ok_item.attributes}
    assert set(by_id) == {"100", "14871214"}
    assert by_id["100"]["write_scope"] == "common"
    assert "description" not in by_id["100"] and "description_zh" not in by_id["100"]
    assert by_id["100"]["unit_options"] == ["см"]
    assert len(by_id["100"]["options"]) == 5
    assert by_id["14871214"]["write_scope"] == "sku" and by_id["14871214"]["variation_role"] == "variant"
    assert not err_item.ok and err_item.error_code == "CATEGORY_LIVE_API_FAILED"

    single_compact = category_attributes_query(
        CategoryAttributesQueryRequest(
            platform="yandex",
            site="global",
            category_id="cat-ok",
            scope="writable",
            compact=True,
        ),
        scope,
        exec_ctx,
    )
    assert [attr["id"] for attr in single_compact.attributes] == ["100"]
    assert single_compact.excluded_attributes == ()
    assert "description" not in single_compact.attributes[0]
