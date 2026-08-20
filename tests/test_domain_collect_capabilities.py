from __future__ import annotations

"""Workstream D 第三批：采集、认领与商品研究 Capability 行为测试。

采集/研究的网络与浏览器边界以可信替代注入；凭据解析与认领持久化走
隔离 AppContext 的真实配置与存储。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.runtime_units.collect_capabilities import (
    CollectCapabilityScope,
    claim_products,
    collect_1688,
    collect_1688_clean,
    collect_batch,
    collect_from_browser_tab,
    source_collect,
)
from erp_web.runtime_units.collect_helpers import claim_products_to_platforms
from erp_web.runtime_units import source_collect_workflows
from erp_web.runtime_units.research_capabilities import (
    ResearchCapabilityScope,
    research_hot_products_search,
    research_run_status_query,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.collect_capabilities import (
    ClaimProductsRequest,
    Collect1688CleanRequest,
    Collect1688Request,
    CollectBatchRequest,
    CollectFromBrowserTabRequest,
    ResearchHotProductsSearchRequest,
    ResearchRunStatusQueryRequest,
    SourceCollectRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError


def _execution(operation_key: str = "op-1") -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={"task_id": "task-1", "step_id": "step-1"},
        idempotency_context={"operation_key": operation_key},
    )


def _collect_scope(**overrides: Any) -> CollectCapabilityScope:
    defaults: dict[str, Any] = dict(
        source_collector=lambda url, mode, platform, claim_platforms: {
            "ok": True,
            "product": {"product_id": "product-collected", "name": "Fan"},
            "diagnostics": {"collect_mode": mode, "source_url": url},
            "next_action": "",
            "message": "采集成功",
            "productsIndex": [{"product_id": "product-collected"}],
        },
        batch_collector=lambda urls, mode, platform, claim_platforms: {
            "ok": True,
            "total": len(urls),
            "success_count": len(urls),
            "partial_count": 0,
            "failed_count": 0,
            "items": [{"url": item, "status": "success"} for item in urls],
            "productsIndex": [],
        },
        browser_tab_collector=(
            lambda tab_url,
            platform_hint,
            product_url,
            claim_platforms,
            save_only: {
                "ok": True,
                "product": {"product_id": "product-tab"},
                "imagePool": [{"id": "tab-image"}],
                "diagnostics": {"collect_mode": "browser_debugging"},
                "browserStatus": {"connected": True},
                "next_action": "",
                "productsIndex": [],
            }
        ),
        online_1688_collector=lambda body: {
            "ok": True,
            "product": {"product_id": "product-1688"},
            "source_price_cny": "19.9",
            "source_material": "ABS",
            "productsIndex": [],
            "echo": dict(body),
        },
        text_cleaner=lambda text, url: {
            "ok": True,
            "source_price_cny": "19.9",
            "clean_source_text": text,
        },
        claimer=lambda product_ids, platforms: {
            "ok": True,
            "claimed_count": len(product_ids),
            "items": [{"product_id": item, "ok": True} for item in product_ids],
            "productsIndex": [],
            "draftsIndex": [],
        },
    )
    defaults.update(overrides)
    return CollectCapabilityScope(**defaults)


def _research_scope(**overrides: Any) -> ResearchCapabilityScope:
    defaults: dict[str, Any] = dict(
        run_creator=lambda body: {
            "ok": True,
            "run": {"run_id": "prr_test", "status": "queued", "request": body},
            "items": [],
            "source_status": [],
            "description": "已创建运行任务，等待后台执行。",
        },
        run_loader=lambda run_id: None,
        active_run_loader=lambda: None,
    )
    defaults.update(overrides)
    return ResearchCapabilityScope(**defaults)


# ---------------------------------------------------------------- 采集


def test_source_collect_success_and_error_mapping() -> None:
    scope = _collect_scope()
    result = source_collect(
        SourceCollectRequest(url="https://detail.1688.com/offer/1.html"),
        scope=scope,
        execution=_execution(),
    )
    assert result.ok is True
    assert result.product_id == "product-collected"
    assert dict(result.diagnostics)["collect_mode"] == "browser"
    assert len(result.products_index) == 1

    failing_scope = _collect_scope(
        source_collector=lambda url, mode, platform, claim_platforms: {
            "ok": False,
            "error": "采集失败：需要登录",
            "diagnostics": {"error_code": "LOGIN_REQUIRED"},
        }
    )
    with pytest.raises(BusinessCapabilityError) as error:
        source_collect(
            SourceCollectRequest(url="https://detail.1688.com/offer/1.html"),
            scope=failing_scope,
            execution=_execution(),
        )
    assert error.value.code == "LOGIN_REQUIRED"

    broken_scope = _collect_scope(
        source_collector=lambda *args: (_ for _ in ()).throw(
            RuntimeError("NO_SNAPSHOT")
        )
    )
    with pytest.raises(BusinessCapabilityError) as broken:
        source_collect(
            SourceCollectRequest(url="https://detail.1688.com/offer/1.html"),
            scope=broken_scope,
            execution=_execution(),
        )
    assert broken.value.code == "SOURCE_COLLECT_FAILED"


def test_collect_batch_counts_and_failure() -> None:
    scope = _collect_scope()
    result = collect_batch(
        CollectBatchRequest(urls=("https://a.example.com", "https://b.example.com")),
        scope=scope,
        execution=_execution(),
    )
    assert result.total == 2
    assert result.success_count == 2
    assert len(result.items) == 2

    def mixed(urls: Any, mode: str, platform: str, claim_platforms: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "total": 2,
            "success_count": 1,
            "partial_count": 0,
            "failed_count": 1,
            "items": [
                {"url": "a", "status": "success"},
                {"url": "b", "status": "failed", "error": "验证码"},
            ],
            "productsIndex": [],
        }

    mixed_scope = _collect_scope(batch_collector=mixed)
    mixed_result = collect_batch(
        CollectBatchRequest(urls=("a", "b")),
        scope=mixed_scope,
        execution=_execution(),
    )
    assert mixed_result.success_count == 1
    assert mixed_result.failed_count == 1

    broken_scope = _collect_scope(
        batch_collector=lambda *args: (_ for _ in ()).throw(RuntimeError("down"))
    )
    with pytest.raises(BusinessCapabilityError) as broken:
        collect_batch(
            CollectBatchRequest(urls=("a",)),
            scope=broken_scope,
            execution=_execution(),
        )
    assert broken.value.code == "COLLECT_BATCH_FAILED"


def test_collect_from_browser_tab_success_and_not_connected() -> None:
    scope = _collect_scope()
    result = collect_from_browser_tab(
        CollectFromBrowserTabRequest(
            product_url="https://detail.1688.com/offer/9.html"
        ),
        scope=scope,
        execution=_execution(),
    )
    assert result.ok is True
    assert dict(result.product)["product_id"] == "product-tab"
    assert dict(result.browser_status)["connected"] is True

    disconnected_scope = _collect_scope(
        browser_tab_collector=lambda *args: {
            "ok": False,
            "diagnostics": {"error_code": "REMOTE_DEBUGGING_NOT_CONNECTED"},
            "error": "未连接 Chrome remote debugging",
            "next_action": "请启动专用 Chrome 后重试。",
        }
    )
    with pytest.raises(BusinessCapabilityError) as error:
        collect_from_browser_tab(
            CollectFromBrowserTabRequest(),
            scope=disconnected_scope,
            execution=_execution(),
        )
    assert error.value.code == "REMOTE_DEBUGGING_NOT_CONNECTED"


def test_disconnected_browser_probe_does_not_mutate_recent_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    before = context.products.save_product(
        {
            "product_id": "product-before-browser-probe",
            "name": "Keep me unchanged",
            "source": {"title": "Keep me unchanged"},
        }
    )
    monkeypatch.setattr(
        source_collect_workflows,
        "browser_debug_status",
        lambda _port: {
            "connected": False,
            "error_code": "REMOTE_DEBUGGING_NOT_CONNECTED",
            "error_message": "未连接 Chrome remote debugging",
            "next_action": "请启动专用 Chrome 后重试。",
        },
    )

    result = source_collect_workflows.collect_from_browser_tab()
    after = context.products.load_product_from_index(
        "product-before-browser-probe",
        "",
    )

    assert result["ok"] is False
    assert result["diagnostics"]["error_code"] == "REMOTE_DEBUGGING_NOT_CONNECTED"
    assert after == before


def test_collect_1688_body_contract_and_cleaned_mapping() -> None:
    captured: dict[str, Any] = {}

    def collector(body: dict[str, Any]) -> dict[str, Any]:
        captured["body"] = dict(body)
        return {
            "ok": True,
            "product": {"product_id": "product-1688"},
            "source_price_cny": "19.9",
            "source_material": "ABS",
            "unrelated_key": "hidden",
            "productsIndex": [],
        }

    scope = _collect_scope(online_1688_collector=collector)
    result = collect_1688(
        Collect1688Request(
            url="https://detail.1688.com/offer/9.html",
            claim_platforms=("mercadolibre",),
        ),
        scope=scope,
        execution=_execution(),
    )
    body = captured["body"]
    assert body["url"] == "https://detail.1688.com/offer/9.html"
    assert body["save"] is True
    assert body["platforms"] == ["mercadolibre"]
    # Capability 契约不接受模型提供的 cookie；凭据由 Scope 在边界内解析。
    assert "cookie" not in body
    assert result.product.get("product_id") == "product-1688"
    assert result.cleaned["source_price_cny"] == "19.9"
    assert "unrelated_key" not in result.cleaned


def test_collect_1688_clean_passthrough_and_failure() -> None:
    scope = _collect_scope()
    result = collect_1688_clean(
        Collect1688CleanRequest(text="价格：￥19.9 材质：ABS"),
        scope=scope,
    )
    assert result.ok is True
    assert dict(result.cleaned)["source_price_cny"] == "19.9"

    failing_scope = _collect_scope(
        text_cleaner=lambda text, url: {"ok": False, "error": "无法识别价格"}
    )
    with pytest.raises(BusinessCapabilityError) as error:
        collect_1688_clean(
            Collect1688CleanRequest(text="无效文本"),
            scope=failing_scope,
        )
    assert error.value.code == "COLLECT_1688_CLEAN_FAILED"


def test_claim_products_with_real_store_and_failure_mapping() -> None:
    context = get_context()
    context.products.save_product(
        {
            "product_id": "product-claim-1",
            "name": "Fan",
            "sku": "claim-sku",
            "source": {
                "title": "Fan",
                "source_platform": "1688",
                "source_url": "https://example.com/claim",
            },
        }
    )
    scope = _collect_scope(
        claimer=lambda product_ids, platforms: claim_products_to_platforms(
            product_ids,
            platforms,
            context=context,
        )
    )
    result = claim_products(
        ClaimProductsRequest(
            product_ids=("product-claim-1",),
            platforms=("mercadolibre",),
        ),
        scope=scope,
        execution=_execution(),
    )
    assert result.claimed_count == 1
    item = dict(result.items[0])
    assert item["ok"] is True
    assert len(item.get("draft_ids") or []) == 1
    assert len(result.drafts_index) >= 1

    failing_scope = _collect_scope(
        claimer=lambda product_ids, platforms: {
            "ok": False,
            "error": "没有可用的草稿目标",
        }
    )
    with pytest.raises(BusinessCapabilityError) as error:
        claim_products(
            ClaimProductsRequest(product_ids=("product-claim-1",)),
            scope=failing_scope,
            execution=_execution(),
        )
    assert error.value.code == "CLAIM_PRODUCTS_FAILED"


def test_collect_credentials_resolve_from_saved_config_only() -> None:
    context = get_context()
    assert global_task_facade._resolved_collect_cookie(context) == ""
    assert global_task_facade._saved_1688_api_config(context) is None

    config = context.config.load_app_config()
    config["alibaba_cookie"] = "saved-cookie"
    config["1688_api"] = {
        "app_key": "key-1",
        "app_secret": "secret-1",
        "access_token": "token-1",
    }
    context.config.save_app_config(config)

    assert global_task_facade._resolved_collect_cookie(context) == "saved-cookie"
    api = global_task_facade._saved_1688_api_config(context)
    assert api is not None
    assert api["app_key"] == "key-1"


# ---------------------------------------------------------------- 研究


def test_research_hot_products_search_builds_typed_body() -> None:
    captured: dict[str, Any] = {}

    def creator(body: dict[str, Any]) -> dict[str, Any]:
        captured["body"] = dict(body)
        return {
            "ok": True,
            "run": {"run_id": "prr_1", "status": "queued"},
            "items": [],
            "source_status": [],
            "description": "已创建运行任务，等待后台执行。",
        }

    scope = _research_scope(run_creator=creator)
    result = research_hot_products_search(
        ResearchHotProductsSearchRequest(
            target_markets=("MLM",),
            limit=5,
        ),
        scope=scope,
        execution=_execution(),
    )
    assert captured["body"] == {
        "markets": {"target_markets": ["MLM"]},
        "result_options": {"limit": 5},
    }
    # 统一 persistent_job 契约：领域无关 Job 引用（job_id + job_type）。
    assert result.job_id == "prr_1"
    assert result.job_type == "product_research"
    assert result.status == "queued"
    assert result.summary

    empty_scope = _research_scope()
    empty_result = research_hot_products_search(
        ResearchHotProductsSearchRequest(),
        scope=empty_scope,
        execution=_execution(),
    )
    assert empty_result.job_id == "prr_test"
    assert empty_result.job_type == "product_research"

    def invalid_creator(body: dict[str, Any]) -> Any:
        raise ValueError("markets.target_markets is required")

    invalid_scope = _research_scope(run_creator=invalid_creator)
    with pytest.raises(BusinessCapabilityError) as invalid:
        research_hot_products_search(
            ResearchHotProductsSearchRequest(),
            scope=invalid_scope,
            execution=_execution(),
        )
    assert invalid.value.code == "RESEARCH_REQUEST_INVALID"

    failing_scope = _research_scope(
        run_creator=lambda body: {"ok": False, "error": "检索源不可用"}
    )
    with pytest.raises(BusinessCapabilityError) as failed:
        research_hot_products_search(
            ResearchHotProductsSearchRequest(),
            scope=failing_scope,
            execution=_execution(),
        )
    assert failed.value.code == "RESEARCH_START_FAILED"

    # 创建成功但缺少 run_id → 稳定错误，不能返回空 Job 引用。
    missing_id_scope = _research_scope(
        run_creator=lambda body: {"ok": True, "run": {"status": "queued"}}
    )
    with pytest.raises(BusinessCapabilityError) as missing_id:
        research_hot_products_search(
            ResearchHotProductsSearchRequest(),
            scope=missing_id_scope,
            execution=_execution(),
        )
    assert missing_id.value.code == "RESEARCH_RUN_ID_MISSING"


def test_research_run_status_query_by_id_and_active() -> None:
    run = {
        "run_id": "prr_9",
        "status": "completed",
        "description": "完成",
        "items": [{"title": "item"}],
        "source_status": [{"source": "hot", "ok": True}],
    }
    scope = _research_scope(
        run_loader=lambda run_id: dict(run) if run_id == "prr_9" else None,
        active_run_loader=lambda: dict(run),
    )

    by_id = research_run_status_query(
        ResearchRunStatusQueryRequest(run_id="prr_9"), scope=scope
    )
    assert by_id.ok is True
    assert by_id.active is False
    assert dict(by_id.run)["status"] == "completed"
    assert len(by_id.items) == 1

    with pytest.raises(BusinessCapabilityError) as missing:
        research_run_status_query(
            ResearchRunStatusQueryRequest(run_id="prr_missing"), scope=scope
        )
    assert missing.value.code == "RESEARCH_RUN_NOT_FOUND"

    active = research_run_status_query(
        ResearchRunStatusQueryRequest(), scope=scope
    )
    assert active.active is True
    assert dict(active.run)["run_id"] == "prr_9"

    idle_scope = _research_scope()
    idle = research_run_status_query(
        ResearchRunStatusQueryRequest(), scope=idle_scope
    )
    assert idle.ok is True
    assert idle.active is False
    assert idle.run == {}
