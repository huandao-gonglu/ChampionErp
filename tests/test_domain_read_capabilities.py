from __future__ import annotations

"""Workstream D 第一批：只读/查询/预览/纯计算 Capability 行为与等价性测试。"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.logistics_capabilities import (
    LOGISTICS_SHIPMENT_CREATE_TOOL,
    LogisticsCapabilityScope,
    _logistics_shipment_approval_snapshot,
    logistics_shipment_create,
    logistics_shipment_preview,
)
from erp_web.runtime_units.platform_query_capabilities import (
    PlatformQueryCapabilityScope,
    platform_orders_query,
    platform_published_items_query,
    products_index_query,
    publish_job_status_query,
    publish_jobs_query,
    publish_logs_query,
)
from erp_web.runtime_units.category_query_capabilities import (
    CategoryQueryCapabilityScope,
    category_attribute_values_query,
    category_attributes_query,
    category_precheck,
    category_search,
)
from erp_web.runtime_units.pricing_upc_capabilities import (
    PricingUpcCapabilityScope,
    pricing_calculate,
    upc_assign,
    upc_import,
)
from erp_web.runtime_units.store_auth_capabilities import (
    StoreAuthCapabilityScope,
    store_auth_check,
    store_auth_checklist,
)
from erp_web.schemas.ai_tools import AiToolExecutionError, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_query_capabilities import (
    CategoryAttributeValuesQueryRequest,
    CategoryAttributesQueryRequest,
    CategoryPrecheckRequest,
    CategorySearchRequest,
)
from erp_web.schemas.logistics_capabilities import (
    LogisticsShipmentCreateRequest,
    LogisticsShipmentPreviewRequest,
)
from erp_web.schemas.platform_query_capabilities import (
    PlatformOrdersQueryRequest,
    PlatformPublishedItemsQueryRequest,
    ProductsIndexQueryRequest,
    PublishJobStatusQueryRequest,
    PublishJobsQueryRequest,
    PublishLogsQueryRequest,
)
from erp_web.schemas.pricing_upc_capabilities import (
    PricingCalculateRequest,
    UpcAssignRequest,
    UpcImportRequest,
)
from erp_web.schemas.store_auth_capabilities import (
    StoreAuthCheckRequest,
    StoreAuthChecklistRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.task_approval import approval_binding_digest


def _execution(
    operation_key: str = "op-1", *, deadline_seconds: float = 300
) -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
        budget_profile="test",
        business_scope={"task_id": "task-1", "step_id": "step-1"},
        idempotency_context={"operation_key": operation_key},
    )


def _approved_execution(
    snapshot: TaskApprovalSnapshot,
    capability_name: str,
    *,
    operation_key: str = "op-1",
    step_id: str = "step-1",
    task_revision: int = 1,
    deadline_seconds: float = 300,
) -> AiExecutionContext:
    """模拟 Controller 批准后注入的可信审批上下文（digest + 任务版本）。"""

    digest = approval_binding_digest(
        snapshot=snapshot,
        capability_name=capability_name,
        capability_version="1",
        operation_key=operation_key,
        step_id=step_id,
        task_revision=task_revision,
    )
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
        budget_profile="test",
        business_scope={
            "task_id": "task-1",
            "step_id": step_id,
            "approver": "local-ui:test",
        },
        idempotency_context={"operation_key": operation_key},
        approval_digest=digest,
        approval_task_revision=task_revision,
    )


def _seed_product(product_id: str = "product-d1") -> dict[str, Any]:
    context = get_context()
    product = context.products.save_product(
        {
            "product_id": product_id,
            "name": "D1 sample",
            "sku": f"{product_id}-sku",
            "source": {
                "title": "D1 sample",
                "source_platform": "1688",
                "source_url": f"https://example.com/{product_id}",
            },
        }
    )
    return product


# ---------------------------------------------------------------- 平台查询


def test_products_index_query_matches_http_loader() -> None:
    context = get_context()
    _seed_product("product-alpha")
    _seed_product("product-beta")
    scope = PlatformQueryCapabilityScope(
        products=context.products,
        published_items_loader=lambda **kwargs: {"ok": True},
        orders_loader=lambda **kwargs: {"ok": True},
        publish_logs_loader=lambda limit=200: [],
        publishing_bus=context.publishing_bus,
    )
    result = products_index_query(ProductsIndexQueryRequest(), scope=scope)
    http_items = context.products.load_products_index()
    assert result.count == len(http_items) == 2
    assert [dict(item)["product_id"] for item in result.items] == [
        str(item.get("product_id")) for item in http_items
    ]
    assert result.snapshot_id.startswith("products_")

    selected = products_index_query(
        ProductsIndexQueryRequest(
            snapshot_id=result.snapshot_id,
            positions=(2,),
        ),
        scope=scope,
    )
    assert [dict(item)["product_id"] for item in selected.selected_items] == [
        str(http_items[1].get("product_id"))
    ]


def test_products_index_position_resolution_rejects_stale_snapshot() -> None:
    context = get_context()
    _seed_product("product-before")
    scope = PlatformQueryCapabilityScope(
        products=context.products,
        published_items_loader=lambda **kwargs: {"ok": True},
        orders_loader=lambda **kwargs: {"ok": True},
        publish_logs_loader=lambda limit=200: [],
        publishing_bus=context.publishing_bus,
    )
    initial = products_index_query(ProductsIndexQueryRequest(), scope=scope)
    _seed_product("product-after")

    with pytest.raises(BusinessCapabilityError) as stale:
        products_index_query(
            ProductsIndexQueryRequest(
                snapshot_id=initial.snapshot_id,
                positions=(1,),
            ),
            scope=scope,
        )

    assert stale.value.code == "PRODUCTS_INDEX_SNAPSHOT_STALE"


def test_platform_item_and_order_queries_map_error_codes() -> None:
    context = get_context()
    failing = {
        "ok": False,
        "error": "Mercado Libre 授权不可用",
        "error_code": "AUTH_INVALID",
    }
    scope = PlatformQueryCapabilityScope(
        products=context.products,
        published_items_loader=lambda **kwargs: dict(failing),
        orders_loader=lambda **kwargs: dict(failing),
        publish_logs_loader=lambda limit=200: [],
        publishing_bus=context.publishing_bus,
    )
    with pytest.raises(BusinessCapabilityError) as items_error:
        platform_published_items_query(
            PlatformPublishedItemsQueryRequest(), scope=scope
        )
    assert items_error.value.code == "AUTH_INVALID"
    with pytest.raises(BusinessCapabilityError) as orders_error:
        platform_orders_query(PlatformOrdersQueryRequest(), scope=scope)
    assert orders_error.value.code == "AUTH_INVALID"

    unsupported = PlatformPublishedItemsQueryRequest(platform="ozon")
    with pytest.raises(BusinessCapabilityError) as platform_error:
        platform_published_items_query(unsupported, scope=scope)
    assert platform_error.value.code == "PLATFORM_QUERY_UNSUPPORTED"


def test_publish_logs_jobs_and_status_queries() -> None:
    context = get_context()
    logs = [{"platform": "mercadolibre", "status": "success"}]
    scope = PlatformQueryCapabilityScope(
        products=context.products,
        published_items_loader=lambda **kwargs: {"ok": True},
        orders_loader=lambda **kwargs: {"ok": True},
        publish_logs_loader=lambda limit=200: list(logs)[:limit],
        publishing_bus=context.publishing_bus,
    )
    log_result = publish_logs_query(PublishLogsQueryRequest(limit=10), scope=scope)
    assert log_result.count == 1
    assert dict(log_result.items[0])["status"] == "success"

    jobs_result = publish_jobs_query(PublishJobsQueryRequest(), scope=scope)
    assert jobs_result.count == 0
    assert jobs_result.jobs == ()

    with pytest.raises(BusinessCapabilityError) as missing:
        publish_job_status_query(
            PublishJobStatusQueryRequest(job_id="missing-job"), scope=scope
        )
    assert missing.value.code == "PUBLISH_JOB_NOT_FOUND"


# ---------------------------------------------------------------- 类目查询


def _category_scope(**overrides: Any) -> CategoryQueryCapabilityScope:
    defaults: dict[str, Any] = dict(
        searcher=lambda platform, query="", site="", limit=20, timeout_seconds=None: [
            {
                "category_id": "MLB123",
                "name": "Ventiladores",
                "category_path": "Home / Fans",
            }
        ],
        attributes_loader=lambda platform,
        category_id,
        site="",
        cursor="",
        limit=50,
        timeout_seconds=None: {
            "ok": True,
            "platform": platform,
            "site": site,
            "category_id": category_id,
            "category_path": "Home / Fans",
            "attributes": [{"id": "BRAND", "name": "Marca"}],
            "next_cursor": "",
            "has_more": False,
        },
        attribute_values_loader=(
            lambda platform,
            category_id,
            attribute_id,
            site="",
            query="",
            cursor="",
            limit=50,
            timeout_seconds=None: {
                "ok": True,
                "category_id": category_id,
                "attribute_id": attribute_id,
                "values": [{"id": "V1", "name": "Champion"}],
                "next_cursor": "",
                "has_more": False,
            }
        ),
        record_loader=lambda platform,
        category_id,
        site="",
        include_attributes=False,
        timeout_seconds=None: {
            "category_id": category_id,
            "category_path": "Home / Fans",
            "attributes": {"required": [], "optional": []},
        },
        draft_context_loader=lambda body: ({}, {"error": "no"}, 404),
        product_loader=lambda body: (
            {"product_id": str(body.get("product_id"))},
            None,
            200,
        ),
    )
    defaults.update(overrides)
    return CategoryQueryCapabilityScope(**defaults)


def test_category_search_attributes_and_values_queries() -> None:
    scope = _category_scope()
    search = category_search(
        CategorySearchRequest(query="fans", platform="mercadolibre"),
        scope=scope,
        execution=_execution(),
    )
    assert search.source == "mercadolibre_live"
    assert dict(search.results[0])["category_id"] == "MLB123"

    attributes = category_attributes_query(
        CategoryAttributesQueryRequest(category_id="MLB123"),
        scope=scope,
        execution=_execution(),
    )
    assert attributes.category_path == "Home / Fans"
    assert dict(attributes.attributes[0])["id"] == "BRAND"

    values = category_attribute_values_query(
        CategoryAttributeValuesQueryRequest(
            category_id="MLB123", attribute_id="BRAND"
        ),
        scope=scope,
        execution=_execution(),
    )
    assert dict(values.values[0])["name"] == "Champion"


def test_category_query_wraps_live_api_failures() -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network down")

    scope = _category_scope(searcher=broken)
    with pytest.raises(BusinessCapabilityError) as error:
        category_search(
            CategorySearchRequest(query="fans"),
            scope=scope,
            execution=_execution(),
        )
    assert error.value.code == "CATEGORY_LIVE_API_FAILED"
    assert error.value.retryable is True


def test_category_precheck_product_path() -> None:
    scope = _category_scope()
    result = category_precheck(
        CategoryPrecheckRequest(product_id="product-d1", category_id="MLB123"),
        scope=scope,
        execution=_execution(),
    )
    assert result.category_id == "MLB123"
    assert result.category_path == "Home / Fans"
    assert isinstance(result.missing_fields, tuple)

    failing_scope = _category_scope(
        product_loader=lambda body: (
            {},
            {"error": "商品不存在", "error_code": "PRODUCT_NOT_FOUND"},
            404,
        )
    )
    with pytest.raises(BusinessCapabilityError) as error:
        category_precheck(
            CategoryPrecheckRequest(product_id="missing", category_id="MLB123"),
            scope=failing_scope,
            execution=_execution(),
        )
    assert error.value.code == "PRODUCT_NOT_FOUND"


def test_category_queries_thread_bounded_timeout_to_live_io() -> None:
    """P1-4：类目查询的同步阻塞 I/O 必须收到 execution.bounded_timeout_seconds()。"""

    captured: dict[str, Any] = {}

    def searcher(platform, query="", site="", limit=20, *, timeout_seconds=None):
        captured["search"] = timeout_seconds
        return [{"category_id": "MLB1", "name": "Fans", "category_path": "Home"}]

    def attributes_loader(
        platform, category_id, site="", *, cursor="", limit=50, timeout_seconds=None
    ):
        captured["attributes"] = timeout_seconds
        return {
            "ok": True,
            "category_id": category_id,
            "attributes": [],
            "next_cursor": "",
            "has_more": False,
        }

    scope = _category_scope(searcher=searcher, attributes_loader=attributes_loader)

    category_search(
        CategorySearchRequest(query="fans"),
        scope=scope,
        execution=_execution(deadline_seconds=42),
    )
    # 外层剩余 ~42s：底层实时接口必须收到该有界 timeout，而不是 None。
    assert captured["search"] is not None
    assert 0 < captured["search"] <= 42

    category_attributes_query(
        CategoryAttributesQueryRequest(category_id="MLB1"),
        scope=scope,
        execution=_execution(deadline_seconds=7),
    )
    assert captured["attributes"] is not None
    assert 0 < captured["attributes"] <= 7


# ---------------------------------------------------------------- 定价 / UPC


def test_pricing_calculate_passthrough_and_failure() -> None:
    context = get_context()

    def calculator(input_data: dict[str, Any]) -> dict[str, Any]:
        assert input_data["targets"] == [{"platform": "mercadolibre"}]
        assert input_data["usd_cny_rate"] == 7.2
        return {
            "ok": True,
            "targets": [{"platform": "mercadolibre", "price": "99"}],
            "exchange_rates": {"ok": True, "source": "manual"},
            "exchange_rate_mode": "manual",
        }

    scope = PricingUpcCapabilityScope(
        pricing_calculator=calculator,
        products=context.products,
        database=context.db,
    )
    result = pricing_calculate(
        PricingCalculateRequest(
            targets=({"platform": "mercadolibre"},),
            usd_cny_rate="7.2",
        ),
        scope=scope,
    )
    assert dict(result.targets[0])["price"] == "99"
    assert result.exchange_rate_mode == "manual"
    assert PricingCalculateRequest(
        targets=({"platform": "mercadolibre"},),
        mxn_usd_rate="17",
    ).mxn_usd_rate == 17.0

    def failing(input_data: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "核价失败：缺少成本"}

    failing_scope = PricingUpcCapabilityScope(
        pricing_calculator=failing,
        products=context.products,
        database=context.db,
    )
    with pytest.raises(BusinessCapabilityError) as error:
        pricing_calculate(
            PricingCalculateRequest(targets=({"platform": "mercadolibre"},)),
            scope=failing_scope,
        )
    assert error.value.code == "PRICING_CALCULATE_FAILED"


def test_pricing_calculate_surfaces_structured_field_errors() -> None:
    """确定性校验失败必须返回结构化字段错误，而不是统一抹平。"""

    context = get_context()

    def calculator(input_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            # 引擎把字段级错误放进 errors 数组；顶层没有 error。
            "errors": [
                {"field": "cost_cny", "message": "采购成本缺失"},
                {"field": "shipping_amount", "message": "物流报价金额必须大于 0"},
            ],
            "results": [],
        }

    scope = PricingUpcCapabilityScope(
        pricing_calculator=calculator,
        products=context.products,
        database=context.db,
    )
    with pytest.raises(BusinessCapabilityError) as error:
        pricing_calculate(
            PricingCalculateRequest(targets=({"platform": "ozon"},)),
            scope=scope,
        )
    assert error.value.code == "PRICING_INPUT_INVALID"
    assert "采购成本缺失" in str(error.value)
    errors = error.value.details["errors"]
    assert [item["field"] for item in errors] == [
        "cost_cny",
        "shipping_amount",
    ]


def test_pricing_calculate_manual_price_deterministic() -> None:
    """手动售价 200 CNY 的 Ozon 核价：利润 141 CNY、利润率 70.5%。"""

    from erp_web.services import pricing_service

    context = get_context()
    scope = PricingUpcCapabilityScope(
        pricing_calculator=pricing_service.pricing_result,
        products=context.products,
        database=context.db,
    )
    result = pricing_calculate(
        PricingCalculateRequest(
            targets=(
                {
                    "platform": "ozon",
                    "site": "global",
                    "listing_currency": "CNY",
                    "pricing_mode": "manual",
                    "manual_price": {"amount": "200", "currency": "CNY"},
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "CNY",
                    "shipping_amount": "10",
                },
            ),
            common={"cost_cny": "9"},
        ),
        scope=scope,
    )
    target = dict(result.targets[0])
    assert target.get("ok") is True
    assert float(target.get("profit_cny")) == pytest.approx(141.0)
    assert float(target.get("margin_percent")) == pytest.approx(70.5)
    applied = target.get("applied_price")
    assert applied["amount"] == "200.00"
    assert applied["currency"] == "CNY"


def test_upc_import_and_assign_roundtrip() -> None:
    context = get_context()
    _seed_product("product-upc")
    scope = PricingUpcCapabilityScope(
        pricing_calculator=lambda data: {"ok": True, "targets": []},
        products=context.products,
        database=context.db,
    )
    imported = upc_import(
        UpcImportRequest(values=("100000000001", "100000000002", "")),
        scope=scope,
        execution=_execution(),
    )
    assert imported.imported == 2

    assigned = upc_assign(
        UpcAssignRequest(product_id="product-upc"),
        scope=scope,
        execution=_execution(),
    )
    assert assigned.upc in {"100000000001", "100000000002"}
    reloaded = context.products.load_product_from_index("product-upc", "")
    assert str(reloaded.get("upc")) == assigned.upc

    with pytest.raises(BusinessCapabilityError) as missing:
        upc_assign(
            UpcAssignRequest(product_id="missing-product"),
            scope=scope,
            execution=_execution(),
        )
    assert missing.value.code == "PRODUCT_NOT_FOUND"


def test_upc_assign_empty_pool() -> None:
    context = get_context()
    _seed_product("product-empty-upc")
    scope = PricingUpcCapabilityScope(
        pricing_calculator=lambda data: {"ok": True, "targets": []},
        products=context.products,
        database=context.db,
    )
    with pytest.raises(BusinessCapabilityError) as error:
        upc_assign(
            UpcAssignRequest(product_id="product-empty-upc"),
            scope=scope,
            execution=_execution(),
        )
    assert error.value.code == "UPC_POOL_EMPTY"


# ---------------------------------------------------------------- 店铺授权


def test_store_auth_checklist_and_check() -> None:
    scope = StoreAuthCapabilityScope(
        checklist_loader=lambda: {"missing": ["APP_ID_MISSING"]},
        auth_tester=lambda platform, scope_name: {
            "ok": True,
            "message": "授权有效",
            "next_action": "",
            "access_token": "SECRET",
        },
    )
    checklist = store_auth_checklist(StoreAuthChecklistRequest(), scope=scope)
    assert checklist.checklist == {"missing": ["APP_ID_MISSING"]}

    check = store_auth_check(
        StoreAuthCheckRequest(platform="mercadolibre"), scope=scope
    )
    assert check.ok is True
    assert "access_token" not in check.details
    assert check.details.get("next_action") == ""

    def failing_tester(platform: str, scope_name: str) -> dict[str, Any]:
        raise RuntimeError("token 已过期")

    failing_scope = StoreAuthCapabilityScope(
        checklist_loader=lambda: {},
        auth_tester=failing_tester,
    )
    failed = store_auth_check(
        StoreAuthCheckRequest(platform="mercadolibre"), scope=failing_scope
    )
    assert failed.ok is False
    assert "token 已过期" in failed.message


# ---------------------------------------------------------------- 物流


def _valid_shipment() -> dict[str, Any]:
    return {
        "product_code": "YC001",
        "receiver": {"name": "Test Buyer", "country": "MX"},
        "packages": [{"weight": 0.5}],
        "declaration_info": [{"name_en": "fan"}],
    }


def test_logistics_preview_and_create_with_server_snapshot() -> None:
    context = get_context()
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, config: dict[str, Any]) -> None:
            captured["config"] = config

        def create_package_order(
            self,
            payload: dict[str, Any],
            access_token: str = "",
            *,
            timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return {"response": {"success": True, "result": {"waybill": "WB1"}}}

    scope = LogisticsCapabilityScope(
        context=context,
        client_factory=_FakeClient,
    )
    shipment = _valid_shipment()
    preview = logistics_shipment_preview(
        LogisticsShipmentPreviewRequest(shipment=shipment), scope=scope
    )
    # 预览是纯只读：不再携带审批 payload，审批信息只由服务端快照生成。
    assert not hasattr(preview, "approval")
    assert preview.request_payload

    request = LogisticsShipmentCreateRequest(shipment=shipment)
    # 没有可信审批上下文的直接执行必须被拒绝。
    with pytest.raises(AiToolExecutionError) as missing:
        logistics_shipment_create(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"
    assert "payload" not in captured

    snapshot = _logistics_shipment_approval_snapshot(request, scope)
    assert "Test Buyer" in snapshot.summary
    assert snapshot.canonical_payload["shipment"] == shipment

    created = logistics_shipment_create(
        request,
        scope=scope,
        execution=_approved_execution(
            snapshot, LOGISTICS_SHIPMENT_CREATE_TOOL
        ),
    )
    assert created.message
    assert captured["payload"]["product_code"] == "YC001"
    # 底层 HTTP 调用必须收到有界 timeout（bounded 上限 20s，受外层剩余时间约束）。
    assert captured["timeout_seconds"] is not None
    assert 0 < captured["timeout_seconds"] <= 20

    # 批准后篡改运单内容 → 原审批失效。
    tampered = dict(shipment)
    tampered["product_code"] = "OTHER"
    with pytest.raises(AiToolExecutionError) as stale:
        logistics_shipment_create(
            LogisticsShipmentCreateRequest(shipment=tampered),
            scope=scope,
            execution=_approved_execution(
                snapshot, LOGISTICS_SHIPMENT_CREATE_TOOL
            ),
        )
    assert stale.value.code == "LOGISTICS_APPROVAL_STALE"


def test_logistics_preview_and_create_error_mapping() -> None:
    context = get_context()
    scope = LogisticsCapabilityScope(
        context=context,
        client_factory=lambda config: None,  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(BusinessCapabilityError) as incomplete:
        logistics_shipment_preview(
            LogisticsShipmentPreviewRequest(shipment={"product_code": "YC001"}),
            scope=scope,
        )
    assert incomplete.value.code == "LOGISTICS_PREVIEW_INCOMPLETE"

    shipment = _valid_shipment()
    request = LogisticsShipmentCreateRequest(shipment=shipment)

    class _RejectedClient:
        def __init__(self, config: dict[str, Any]) -> None:
            pass

        def create_package_order(
            self,
            payload: dict[str, Any],
            access_token: str = "",
            *,
            timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            return {
                "response": {
                    "success": False,
                    "code": "ADDR_INVALID",
                    "msg": "地址无效",
                }
            }

    rejected_scope = LogisticsCapabilityScope(
        context=context,
        client_factory=_RejectedClient,
    )
    rejected_snapshot = _logistics_shipment_approval_snapshot(
        request, rejected_scope
    )
    with pytest.raises(BusinessCapabilityError) as rejected:
        logistics_shipment_create(
            request,
            scope=rejected_scope,
            execution=_approved_execution(
                rejected_snapshot, LOGISTICS_SHIPMENT_CREATE_TOOL
            ),
        )
    assert rejected.value.code == "LOGISTICS_CREATE_REJECTED"

    class _BrokenClient:
        def __init__(self, config: dict[str, Any]) -> None:
            pass

        def create_package_order(
            self,
            payload: dict[str, Any],
            access_token: str = "",
            *,
            timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            raise RuntimeError("connection refused")

    broken_scope = LogisticsCapabilityScope(
        context=context,
        client_factory=_BrokenClient,
    )
    broken_snapshot = _logistics_shipment_approval_snapshot(request, broken_scope)
    with pytest.raises(BusinessCapabilityError) as broken:
        logistics_shipment_create(
            request,
            scope=broken_scope,
            execution=_approved_execution(
                broken_snapshot, LOGISTICS_SHIPMENT_CREATE_TOOL
            ),
        )
    # 外部下单请求已发出后失败：结果是未知的，必须禁止自动重试。
    assert broken.value.code == "LOGISTICS_CREATE_OUTCOME_UNKNOWN"
    assert broken.value.retryable is False
    assert broken.value.details == {"outcome_unknown": True}

    class _TimeoutClient:
        def __init__(self, config: dict[str, Any]) -> None:
            pass

        def create_package_order(
            self,
            payload: dict[str, Any],
            access_token: str = "",
            *,
            timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            raise TimeoutError("云途 API 请求超时")

    timeout_scope = LogisticsCapabilityScope(
        context=context,
        client_factory=_TimeoutClient,
    )
    timeout_snapshot = _logistics_shipment_approval_snapshot(request, timeout_scope)
    with pytest.raises(BusinessCapabilityError) as timed_out:
        logistics_shipment_create(
            request,
            scope=timeout_scope,
            execution=_approved_execution(
                timeout_snapshot, LOGISTICS_SHIPMENT_CREATE_TOOL
            ),
        )
    # 超时后的副作用同样不得记录为普通可重试失败。
    assert timed_out.value.code == "LOGISTICS_CREATE_OUTCOME_UNKNOWN"
    assert timed_out.value.retryable is False
    assert timed_out.value.details == {"outcome_unknown": True}


def test_logistics_create_uses_outer_remaining_when_shorter_than_default() -> None:
    """P1-4：外层剩余时间短于内层默认（20s）时，必须实际采用外层剩余时间。"""

    context = get_context()
    captured: dict[str, Any] = {}

    class _Client:
        def __init__(self, config: dict[str, Any]) -> None:
            pass

        def create_package_order(
            self,
            payload: dict[str, Any],
            access_token: str = "",
            *,
            timeout_seconds: float | None = None,
        ) -> dict[str, Any]:
            captured["timeout_seconds"] = timeout_seconds
            return {"response": {"success": True, "result": {"waybill": "WB1"}}}

    scope = LogisticsCapabilityScope(context=context, client_factory=_Client)
    request = LogisticsShipmentCreateRequest(shipment=_valid_shipment())
    snapshot = _logistics_shipment_approval_snapshot(request, scope)

    # 外层只剩 3s，远小于云途默认 20s：底层 HTTP 必须收到 ~3s，而不是 20s。
    logistics_shipment_create(
        request,
        scope=scope,
        execution=_approved_execution(
            snapshot,
            LOGISTICS_SHIPMENT_CREATE_TOOL,
            deadline_seconds=3,
        ),
    )
    assert captured["timeout_seconds"] is not None
    assert 0 < captured["timeout_seconds"] <= 3
