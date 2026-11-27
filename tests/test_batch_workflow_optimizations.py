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
