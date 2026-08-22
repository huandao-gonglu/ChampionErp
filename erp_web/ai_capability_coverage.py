from __future__ import annotations

"""Endpoint Coverage Manifest：全部 HTTP 入口的 AI Capability 覆盖治理清单。

Manifest 是静态数据：对 ``erp_web/http_route_units/*::HANDLED_PATHS`` 的
每个入口给出 method / path / business_domain / disposition /
capability_names / reason。它只用于覆盖治理（架构测试），不驱动运行时
路由，Catalog 也不反向依赖 HTTP。

disposition：
- ``capability``：入口语义已由列出的 Capability 覆盖；
- ``internal_only``：受信内部协议入口（如全局任务 UI 门面）；
- ``excluded``：基础设施/凭据/协议入口，按规划不包装为 Capability
  （最终要求是零 unclassified，而不是零 excluded）。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from erp_web.http_route_units import (
    ai_work_routes,
    get_routes,
    image_routes,
)
from erp_web.http_routes import POST_API_ROUTES


Disposition = Literal["capability", "internal_only", "excluded"]
Method = Literal["GET", "POST"]


class AiCapabilityCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Method
    path: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    business_domain: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    disposition: Disposition
    capability_names: tuple[str, ...] = ()
    reason: Annotated[str, StringConstraints(max_length=600)] = ""

    @model_validator(mode="after")
    def validate_disposition_shape(self) -> "AiCapabilityCoverageEntry":
        if self.disposition == "capability":
            if not self.capability_names:
                raise ValueError(
                    f"{self.method} {self.path}：capability 分类必须列出能力"
                )
        else:
            if self.capability_names:
                raise ValueError(
                    f"{self.method} {self.path}："
                    f"{self.disposition} 分类不得引用能力"
                )
            if not self.reason.strip():
                raise ValueError(
                    f"{self.method} {self.path}："
                    f"{self.disposition} 分类必须给出理由"
                )
        return self


def all_handled_endpoints() -> frozenset[tuple[str, str]]:
    """机械汇总全部受信 HTTP 入口（method, path）。

    只依赖 route unit 自身的 HANDLED_PATHS / IMAGE_POST_PATHS 声明，
    不依赖 Manifest，供架构测试做零 unclassified 校验。
    """

    endpoints: set[tuple[str, str]] = set()
    endpoints.update(("GET", path) for path in get_routes.HANDLED_PATHS)
    endpoints.update(("GET", path) for path in ai_work_routes.HANDLED_PATHS)
    endpoints.update(("POST", path) for path in POST_API_ROUTES)
    endpoints.update(("POST", path) for path in image_routes.IMAGE_POST_PATHS)
    return frozenset(endpoints)


AI_CAPABILITY_COVERAGE_MANIFEST: tuple[AiCapabilityCoverageEntry, ...] = (
    # -------------------------------------------------- 前端页面（GET）
    AiCapabilityCoverageEntry(
        method="GET",
        path="/",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/research",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/collect",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/library",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/drafts",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/ml-items",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/edit",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/media",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/pricing",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/publish",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/pending",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/settings",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/auth",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/logs",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/aiWork",
        business_domain="前端页面",
        disposition="excluded",
        reason="SPA 页面壳，前端静态路由，无业务行为。",
    ),
    # -------------------------------------------------- 平台查询（GET）
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/products-index",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("products_index_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/drafts-index",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("drafts_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/mercadolibre/published-items",
        business_domain="平台商品与订单",
        disposition="capability",
        capability_names=("platform_published_items_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/mercadolibre/orders",
        business_domain="平台商品与订单",
        disposition="capability",
        capability_names=("platform_orders_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/publish-logs",
        business_domain="发布",
        disposition="capability",
        capability_names=("publish_logs_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/publish-bus/jobs",
        business_domain="发布",
        disposition="capability",
        capability_names=("publish_jobs_query",),
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/publish-bus/status",
        business_domain="发布",
        disposition="capability",
        capability_names=("publish_job_status_query",),
    ),
    # -------------------------------------------------- 基础设施（GET）
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/state",
        business_domain="聚合基础设施",
        disposition="excluded",
        reason="页面聚合初始状态，协议级基础设施，不属于单一业务能力。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/ai-config",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="AI 模型配置读取，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/browser-debug/status",
        business_domain="浏览器调试",
        disposition="excluded",
        reason="浏览器调试连接状态，采集基础设施，非业务能力。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/file",
        business_domain="静态资源",
        disposition="excluded",
        reason="本地图片静态文件服务，基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/auth/mercadolibre",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="OAuth 授权说明静态页面。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/auth/ozon",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="授权说明静态页面。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/auth/mercadolibre/callback",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="OAuth 回跳与 code 换取 token，凭据流程不包装为能力。",
    ),
    # -------------------------------------------------- AI Work 会话（GET）
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="会话列表读取，chat/会话 transport，不属于业务能力。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations/",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="会话列表读取（尾斜杠别名），chat/会话 transport。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations/<conversation_id>/ui-messages",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="会话消息读取，chat/会话 transport，不属于业务能力。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations/<conversation_id>/task-link",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="conversation→未解决 Deferred 任务的只读关联，chat/会话 transport。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations/<conversation_id>/events",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="活动 conversation 官方事件订阅 SSE，chat/会话 transport。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/global-tasks/<task_id>",
        business_domain="全局任务",
        disposition="internal_only",
        reason="受信任务卡的纯读任务状态；主 Agent 等价路径是 global_task_get。",
    ),
    # -------------------------------------------------- 主 Agent 运输（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-chat/runs",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="主 Agent 对话 SSE transport；能力经 global.chat ToolSet 暴露。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-presentations",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="AI 演示 SSE transport，协议基础设施。",
    ),
    # -------------------------------------------------- 全局任务门面（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/global-task-input",
        business_domain="全局任务",
        disposition="internal_only",
        reason="受信任务 UI 的 HTTP 门面；主 Agent 等价路径是 global_task_submit_input。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/global-task-approve",
        business_domain="全局任务",
        disposition="internal_only",
        reason="受信任务 UI 的人工审批门；必须携带审批 token，主 Agent 不具备等价路径。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/global-task-reject",
        business_domain="全局任务",
        disposition="internal_only",
        reason="受信任务 UI 的人工拒绝门；必须携带审批 token，主 Agent 不具备等价路径。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/global-task-cancel",
        business_domain="全局任务",
        disposition="internal_only",
        reason="受信任务 UI 的 HTTP 门面；主 Agent 等价路径是 global_task_cancel。",
    ),
    # -------------------------------------------------- 商品与草稿（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/load-product",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("product_read",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/save-product",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("product_save",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/delete-products",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("product_delete",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/load-draft",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("draft_read",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/save-draft",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("draft_save",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/delete-draft",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("draft_delete",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/assign-upc",
        business_domain="定价与 UPC",
        disposition="capability",
        capability_names=("upc_assign",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/upc-pool/import",
        business_domain="定价与 UPC",
        disposition="capability",
        capability_names=("upc_import",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/calculate-price",
        business_domain="定价与 UPC",
        disposition="capability",
        capability_names=("pricing_calculate",),
    ),
    # -------------------------------------------------- 类目与属性（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/category-search",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_search",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/category-attrs",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_attributes_query",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/category-attribute-values",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_attribute_values_query",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/category-precheck",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_precheck",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/category-ai-fill",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("product_attributes_fill",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/category-match",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_match",),
    ),
    # -------------------------------------------------- 采集与认领（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-source",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("source_collect",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-batch",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("collect_batch",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-1688",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("collect_1688",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-1688-clean",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("collect_1688_clean",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-from-browser-tab",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("collect_from_browser_tab",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/claim-products",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("claim_products",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/browser-debug/open-profile",
        business_domain="浏览器调试",
        disposition="excluded",
        reason="打开本地浏览器 profile，本机调试基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/open-1688-browser",
        business_domain="浏览器调试",
        disposition="excluded",
        reason="打开任意 URL 的浏览器会话，本机调试基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-extension-payload",
        business_domain="采集与认领",
        disposition="excluded",
        reason="浏览器扩展原始 payload 接收协议，不包装为能力。",
    ),
    # -------------------------------------------------- 文案与翻译（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/generate-copy",
        business_domain="文案与翻译",
        disposition="capability",
        capability_names=("copy_generate",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/generate-copy-batch",
        business_domain="文案与翻译",
        disposition="capability",
        capability_names=("copy_generate_batch",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/generate-image-prompts",
        business_domain="文案与翻译",
        disposition="capability",
        capability_names=("image_prompts_generate",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/test-ai-model",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="AI 模型探测，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/text-translate",
        business_domain="文案与翻译",
        disposition="capability",
        capability_names=("text_translate",),
    ),
    # -------------------------------------------------- 图片（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-pool/upload",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_pool_upload",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-pool/save",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_pool_save",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-pool/action",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_pool_action",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-pool/sync-generated",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_pool_sync_generated",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-translate",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_translate",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/image-edit",
        business_domain="图片",
        disposition="capability",
        capability_names=("image_edit",),
    ),
    # -------------------------------------------------- 发布（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/publish-precheck",
        business_domain="发布",
        disposition="capability",
        capability_names=("product_publish_validate",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/publish-payload-preview",
        business_domain="发布",
        disposition="capability",
        capability_names=("product_publish_validate",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/publish-bus/enqueue",
        business_domain="发布",
        disposition="capability",
        capability_names=("product_publish_request",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/publish-product",
        business_domain="发布",
        disposition="capability",
        capability_names=("product_publish_direct",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/confirm-real-publish",
        business_domain="发布",
        disposition="capability",
        capability_names=("publish_real_confirm",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/close-item",
        business_domain="发布",
        disposition="capability",
        capability_names=("platform_item_close",),
    ),
    # -------------------------------------------------- 物流（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/logistics/yunexpress/preview",
        business_domain="物流",
        disposition="capability",
        capability_names=("logistics_shipment_preview",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/logistics/yunexpress/create-shipment",
        business_domain="物流",
        disposition="capability",
        capability_names=("logistics_shipment_create",),
    ),
    # -------------------------------------------------- 店铺授权（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/auth-checklist",
        business_domain="店铺授权状态",
        disposition="capability",
        capability_names=("store_auth_checklist",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/test-store-auth",
        business_domain="店铺授权状态",
        disposition="capability",
        capability_names=("store_auth_check",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/real-auth-test",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="真实 OAuth token 诊断测试，原始凭据诊断不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/test-api-config",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="采集 API 配置连通性测试，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/auth-link",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="OAuth 授权链接生成，凭据流程不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/open-auth-link",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="打开授权链接（任意 URL），本机浏览器基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/exchange-code",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="OAuth code 换取 token，凭据流程不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/refresh-token",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="OAuth refresh token，凭据流程不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/ai-config/save",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="AI 配置保存，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/save-settings",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="原始设置保存，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/store-auth/clear",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="清除已保存凭据，原始密钥读写不包装为能力。",
    ),
    # -------------------------------------------------- 平台通知（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/mercadolibre/notifications",
        business_domain="平台商品与订单",
        disposition="excluded",
        reason="平台 webhook/notification 接收，外部协议入口。",
    ),
    # -------------------------------------------------- 商品研究（POST）
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/product-research/hot-products/search",
        business_domain="商品研究",
        disposition="capability",
        capability_names=("research_hot_products_search",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/product-research/search-providers/test",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="搜索 Provider 连通性测试，配置管理基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/product-research/source-registry/save",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="选品源注册表配置保存，配置管理基础设施。",
    ),
)


def coverage_manifest_endpoints() -> frozenset[tuple[str, str]]:
    """Manifest 声明的入口集合（method, path）。"""

    return frozenset((entry.method, entry.path) for entry in AI_CAPABILITY_COVERAGE_MANIFEST)


__all__ = [
    "AI_CAPABILITY_COVERAGE_MANIFEST",
    "AiCapabilityCoverageEntry",
    "all_handled_endpoints",
    "coverage_manifest_endpoints",
]
