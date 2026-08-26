"""全局任务 focused facade：Capability 组合根与受信 HTTP payload 函数。

从 ``AppContext`` 装配各领域 Capability Scope、Task ToolSet、通用 Job 状态
读取和 ``GlobalTaskController``；同时为主 Agent 组合 Direct + 任务控制
ToolSet。不包含 Planner，也不包含逐 Capability 手写 handler。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Mapping, cast

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
    fetch_category_attribute_page,
    fetch_category_attribute_values,
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
    JobLifecycleStatus,
    JobStateActivity,
    JobStateSnapshot,
    LocalGlobalTaskState,
)
from erp_web.schemas.task_approval import normalize_task_approval_mode
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
    JobStatusReader,
)
from erp_web.services.global_task_progress_service import (
    GlobalTaskProgressProjector,
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
                attributes_loader=fetch_category_attribute_page,
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


# -- Job 进度白名单映射 -----------------------------------------------------
#
# 以下常量与辅助函数把 PublishingBus / 选品研究已持久化的专用状态映射为
# ``JobStateSnapshot`` 的通用展示字段。只做字段白名单与长度约束，绝不把
# 凭据、完整 payload 或原始平台对象带入 UI。

#: 发布 Job 内部步骤的稳定顺序；用作通用活动列表骨架。
_PUBLISH_ACTIVITY_ORDER = (
    "offer_mapping",
    "campaign_offer",
    "price",
    "stock",
    "confirmation",
)

#: 内部步骤 code → 用户可见名称（前端不识别平台专用 checkpoint）。
_PUBLISH_ACTIVITY_LABELS = {
    "offer_mapping": "提交商品资料",
    "campaign_offer": "加入店铺",
    "price": "更新价格",
    "stock": "更新库存",
    "confirmation": "确认平台状态",
}

#: 发布阶段 / 总线 stage → 用户可见阶段名称。
_PUBLISH_STAGE_LABELS = {
    "queued": "排队等待执行",
    "pending": "排队等待执行",
    "resolving_category": "解析商品类目",
    "publishing": "正在提交发布",
    "publishing_approved_payload": "正在提交发布",
    "waiting_platform_confirmation": "等待平台确认",
    "offer_mapping": "提交商品资料",
    "campaign_offer": "加入店铺",
    "price": "更新价格",
    "stock": "更新库存",
    "confirmation": "等待平台确认",
    "terminal": "已完成",
    "finished": "已完成",
    "success": "已完成",
    "retrying": "退避重试中",
    "failed": "发布失败",
}


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    """把 ISO / 本地时间字符串解析为带时区 datetime；失败返回 None。"""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        # PublishingBus 使用本地 naive 时间；按本地时区解释，保证耗时正确。
        parsed = parsed.astimezone()
    return parsed


def _epoch_datetime(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _publish_focus_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择一个用于展示进度的平台条目：活跃优先，其次含 checkpoint。"""

    active = [
        item
        for item in entries
        if str(item.get("status") or "").strip().lower()
        in {"queued", "pending", "running", "retrying"}
    ]
    candidates = active or entries
    for item in candidates:
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("checkpoint"), dict):
            return item
    return candidates[0] if candidates else None


def _publish_checkpoint(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """提取平台 checkpoint。

    进行中的 pending 结果把 checkpoint 放在 ``result.result.checkpoint``；
    终态结果放在 ``result.checkpoint``。与轮询函数相同的防御性查找。
    """

    if not isinstance(entry, dict):
        return None
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, dict):
        return checkpoint
    inner = result.get("result")
    if isinstance(inner, dict):
        inner_checkpoint = inner.get("checkpoint")
        if isinstance(inner_checkpoint, dict):
            return inner_checkpoint
    return None


def _evidence_time(evidence: Mapping[str, Any], code: str) -> datetime | None:
    item = evidence.get(code)
    if not isinstance(item, dict):
        return None
    return _parse_datetime(item.get("at") or item.get("checked_at"))


def _publish_activities(
    checkpoint: Mapping[str, Any],
) -> tuple[JobStateActivity, ...]:
    completed = {
        str(step)
        for step in (checkpoint.get("completed_steps") or [])
    }
    evidence = (
        checkpoint.get("evidence")
        if isinstance(checkpoint.get("evidence"), dict)
        else {}
    )
    activities: list[JobStateActivity] = []
    running_assigned = False
    for code in _PUBLISH_ACTIVITY_ORDER:
        if code in completed:
            activity_status = "completed"
            completed_at = _evidence_time(evidence, code)
        elif not running_assigned:
            activity_status = "running"
            completed_at = None
            running_assigned = True
        else:
            activity_status = "queued"
            completed_at = None
        activities.append(
            JobStateActivity(
                code=code,
                label=_PUBLISH_ACTIVITY_LABELS.get(code, code),
                status=activity_status,
                completed_at=completed_at,
            )
        )
    return tuple(activities)


