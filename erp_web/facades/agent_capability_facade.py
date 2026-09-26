"""主 Agent 的领域能力装配；不决定下一步工具或管理 Agent 生命周期。"""

from __future__ import annotations

import logging
import json
from dataclasses import replace
from erp_web.services.ai_tool_registry import deadline_aware_tool_executor
from typing import Any


from erp_web.context import AppContext, get_context
from erp_web.facades.agent_draft_scope import authorized_draft_ids
from erp_web.runtime_units.conversation_fact_capabilities import ConversationFactScope
from erp_web.facades.category_match_facade import (
    match_category as run_category_match,
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
from erp_web.runtime_units.collect_helpers import claim_products_to_platforms, claim_products_to_markets
from erp_web.runtime_units.content_capabilities import ContentCapabilityScope
from erp_web.runtime_units.copy_generation import generate_ai_copy_bundle
from erp_web.runtime_units.draft_capabilities import DraftQueryCapabilityScope
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
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
from erp_web.runtime_units.draft_pricing_capabilities import DraftPricingCapabilityScope
from erp_web.runtime_units.upc_capabilities import (
    UpcCapabilityScope,
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
    mercadolibre_pause_user_product,
    mercadolibre_user_products,
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
from erp_web.ai_capability_composition import (
    application_capability_permissions,
    bind_global_chat_toolset as bind_chat_capabilities,
)
from erp_web.services import collect_service, config_service, product_research_service
from erp_web.services.ai_tool_catalog import AiToolBindingScope
from erp_web.services.ai_tool_registry import AiToolSet


from erp_web.runtime_units.domain_job_readers import (
    PublishJobStatusReader,
    ResearchJobStatusReader,
)

ResponseWithStatus = tuple[dict[str, Any], int]
logger = logging.getLogger(__name__)

GLOBAL_CHAT_TOOLSET_ID = "global.chat"


def global_chat_permissions() -> frozenset[str]:
    """主 Agent Execution Profile 的可信权限集合。"""

    return application_capability_permissions()


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
            ConversationFactScope: ConversationFactScope(
                context.agent_calls, context.chat_turn_claims
            ),
            ProductCapabilityScope: ProductCapabilityScope(
                products=context.products,
            ),
            CategoryCapabilityScope: CategoryCapabilityScope(
                products=context.products,
                matcher=run_category_match,
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
                    lambda product, source_platform, target_market, language, mode, app_config: (
                        generate_ai_copy_bundle(
                            product,
                            source_platform,
                            target_market,
                            language,
                            mode,
                            app_config,
                            app_dir=context.paths.app_dir,
                        )
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
                user_products_loader=mercadolibre_user_products,
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
            DraftPricingCapabilityScope: DraftPricingCapabilityScope(products=context.products),
            UpcCapabilityScope: UpcCapabilityScope(
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
                    lambda url, mode, platform, claim_platforms: collect_source_product(
                        url,
                        mode,
                        _resolved_collect_cookie(context),
                        platform or None,
                        list(claim_platforms) or None,
                        _saved_1688_api_config(context),
                    )
                ),
                batch_collector=(
                    lambda urls, mode, platform, claim_platforms: (
                        collect_batch_products(
                            list(urls),
                            mode,
                            _resolved_collect_cookie(context),
                            platform or None,
                            list(claim_platforms) or None,
                            _saved_1688_api_config(context),
                        )
                    )
                ),
                browser_tab_collector=(
                    lambda tab_url, platform_hint, product_url, claim_platforms, save_only: (
                        collect_from_browser_tab_workflow(
                            tab_url=tab_url,
                            platform_hint=platform_hint,
                            product_url=product_url,
                            port=context.paths.browser_debug_port,
                            claim_platforms=list(claim_platforms) or None,
                            save_only=save_only,
                        )
                    )
                ),
                online_1688_collector=(
                    lambda body: collect_1688_payload_service(
                        {**body, "cookie": _resolved_collect_cookie(context)}
                    )
                ),
                text_cleaner=collect_service.clean_1688_text,
                claimer=(
                    lambda product_ids, targets: claim_products_to_markets(
                        product_ids,
                        targets,
                        context=context,
                    )
                ),
            ),
            ResearchCapabilityScope: ResearchCapabilityScope(
                run_creator=lambda body: _create_research_run(context, body),
                run_loader=product_research_service.get_hot_product_run,
                active_run_loader=(product_research_service.get_active_hot_product_run),
            ),
            PublishAdminCapabilityScope: PublishAdminCapabilityScope(
                direct_publisher=run_direct_publish,
                product_loader=context.products.load_required_product_from_body,
                store_config_loader=context.config.load_store_config,
                user_product_pauser=mercadolibre_pause_user_product,
            ),
        }
    )


# -- Job 进度白名单映射 -----------------------------------------------------
#
# 以下常量与辅助函数把 PublishingBus / 选品研究已持久化的专用状态映射为
# ``JobStateSnapshot`` 的通用展示字段。只做字段白名单与长度约束，绝不把
# 凭据、完整 payload 或原始平台对象带入 UI。


#: 发布 Job 内部步骤的稳定顺序；用作通用活动列表骨架。
def build_job_status_readers(
    context: AppContext | None = None,
) -> dict[str, Any]:
    """装配按 job_type 注册的通用 Job 状态读取器（生命周期 + 进度展示）。"""

    active_context = context or get_context()
    return {
        PUBLISH_JOB_TYPE: PublishJobStatusReader(active_context.publishing_bus),
        PRODUCT_RESEARCH_JOB_TYPE: ResearchJobStatusReader(),
    }


def build_global_chat_toolset(context: AppContext | None = None) -> AiToolSet:
    active = context or get_context()
    toolset = bind_chat_capabilities(
        scope=build_capability_binding_scope(active),
        declared_permissions=global_chat_permissions(),
    )

    def guarded(binding):
        @deadline_aware_tool_executor
        def execute(arguments, execution):
            from erp_web.schemas.ai_tools import AiToolExecutionError

            if binding.definition.name in {"category_match", "draft_prepare_for_market"}:
                failures = []
                for receipt in active.agent_calls.current_turn_receipts(
                    execution.business_scope.get("conversation_id", "")
                ):
                    output = receipt["output"] or {}
                    error = output.get("error") or {}
                    previous = receipt["arguments"]
                    if (receipt["tool_name"] in {"category_match", "draft_prepare_for_market"}
                            and previous.get("target_platform") == arguments.get("target_platform")
                            and error.get("retryable")
                            and error.get("code") != "TOOL_INPUT_REQUIRED"):
                        failures.append(previous)
                if len(failures) >= 3 or sum(
                    item.get("draft_id") == arguments.get("draft_id") for item in failures
                ) >= 2:
                    raise AiToolExecutionError(
                        "CATEGORY_SERVICE_UNAVAILABLE",
                        "本轮该平台类目服务已重复失败，停止相同依赖的重试；请继续其他平台并汇总失败目标。网络恢复后可在新一轮继续。",
                        retryable=False,
                    )
            source_conversation = arguments.get("source_conversation_id")
            if source_conversation:
                claim = active.chat_turn_claims.find_for_conversation(
                    source_conversation
                )
                if (
                    claim
                    and claim.actor_id == execution.actor_id
                    and claim.tenant_id == execution.tenant_id
                ):
                    facts = active.agent_calls.user_facts(source_conversation)
                    execution = replace(
                        execution,
                        business_scope={
                            **dict(execution.business_scope),
                            "user_facts": json.dumps(facts, ensure_ascii=False),
                        },
                    )
            ids = set(
                json.loads(execution.business_scope.get("target_draft_ids", "[]"))
            )
            ids = authorized_draft_ids(
                active, execution.business_scope.get("conversation_id", ""), ids,
            )
            from erp_web.schemas.ai_tools import AiToolExecutionError

            if ids:
                product_ids = {
                    active.products.draft_record(draft_id).get("product_id")
                    for draft_id in ids
                }

                def check_targets(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if key in {
                                "draft_id",
                                "draft_ids",
                                "target_draft_id",
                                "product_id",
                                "product_ids",
                            }:
                                allowed = (
                                    product_ids if key.startswith("product") else ids
                                )
                                targets = item if isinstance(item, list) else [item]
                                if any(
                                    target and target not in allowed
                                    for target in targets
                                ):
                                    raise AiToolExecutionError(
                                        "DRAFT_OUTSIDE_AUTHORIZED_SCOPE",
                                        "写入对象不在用户所选草稿及其关联商品范围内。",
                                    )
                            check_targets(item)
                    elif isinstance(value, list):
                        for item in value:
                            check_targets(item)

                check_targets(arguments)
            requested = arguments.get("draft_id") or arguments.get("target_draft_id")
            if ids and requested and requested not in ids:
                raise AiToolExecutionError(
                    "DRAFT_OUTSIDE_AUTHORIZED_SCOPE", "该草稿不在用户所选范围内。"
                )
            if requested:
                draft = active.products.draft_record(requested)
                saved = [
                    f"{row.get('site_id') or ''}:{row.get('logistic_type') or ''}"
                    for row in draft.get("sites_to_sell", [])
                    if isinstance(row, dict)
                ]
                execution = replace(
                    execution,
                    business_scope={
                        **dict(execution.business_scope),
                        "saved_user_facts": json.dumps({"sales_target": saved}),
                    },
                )
            # 模型、网络和人工等待期间不持有商品锁；Store 在局部读改写事务内互斥。
            return binding.executor(arguments, execution)

        return replace(binding, executor=execute)

    return AiToolSet(
        toolset_id=toolset.toolset_id,
        bindings={
            name: guarded(binding)
            if binding.definition.side_effect == "write"
            else binding
            for name, binding in toolset.bindings.items()
        },
    )
