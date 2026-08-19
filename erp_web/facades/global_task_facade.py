"""全局任务 focused facade：Capability 组合根与受信 HTTP payload 函数。

从 ``AppContext`` 装配各领域 Capability Scope、Task ToolSet、通用 Job 状态
读取和 ``GlobalTaskController``；同时为主 Agent 组合 Direct + 任务控制
ToolSet。不包含 Planner，也不包含逐 Capability 手写 handler。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from erp_web.context import AppContext, get_context
from erp_web.facades.category_match_facade import (
    match_category as run_category_match,
)
from erp_web.runtime_units.attribute_fill_capabilities import (
    AttributeFillCapabilityScope,
)
from erp_web.runtime_units.category_capabilities import CategoryCapabilityScope
from erp_web.runtime_units.category_query_capabilities import (
    CategoryQueryCapabilityScope,
)
from erp_web.runtime_units.category_store import (
    fetch_category_attribute_values,
    fetch_category_attributes,
    fetch_category_record,
    search_categories_live,
)
from erp_web.runtime_units.collect_capabilities import CollectCapabilityScope
from erp_web.runtime_units.collect_helpers import claim_products_to_platforms
from erp_web.runtime_units.content_capabilities import ContentCapabilityScope
from erp_web.runtime_units.copy_generation import generate_ai_copy_bundle
from erp_web.runtime_units.draft_capabilities import DraftQueryCapabilityScope
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
)
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskControlScope,
    TASK_CONTROL_PERMISSION,
    bind_global_task_control_toolset,
)
from erp_web.runtime_units.image_capabilities import ImageCapabilityScope
from erp_web.runtime_units.logistics_capabilities import LogisticsCapabilityScope
from erp_web.runtime_units.market_prepare_capabilities import (
    MarketPrepareCapabilityScope,
)
from erp_web.runtime_units.mercadolibre_orders import mercadolibre_recent_orders
from erp_web.runtime_units.platform_query_capabilities import (
    PlatformQueryCapabilityScope,
)
from erp_web.runtime_units.pricing_runtime import calculate_price
from erp_web.runtime_units.pricing_upc_capabilities import (
    PricingUpcCapabilityScope,
)
from erp_web.runtime_units.product_capabilities import ProductCapabilityScope
from erp_web.runtime_units.product_write_capabilities import (
    ProductWriteCapabilityScope,
)
from erp_web.runtime_units.publish_admin_capabilities import (
    PublishAdminCapabilityScope,
)
from erp_web.runtime_units.publish_bus import load_publish_logs
from erp_web.runtime_units.publish_capabilities import PublishCapabilityScope
from erp_web.runtime_units.publish_mercadolibre import (
    mercadolibre_close_remote_item,
    mercadolibre_real_publish,
    mercadolibre_remote_items,
)
from erp_web.runtime_units.research_capabilities import ResearchCapabilityScope
from erp_web.runtime_units.runtime_api import (
    publish_product as run_direct_publish,
)
from erp_web.runtime_units.source_collect_workflows import (
    collect_1688_payload_service,
    collect_batch_products,
    collect_from_browser_tab as collect_from_browser_tab_workflow,
    collect_source_product,
)
from erp_web.runtime_units.store_auth_capabilities import StoreAuthCapabilityScope
from erp_web.runtime_units.store_credentials import test_store_auth
from erp_web.runtime_units.yunexpress_client import YunExpressClient
from erp_web.schemas.ai_tools import (
    PRODUCT_RESEARCH_JOB_TYPE,
    PUBLISH_JOB_TYPE,
)
from erp_web.schemas.global_tasks import (
    GlobalTaskApproveRequest,
    GlobalTaskIdRequest,
    GlobalTaskInputRequest,
    GlobalTaskRejectRequest,
    GlobalTaskResponse,
    LocalGlobalTaskState,
)
from erp_web.ai_capability_composition import (
    APPLICATION_CAPABILITY_CATALOG,
    application_capability_permissions,
    bind_global_chat_direct_toolset,
    bind_global_task_toolset,
)
from erp_web.services import collect_service, config_service, product_research_service
from erp_web.services.ai_tool_catalog import AiToolBindingScope
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.approval_session import ApprovalSessionError
from erp_web.services.global_task_controller import (
    GlobalTaskController,
    GlobalTaskControllerError,
)
from erp_web.stores.global_task_store import GlobalTaskStoreError


ResponseWithStatus = tuple[dict[str, Any], int]
logger = logging.getLogger(__name__)

GLOBAL_CHAT_TOOLSET_ID = "global.chat"


def global_chat_permissions() -> frozenset[str]:
    """主 Agent Execution Profile 的可信权限集合。"""

    return application_capability_permissions() | {TASK_CONTROL_PERMISSION}


def _resolved_collect_cookie(context: AppContext) -> str:
    """采集 Cookie 只能来自已保存配置；模型不得提供凭据。"""

    saved = str(context.config.load_app_config().get("alibaba_cookie") or "")
    return str(
        config_service.resolve_runtime_secret_value(
            saved,
            None,
            "alibaba_cookie",
        )
        or ""
    )


def _saved_1688_api_config(context: AppContext) -> dict[str, Any] | None:
    """已配置且完整的 1688 API 凭据；未配置时返回 None。"""

    api = context.config.load_app_config().get("1688_api")
    if not isinstance(api, dict):
        return None
    if not str(api.get("app_key") or "").strip():
        return None
    if not str(api.get("app_secret") or "").strip():
        return None
    return dict(api)


def _create_research_run(
    context: AppContext,
    body: dict[str, Any],
) -> dict[str, Any]:
    app_config = context.config.load_app_config()
    run = product_research_service.create_hot_product_run_async(
        context.paths.app_dir,
        body,
        app_config.get("product_research", {}),
        app_config,
    )
    return product_research_service.build_run_response(run)


def build_capability_binding_scope(
    context: AppContext,
) -> AiToolBindingScope:
    """从 AppContext 装配全部领域 Capability 的可信 Scope provider。"""

    return AiToolBindingScope(
        {
            ProductCapabilityScope: ProductCapabilityScope(
                products=context.products,
            ),
            CategoryCapabilityScope: CategoryCapabilityScope(
                products=context.products,
                matcher=run_category_match,
            ),
            AttributeFillCapabilityScope: AttributeFillCapabilityScope(
                products=context.products,
            ),
            MarketPrepareCapabilityScope: MarketPrepareCapabilityScope(
                products=context.products,
                category_matcher=run_category_match,
                claim_target_drafts=(
                    lambda product_ids, platforms: claim_products_to_platforms(
                        product_ids,
                        platforms,
                        context=context,
                    )
                ),
                copy_generator=(
                    lambda product,
                    source_platform,
                    target_market,
                    language,
                    mode,
                    app_config: generate_ai_copy_bundle(
                        product,
                        source_platform,
                        target_market,
                        language,
                        mode,
                        app_config,
                        app_dir=context.paths.app_dir,
                    )
                ),
                app_config_loader=context.config.load_app_config,
            ),
            PublishCapabilityScope: PublishCapabilityScope(
                context=context,
                publishing_bus=context.publishing_bus,
            ),
            DraftQueryCapabilityScope: DraftQueryCapabilityScope(
                products=context.products,
                draft_snapshots=context.draft_query_snapshots,
            ),
            PlatformQueryCapabilityScope: PlatformQueryCapabilityScope(
                products=context.products,
                published_items_loader=mercadolibre_remote_items,
                orders_loader=mercadolibre_recent_orders,
                publish_logs_loader=load_publish_logs,
                publishing_bus=context.publishing_bus,
            ),
            CategoryQueryCapabilityScope: CategoryQueryCapabilityScope(
                searcher=search_categories_live,
                attributes_loader=fetch_category_attributes,
                attribute_values_loader=fetch_category_attribute_values,
                record_loader=fetch_category_record,
                draft_context_loader=(
                    lambda body: load_required_draft_publish_context(
                        body,
                        context=context,
                    )
                ),
                product_loader=context.products.load_required_product_from_body,
            ),
            PricingUpcCapabilityScope: PricingUpcCapabilityScope(
                pricing_calculator=calculate_price,
                products=context.products,
                database=context.db,
            ),
            StoreAuthCapabilityScope: StoreAuthCapabilityScope(
                checklist_loader=context.config.mercadolibre_auth_checklist,
                auth_tester=test_store_auth,
            ),
            LogisticsCapabilityScope: LogisticsCapabilityScope(
                context=context,
                client_factory=YunExpressClient,
            ),
            ProductWriteCapabilityScope: ProductWriteCapabilityScope(
                products=context.products,
            ),
            ContentCapabilityScope: ContentCapabilityScope(
                products=context.products,
                app_config_loader=context.config.load_app_config,
            ),
            ImageCapabilityScope: ImageCapabilityScope(
                context=context,
            ),
            CollectCapabilityScope: CollectCapabilityScope(
                source_collector=(
                    lambda url,
                    mode,
                    platform,
                    claim_platforms: collect_source_product(
                        url,
                        mode,
                        _resolved_collect_cookie(context),
                        platform or None,
                        list(claim_platforms) or None,
                        _saved_1688_api_config(context),
                    )
                ),
                batch_collector=(
                    lambda urls,
                    mode,
                    platform,
                    claim_platforms: collect_batch_products(
                        list(urls),
                        mode,
                        _resolved_collect_cookie(context),
                        platform or None,
                        list(claim_platforms) or None,
                        _saved_1688_api_config(context),
                    )
                ),
                browser_tab_collector=(
                    lambda tab_url,
                    platform_hint,
                    product_url,
                    claim_platforms,
                    save_only: collect_from_browser_tab_workflow(
                        tab_url=tab_url,
                        platform_hint=platform_hint,
                        product_url=product_url,
                        port=context.paths.browser_debug_port,
                        claim_platforms=list(claim_platforms) or None,
                        save_only=save_only,
                    )
                ),
                online_1688_collector=(
                    lambda body: collect_1688_payload_service(
                        {**body, "cookie": _resolved_collect_cookie(context)}
                    )
                ),
                text_cleaner=collect_service.clean_1688_text,
                claimer=(
                    lambda product_ids,
                    platforms: claim_products_to_platforms(
                        product_ids,
                        platforms,
                        context=context,
                    )
                ),
            ),
            ResearchCapabilityScope: ResearchCapabilityScope(
                run_creator=lambda body: _create_research_run(context, body),
                run_loader=product_research_service.get_hot_product_run,
                active_run_loader=(
                    product_research_service.get_active_hot_product_run
                ),
            ),
            PublishAdminCapabilityScope: PublishAdminCapabilityScope(
                direct_publisher=run_direct_publish,
                product_loader=context.products.load_required_product_from_body,
                store_config_loader=context.config.load_store_config,
                real_publisher=mercadolibre_real_publish,
                item_closer=mercadolibre_close_remote_item,
            ),
        }
    )


class PublishJobStatusReader:
    """把 PublishingBus 的平台状态收敛为通用 job_id → 状态。"""

    def __init__(self, publishing_bus: Any) -> None:
        self._bus = publishing_bus

    def read_job_state(self, job_id: str) -> Mapping[str, Any]:
        raw = self._bus.get_public_status(job_id)
        platforms = (
            raw.get("platforms") if isinstance(raw.get("platforms"), dict) else {}
        )
        entries = [
            item for item in platforms.values() if isinstance(item, dict)
        ]
        statuses = [
            str(item.get("status") or "").strip().lower() for item in entries
        ]
        if not statuses or any(
            status in {"queued", "pending", "running", "retrying"}
            for status in statuses
        ):
            return {"status": "running"}
        if statuses and all(status == "success" for status in statuses):
            return {"status": "success"}
        errors = [
            str(item.get("error") or "").strip()
            for item in entries
            if item.get("error")
        ]
        return {
            "status": "failed",
            "error": "；".join(item for item in errors if item) or "平台任务失败。",
        }


class ResearchJobStatusReader:
    """把热门选品研究运行状态收敛为通用 job_id → 状态。

    运行记录持久化在 SQLite（ProductResearchRunRegistry），进程重启后仍可
    读取终态；Controller 不导入研究领域模块，只依赖这里注册的通用读取器。
    """

    def __init__(
        self,
        run_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._run_loader = (
            run_loader or product_research_service.get_hot_product_run
        )

    def read_job_state(self, job_id: str) -> Mapping[str, Any]:
        run = self._run_loader(str(job_id or "").strip())
        if run is None:
            return {
                "status": "failed",
                "error": "选品研究运行不存在或已被清理。",
            }
        status = str(run.get("status") or "").strip().lower()
        if status == "completed":
            return {"status": "success"}
        if status == "failed":
            error = str(run.get("error") or run.get("description") or "").strip()
            return {
                "status": "failed",
                "error": error or "选品研究运行失败。",
            }
        return {"status": "running"}


def build_global_task_controller(
    context: AppContext | None = None,
) -> GlobalTaskController:
    """组合唯一应用级 Catalog、Task ToolSet 与通用 Controller。"""

    active_context = context or get_context()
    binding_scope = build_capability_binding_scope(active_context)
    permissions = application_capability_permissions()
    task_toolset = bind_global_task_toolset(
        scope=binding_scope,
        declared_permissions=permissions,
    )
    return GlobalTaskController(
        store=active_context.global_tasks,
        catalog=APPLICATION_CAPABILITY_CATALOG,
        task_toolset=task_toolset,
        job_status_readers={
            PUBLISH_JOB_TYPE: PublishJobStatusReader(
                active_context.publishing_bus
            ),
            PRODUCT_RESEARCH_JOB_TYPE: ResearchJobStatusReader(),
        },
    )


def build_global_chat_toolset(
    context: AppContext | None = None,
) -> AiToolSet:
    """主 Agent ToolSet：Direct 只读能力 + 全局任务控制能力。"""

    active_context = context or get_context()
    binding_scope = build_capability_binding_scope(active_context)
    permissions = global_chat_permissions()
    direct_toolset = bind_global_chat_direct_toolset(
        scope=binding_scope,
        declared_permissions=permissions,
    )
    controller = build_global_task_controller(active_context)
    control_scope = GlobalTaskControlScope(controller=controller)
    control_toolset = bind_global_task_control_toolset(
        scope=control_scope,
        declared_permissions=permissions,
    )
    return AiToolSet(
        toolset_id=GLOBAL_CHAT_TOOLSET_ID,
        bindings={**direct_toolset.bindings, **control_toolset.bindings},
    )


def _success(response: GlobalTaskResponse) -> ResponseWithStatus:
    return response.model_dump(mode="json"), 200


def _state_success(task: LocalGlobalTaskState) -> ResponseWithStatus:
    return _success(GlobalTaskResponse(task=task, task_id=task.task_id))


def _error(exc: Exception) -> ResponseWithStatus:
    if isinstance(exc, ValidationError):
        return {
            "ok": False,
            "error": "请求字段不符合全局任务契约。",
            "error_code": "GLOBAL_TASK_REQUEST_INVALID",
        }, 400
    if isinstance(exc, ApprovalSessionError):
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, 403
    if isinstance(exc, GlobalTaskControllerError):
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, exc.status_code
    if isinstance(exc, GlobalTaskStoreError):
        status = 404 if exc.code == "GLOBAL_TASK_NOT_FOUND" else 409
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, status
    logger.exception("全局任务 HTTP 门面发生未处理异常", exc_info=exc)
    return {
        "ok": False,
        "error": "全局任务处理失败，请稍后重试。",
        "error_code": "GLOBAL_TASK_REQUEST_FAILED",
    }, 500


def get_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _state_success(
            build_global_task_controller().get_state(request.task_id)
        )
    except Exception as exc:
        return _error(exc)


def submit_global_task_input_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskInputRequest.model_validate(body)
        return _success(build_global_task_controller().submit_input(request))
    except Exception as exc:
        return _error(exc)


def approve_global_task_payload(
    body: dict[str, Any],
    *,
    approval_token: str = "",
) -> ResponseWithStatus:
    """受信批准入口：审批身份只能从校验通过的可信凭据派生。"""

    try:
        request = GlobalTaskApproveRequest.model_validate(body)
        approver = get_context().approval_session.require_approver(
            approval_token
        )
        return _success(
            build_global_task_controller().approve_task(
                request,
                approver=approver,
            )
        )
    except Exception as exc:
        return _error(exc)


def reject_global_task_payload(
    body: dict[str, Any],
    *,
    approval_token: str = "",
) -> ResponseWithStatus:
    """受信拒绝入口：审批身份只能从校验通过的可信凭据派生。"""

    try:
        request = GlobalTaskRejectRequest.model_validate(body)
        approver = get_context().approval_session.require_approver(
            approval_token
        )
        return _success(
            build_global_task_controller().reject_task(
                request,
                approver=approver,
            )
        )
    except Exception as exc:
        return _error(exc)


def cancel_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _success(build_global_task_controller().cancel_task(request))
    except Exception as exc:
        return _error(exc)


def refresh_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _success(
            build_global_task_controller().refresh_task(request.task_id)
        )
    except Exception as exc:
        return _error(exc)


__all__ = [
    "GLOBAL_CHAT_TOOLSET_ID",
    "PRODUCT_RESEARCH_JOB_TYPE",
    "PUBLISH_JOB_TYPE",
    "PublishJobStatusReader",
    "ResearchJobStatusReader",
    "ResponseWithStatus",
    "TASK_CONTROL_PERMISSION",
    "approve_global_task_payload",
    "build_capability_binding_scope",
    "build_global_chat_toolset",
    "build_global_task_controller",
    "cancel_global_task_payload",
    "get_global_task_payload",
    "global_chat_permissions",
    "refresh_global_task_payload",
    "reject_global_task_payload",
    "submit_global_task_input_payload",
]