def _publish_phase_started_at(
    checkpoint: Mapping[str, Any],
) -> datetime | None:
    """当前阶段开始时间 ≈ 上一个已完成步骤的完成时间。"""

    completed = {
        str(step)
        for step in (checkpoint.get("completed_steps") or [])
    }
    evidence = (
        checkpoint.get("evidence")
        if isinstance(checkpoint.get("evidence"), dict)
        else {}
    )
    previous: str | None = None
    for code in _PUBLISH_ACTIVITY_ORDER:
        if code not in completed:
            break
        previous = code
    if previous is None:
        return None
    return _evidence_time(evidence, previous)


def _publish_display_fields(
    raw: Mapping[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """从发布 Job 公共状态提取白名单展示字段。"""

    attempts = [int(item.get("attempts") or 0) for item in entries]
    fields: dict[str, Any] = {
        "updated_at": _parse_datetime(raw.get("updated_at")),
        "attempt": (
            max(attempts) if any(value > 0 for value in attempts) else None
        ),
    }

    focus = _publish_focus_entry(entries)
    checkpoint = _publish_checkpoint(focus)
    if checkpoint is not None:
        phase = str(checkpoint.get("phase") or "").strip().lower()
        stage_label = _PUBLISH_STAGE_LABELS.get(phase, "正在执行发布")
        summary = (
            "远端写入已完成，正在确认店铺商品状态"
            if phase == "confirmation"
            else f"正在{stage_label}"
        )
        last_response = (
            checkpoint.get("last_response_summary")
            if isinstance(checkpoint.get("last_response_summary"), dict)
            else {}
        )
        fields.update(
            {
                "stage_code": _truncate(phase, 120),
                "stage_label": _truncate(stage_label, 200),
                "summary": _truncate(summary, 500),
                "retry_count": max(0, int(checkpoint.get("retries") or 0)),
                "next_check_at": _epoch_datetime(checkpoint.get("next_poll_at")),
                "last_external_status": _truncate(
                    last_response.get("status") or "", 80
                ),
                "phase_started_at": _publish_phase_started_at(checkpoint),
                "activities": _publish_activities(checkpoint),
            }
        )
        return fields

    stage = str((focus or {}).get("stage") or "").strip().lower()
    if stage:
        fields.update(
            {
                "stage_code": _truncate(stage, 120),
                "stage_label": _truncate(
                    _PUBLISH_STAGE_LABELS.get(stage, stage), 200
                ),
                "summary": _truncate(
                    _PUBLISH_STAGE_LABELS.get(stage, "正在执行发布"), 500
                ),
            }
        )
    return fields


def _research_activities(run: Mapping[str, Any]) -> tuple[JobStateActivity, ...]:
    source_status = (
        run.get("source_status")
        if isinstance(run.get("source_status"), list)
        else []
    )
    activities: list[JobStateActivity] = []
    for item in source_status:
        if not isinstance(item, dict):
            continue
        code = str(item.get("source_id") or item.get("source") or "").strip()
        if not code:
            continue
        raw_status = str(item.get("status") or "").strip().lower()
        if raw_status in {"failed", "error"}:
            activity_status = "failed"
        else:
            activity_status = "completed"
        activities.append(
            JobStateActivity(
                code=_truncate(code, 80),
                label=_truncate(str(item.get("source") or code), 200),
                status=activity_status,
            )
        )
    return tuple(activities[:50])


class PublishJobStatusReader:
    """把 PublishingBus 的平台状态收敛为通用 job_id → 类型化快照。

    生命周期字段（status/error）与原实现一致；展示字段从已持久化的平台
    checkpoint 白名单映射而来（阶段、内部活动、重试、下次检查、最近外部
    状态），绝不透传凭据、完整 payload 或原始平台对象。
    """

    def __init__(self, publishing_bus: Any) -> None:
        self._bus = publishing_bus

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        try:
            raw = self._bus.get_public_status(job_id)
        except Exception:
            # Job 不存在或读取失败：生命周期保持 running（不误判终态），
            # 任务卡展示降级为“暂时无法读取后台任务进度”。
            return JobStateSnapshot(status="running", available=False)
        if not isinstance(raw, dict) or not raw:
            return JobStateSnapshot(status="running", available=False)

        platforms = (
            raw.get("platforms") if isinstance(raw.get("platforms"), dict) else {}
        )
        entries = [
            item for item in platforms.values() if isinstance(item, dict)
        ]
        statuses = [
            str(item.get("status") or "").strip().lower() for item in entries
        ]

        # 生命周期判定与原实现保持一致。
        status: str
        error = ""
        if not statuses or any(
            value in {"queued", "pending", "running", "retrying"}
            for value in statuses
        ):
            status = "running"
        elif all(value == "success" for value in statuses):
            status = "success"
        else:
            errors = [
                str(item.get("error") or "").strip()
                for item in entries
                if item.get("error")
            ]
            status = "failed"
            error = "；".join(item for item in errors if item) or "平台任务失败。"

        display = _publish_display_fields(raw, entries)
        return JobStateSnapshot(
            status=cast(JobLifecycleStatus, status),
            error=_truncate(error, 2000),
            available=True,
            **display,
        )


class ResearchJobStatusReader:
    """把热门选品研究运行状态收敛为通用 job_id → 类型化快照。

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

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        run = self._run_loader(str(job_id or "").strip())
        if run is None:
            return JobStateSnapshot(
                status="failed",
                error="选品研究运行不存在或已被清理。",
            )
        status = str(run.get("status") or "").strip().lower()
        error = ""
        if status == "completed":
            lifecycle: str = "success"
        elif status == "failed":
            lifecycle = "failed"
            error = str(run.get("error") or run.get("description") or "").strip()
        else:
            lifecycle = "running"

        summary = str(
            run.get("progress_description") or run.get("description") or ""
        ).strip()
        return JobStateSnapshot(
            status=cast(JobLifecycleStatus, lifecycle),
            error=_truncate(error, 2000),
            available=True,
            summary=_truncate(summary, 500),
            updated_at=_parse_datetime(
                run.get("completed_at") or run.get("created_at")
            ),
            activities=_research_activities(run),
        )


def build_job_status_readers(
    context: AppContext | None = None,
) -> dict[str, JobStatusReader]:
    """装配按 job_type 注册的通用 Job 状态读取器（生命周期 + 进度展示）。"""

    active_context = context or get_context()
    return {
        PUBLISH_JOB_TYPE: PublishJobStatusReader(active_context.publishing_bus),
        PRODUCT_RESEARCH_JOB_TYPE: ResearchJobStatusReader(),
    }


#: Global Task 步骤 capability → 用户可读名称；缺失时回落 capability 名。
#: 仅服务任务卡展示，不参与任何执行语义。
_GLOBAL_TASK_CAPABILITY_LABELS = {
    "drafts_query": "查询草稿",
    "product_read": "读取商品",
    "draft_read": "读取草稿",
    "product_profile_patch": "更新商品主档",
    "product_delete": "删除商品",
    "draft_stock_update": "更新草稿库存",
    "draft_pricing_apply": "更新草稿价格",
    "draft_delete": "删除草稿",
    "product_attributes_update": "更新商品属性",
    "product_images_prepare": "准备商品图片",
    "category_match": "匹配类目",
    "product_attributes_fill": "补全商品属性",
    "draft_prepare_for_market": "准备目标市场草稿",
    "product_publish_validate": "校验发布条件",
    "product_publish_request": "提交商品发布",
    "copy_generate": "生成营销文案",
    "copy_generate_batch": "批量生成文案",
    "image_prompts_generate": "生成图片提示词",
    "text_translate": "翻译文本",
    "image_pool_upload": "上传图片",
    "image_pool_save": "保存图片",
    "image_pool_action": "处理图片",
    "image_pool_sync_generated": "同步生成图片",
    "image_translate": "翻译图片",
    "image_edit": "编辑图片",
    "logistics_shipment_preview": "预览发货单",
    "logistics_shipment_create": "创建发货单",
    "upc_assign": "分配 UPC",
    "upc_import": "导入 UPC",
    "source_collect": "采集源商品",
    "collect_batch": "批量采集",
    "collect_from_browser_tab": "从浏览器标签采集",
    "collect_1688": "采集 1688 商品",
    "collect_1688_clean": "清洗 1688 数据",
    "claim_products": "认领商品到草稿",
    "research_hot_products_search": "搜索热门商品",
    "research_run_status_query": "查询研究进度",
    "product_publish_direct": "直接发布",
    "publish_real_confirm": "确认真实发布",
    "platform_item_close": "关闭平台商品",
}


def _capability_display_label(capability_name: str) -> str:
    return _GLOBAL_TASK_CAPABILITY_LABELS.get(
        str(capability_name or "").strip(),
        "",
    )


def build_global_task_progress_projector(
    context: AppContext | None = None,
) -> GlobalTaskProgressProjector:
    """装配只读进度投影器；复用受信 Reader，不触碰任务执行链。"""

    return GlobalTaskProgressProjector(
        job_status_readers=build_job_status_readers(context),
        capability_label_loader=_capability_display_label,
    )


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
        job_status_readers=build_job_status_readers(active_context),
        deferred_links=active_context.deferred_task_links,
        approval_mode_loader=lambda: normalize_task_approval_mode(
            active_context.config.load_app_config().get("task_approval_mode")
        ),
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


def _view_success(task: LocalGlobalTaskState) -> ResponseWithStatus:
    """HTTP/UI 读模型：持久化任务 + 计算型执行进度视图。

    进度投影失败只丢进度字段，任务主体照常返回；GET 与各写响应共用同一
    包装，避免写操作完成后任务卡短暂丢失进度。Pydantic 控制 Tool 不走
    这里，继续使用原领域响应。
    """

    view = build_global_task_progress_projector().build_view_response(task)
    return view.model_dump(mode="json"), 200


def _success(response: GlobalTaskResponse) -> ResponseWithStatus:
    return _view_success(response.task)


def _state_success(task: LocalGlobalTaskState) -> ResponseWithStatus:
    return _view_success(task)


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


def read_global_task_state_payload(task_id: str) -> ResponseWithStatus:
    """按 task_id 纯读任务状态；供任务卡 GET 刷新使用，不推进任务。"""

    try:
        normalized = str(task_id or "").strip()
        if not normalized:
            return {
                "ok": False,
                "error": "task_id 不能为空。",
                "error_code": "GLOBAL_TASK_REQUEST_INVALID",
            }, 400
        return _state_success(
            build_global_task_controller().get_state(normalized)
        )
    except Exception as exc:
        return _error(exc)


def conversation_task_link_payload(
    conversation_id: str,
) -> ResponseWithStatus:
    """conversation → 未解决 Deferred 任务的纯读关联。

    只返回 ``link_status='ready'`` 的 link；``awaiting_history`` provisional
    link 仅供服务端恢复/清理，不向前端宣告任务已受理。任务终结并 continuation
    提交后 link 变为 resolved，本接口回到空任务。

    报告 A-15：本接口只返回最小公开状态（conversation_id、task_id、
    link_status），不再内嵌完整 Task。任务的步骤、参数、结果与审批内容由前端
    通过规范 Task GET（单一 owner）读取，避免同一数据两个暴露入口。
    """

    from erp_web.stores.pydantic_deferred_task_link_store import READY

    try:
        context = get_context()
        normalized = str(conversation_id or "").strip()
        if not normalized:
            return {
                "ok": False,
                "error": "conversation_id 不能为空。",
                "error_code": "AI_CHAT_CONVERSATION_ID_INVALID",
            }, 400
        link = context.deferred_task_links.active_for_conversation(normalized)
        if link is None or link.link_status != READY:
            return {
                "ok": True,
                "conversation_id": normalized,
                "task": None,
                "task_id": "",
                "link_status": "",
            }, 200
        # 校验任务存在（不存在则按 404 返回），但不把任务详情内嵌到关联响应。
        build_global_task_controller(context).get_state(link.task_id)
        return {
            "ok": True,
            "conversation_id": normalized,
            "task_id": link.task_id,
            "link_status": link.link_status,
            "task": None,
        }, 200
    except GlobalTaskStoreError as exc:
        status = 404 if exc.code == "GLOBAL_TASK_NOT_FOUND" else 409
        return {"ok": False, "error": str(exc), "error_code": exc.code}, status
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
    "build_global_task_progress_projector",
    "build_job_status_readers",
    "cancel_global_task_payload",
    "conversation_task_link_payload",
    "global_chat_permissions",
    "read_global_task_state_payload",
    "reject_global_task_payload",
    "submit_global_task_input_payload",
]
