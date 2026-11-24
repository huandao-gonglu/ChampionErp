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

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from erp_web.http_route_units import (
    account_routes,
    business_query_routes,
    task_queue_routes,
    wechat_routes,
    alibaba_authorization_routes,
    ai_work_routes,
    get_routes,
    image_routes,
    image_hosting_routes,
    pricing_workspace_routes,
    online_product_routes,
    order_routes,
    fulfillment_routes,
)
from erp_web.http_routes import GET_API_ROUTES, POST_API_ROUTES


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
                    f"{self.method} {self.path}：{self.disposition} 分类不得引用能力"
                )
            if not self.reason.strip():
                raise ValueError(
                    f"{self.method} {self.path}：{self.disposition} 分类必须给出理由"
                )
        return self


def all_handled_endpoints() -> frozenset[tuple[str, str]]:
    """机械汇总全部受信 HTTP 入口（method, path）。

    只依赖 route unit 自身的 HANDLED_PATHS / IMAGE_POST_PATHS 声明，
    不依赖 Manifest，供架构测试做零 unclassified 校验。
    """

    endpoints: set[tuple[str, str]] = set()
    endpoints.update(("GET", path) for path in GET_API_ROUTES)
    endpoints.update(("GET", path) for path in account_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in business_query_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in task_queue_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in wechat_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in alibaba_authorization_routes.GET_HANDLED_PATHS)
    endpoints.update(("GET", path) for path in get_routes.HANDLED_PATHS)
    endpoints.update(("GET", path) for path in ai_work_routes.HANDLED_PATHS)
    endpoints.update(("GET", path) for path in online_product_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in order_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in fulfillment_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in image_hosting_routes.GET_API_ROUTES)
    endpoints.update(("GET", path) for path in pricing_workspace_routes.GET_API_ROUTES)
    endpoints.update(("POST", path) for path in POST_API_ROUTES)
    endpoints.update(("POST", path) for path in image_routes.IMAGE_POST_PATHS)
    return frozenset(endpoints)


AI_CAPABILITY_COVERAGE_MANIFEST: tuple[AiCapabilityCoverageEntry, ...] = (
    AiCapabilityCoverageEntry(method="GET", path="/api/v1/product-research/hot-products/runs", business_domain="选品任务", disposition="capability", capability_names=("research_run_status_query",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/v1/product-research/source-registry", business_domain="配置基础设施", disposition="excluded", reason="受信编辑视图包含数据源凭据占位；普通配置元数据由 settings.product_research 提供。"),
    AiCapabilityCoverageEntry(method="GET", path="/api/business-data/catalog", business_domain="业务数据", disposition="capability", capability_names=("business_data_catalog",)),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="业务数据", disposition="capability", capability_names=("business_data_read",)) for method, path in (
        ("POST", "/api/business-data/read"),
    )),
    *(AiCapabilityCoverageEntry(method="GET", path=path, business_domain="业务查询", disposition="capability",
                                capability_names=("business_data_read",))
      for path in sorted(business_query_routes.GET_API_ROUTES)),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="共享设置与运费表", disposition="internal_only",
        reason="管理员维护共享企业凭据、汇率 API 与默认运费，用户显式导入个人表或确认覆盖；模型只消费生效配置。")
      for method, path in (
          ("GET", "/api/admin/settings"), ("POST", "/api/admin/settings"),
          ("POST", "/api/shipping/tariffs/import"),
          ("POST", "/api/shipping/tariffs/upgrade"),
      )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="授权基础设施", disposition="excluded",
        reason="个人账号、会话、用户管理及 New API 账号交接仅供受信界面、内部服务和部署命令使用，不向模型提供身份或权限操作。")
      for method, path in (
          ("GET", "/api/account/registration-config"), ("GET", "/api/account/session"), ("GET", "/api/admin/users"),
          ("GET", "/api/account/newapi"),
          *(("POST", path) for path in account_routes.HANDLED_PATHS),
      )),
    *(AiCapabilityCoverageEntry(method="GET", path=path, business_domain="前端页面", disposition="excluded",
        reason="账号界面只加载页面资源；业务 API 独立认证。")
      for path in ("/login", "/register", "/account", "/admin/users")),
    *(AiCapabilityCoverageEntry(method='POST', path='/api/desktop/' + suffix,
        business_domain='本机连接协议', disposition='excluded',
        reason='仅 Electron 主进程使用的调用关联与素材交接；模型不能选择桌面或消费传输协议。')
      for suffix in ('connect', 'poll', 'result', 'asset', 'disconnect')),
    *(AiCapabilityCoverageEntry(method='POST', path='/api/desktop-handoff/' + suffix,
        business_domain='桌面导航交接', disposition='excluded',
        reason='网页与同账号桌面之间的短期页面交接和确认，不提供业务执行或设备授权。')
      for suffix in ('create', 'status', 'resolve', 'acknowledge', 'installers')),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="授权基础设施", disposition="excluded",
        reason="1688 买家授权状态、浏览器登录、一次性回调、企业自用 Token 绑定及管理员恢复企业万邦 Key 仅供授权与管理界面使用。") for method, path in (
        ("GET", "/api/alibaba/crossborder/authorization"),
        ("POST", "/api/alibaba/crossborder/authorization/start"),
        ("GET", "/api/alibaba/authorization"), ("GET", "/auth/alibaba/callback"),
        ("POST", "/api/alibaba/authorization/start"), ("POST", "/api/alibaba/authorization/complete"),
        ("POST", "/api/alibaba/authorization/onebound/complete"),
        ("POST", "/api/alibaba/authorization/self-use-token"),
        ("POST", "/api/alibaba/onebound/key/restore"),
    )),
    AiCapabilityCoverageEntry(method="POST", path="/api/publish-bus/retry", business_domain="商品发布", disposition="internal_only",
        reason="任务队列中由用户显式重试原批准发布内容；不增加 Agent 自动重发入口。"),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="禁售类目设置", disposition="internal_only",
        reason="受信系统设置界面维护禁售名单；模型预检和发布使用当前名单，不能通过工具修改禁售规则。") for method, path in (
        ("POST", "/api/system-settings/forbidden-categories/import"),
    )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="任务队列", disposition="internal_only",
        reason="可信页面的跨领域任务投影与有限确认门面；AI 继续使用原发布/在线商品领域能力，查询共用同一确认预算。") for method, path in (
        ("GET", "/api/tasks"), ("POST", "/api/tasks/check"),
    )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="配置基础设施", disposition="excluded",
        reason="仅受信设置界面管理扫码、配对及会话绑定，不作为模型工具；微信消息通过共用原生对话入口接收。") for method, path in (
        ("GET", "/api/wechat/status"), ("POST", "/api/wechat/login"), ("POST", "/api/wechat/verify"),
        ("POST", "/api/wechat/unbind"), ("POST", "/api/wechat/settings"),
    )),
    *(AiCapabilityCoverageEntry(method="POST", path=path, business_domain="在线商品", disposition="internal_only",
        reason="在线商品页面的多选范围预览与平台批量投递门面；AI 的 online_products_change/online_products_changes_apply 通过原生审批复用逐项领域队列、版本和冲突校验，不调用此页面门面。") for path in (
        "/api/online-products/change-batch/preview", "/api/online-products/change-batch",
    )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="发布仓库设置", disposition="internal_only", reason="用户在系统设置中同步远端发布仓库并指定物流商和运输模式；核价和发布能力使用已确认的配置，模型不推断仓库运输服务。") for method, path in (
        ("POST", "/api/yandex/warehouses/refresh"),
        ("POST", "/api/yandex/warehouses/logistics"),
    )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="核价设置", disposition="internal_only", reason="核价页面维护人工费用模板及实时汇率参考；业务核价仍通过 draft_pricing 能力完成。") for method, path in (
        ("GET", "/api/pricing/exchange-rates"),
        ("POST", "/api/pricing/templates/save"), ("POST", "/api/pricing/templates/default"), ("POST", "/api/pricing/templates/delete"),
    )),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/chat/history", business_domain="订单聊天", disposition="capability", capability_names=("order_buyer_chat_read",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/chat/send", business_domain="订单聊天", disposition="capability", capability_names=("order_buyer_chat_send",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/summary", business_domain="订单采购", disposition="capability", capability_names=("orders_summary_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/detail", business_domain="订单采购", disposition="capability", capability_names=("order_detail_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/address-note", business_domain="订单采购", disposition="capability", capability_names=("order_address_note_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/receiving-warehouse", business_domain="订单采购", disposition="capability", capability_names=("order_receiving_warehouse_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/fulfillment", business_domain="订单采购", disposition="capability", capability_names=("order_fulfillment_read",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/sync", business_domain="订单采购", disposition="capability", capability_names=("orders_sync",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/retry", business_domain="订单采购", disposition="capability", capability_names=("order_notification_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/acknowledge", business_domain="订单采购", disposition="capability", capability_names=("order_notification_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/select-source", business_domain="订单采购", disposition="capability", capability_names=("order_source_select",)),
    *(AiCapabilityCoverageEntry(method="POST", path=path, business_domain="订单采购", disposition="internal_only", reason="用户粘贴 1688 链接并点选核验规格绑定或切换销售 SKU 货源映射，须经人工核对实物与规格对应关系，不由模型自动改绑。") for path in (
        "/api/sku-sources/resolve-1688",
        "/api/sku-sources/query",
        "/api/sku-sources/bind",
        "/api/sku-sources/set-default",
        "/api/sku-sources/remove",
        "/api/sku-sources/link-master",
    )),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/record-purchase", business_domain="订单采购", disposition="capability", capability_names=("order_purchase_record",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/cancel-purchase", business_domain="订单采购", disposition="capability", capability_names=("order_purchase_cancel",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/purchase-sync", business_domain="订单采购", disposition="capability", capability_names=("order_purchase_sync",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/purchase-query", business_domain="订单采购", disposition="capability", capability_names=("order_purchase_query",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/address-note", business_domain="订单采购", disposition="capability", capability_names=("order_address_note_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/receiving-warehouse", business_domain="订单采购", disposition="capability", capability_names=("order_receiving_warehouse_update",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/alibaba-purchase", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_options",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/alibaba-purchase/batch", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_batch_options",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/alibaba-purchase/addresses", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_addresses",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/batch-preview", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_batch_preview",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/parse-address", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_parse_address",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/preview", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_preview",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/create", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_create",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/pay", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_pay",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/payment-status", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_payment_status",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/reconcile", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_reconcile",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/alibaba-purchase/cashier", business_domain="订单采购", disposition="capability", capability_names=("order_alibaba_cashier",)),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="1688 万邦直购", disposition="internal_only",
        reason="独立于 ERP 业务流程的个人 1688 万邦直购入口，涉及人工勾选多 SKU、核对个人收货地址及真实下单付款，不纳入 ERP 业务模型工具。") for method, path in (
        ("GET", "/api/alibaba/direct-purchase/state"),
        ("GET", "/api/alibaba/direct-purchase/addresses"),
        ("POST", "/api/alibaba/direct-purchase/resolve"),
        ("POST", "/api/alibaba/direct-purchase/parse-address"),
        ("POST", "/api/alibaba/direct-purchase/preview"),
        ("POST", "/api/alibaba/direct-purchase/create"),
        ("POST", "/api/alibaba/direct-purchase/reconcile"),
        ("POST", "/api/alibaba/direct-purchase/cashier"),
        ("POST", "/api/alibaba/direct-purchase/payment-status"),
        ("POST", "/api/alibaba/direct-purchase/pay"),
    )),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/fetch-label", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/label", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/parcels", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/plan", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/pause", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/resume", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/submit", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/sync", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/associate", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/retry", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/fulfillment/cancel", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_update",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/crossborderbus/services", business_domain="跨境履约", disposition="capability", capability_names=("order_fulfillment_services",)),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="物流设置", disposition="internal_only", reason="用户维护合作仓收货地址并确认承接规则；承接能力不能由模型猜测或自动确认。") for method, path in (
        ("POST", "/api/logistics/refresh"),
        ("POST", "/api/logistics/address"),
        ("POST", "/api/logistics/parse-address"),
    )),
    *(AiCapabilityCoverageEntry(method=method, path=path, business_domain="跨境履约", disposition="internal_only", reason="物流授权、全局承接规则维护及本地文件上传保留可信界面入口；单笔订单处理已通过领域工具开放。") for method, path in (
        ("POST", "/api/crossborderbus/authorize"),
        ("POST", "/api/crossborderbus/catalog"),
        ("POST", "/api/crossborderbus/save-rule"),
        ("POST", "/api/crossborderbus/delete-rule"),
        ("POST", "/api/orders/fulfillment/upload-label"),
    )),

    AiCapabilityCoverageEntry(method="GET", path="/api/system-settings/user-agents", business_domain="配置基础设施", disposition="excluded",
        reason="设置界面读取可编辑文档；当前账号的偏好正文由原生 Agent 指令加载。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/system-settings/user-agents", business_domain="配置基础设施", disposition="capability",
        capability_names=("user_agents_update",)),

    AiCapabilityCoverageEntry(method="POST", path="/api/system-settings", business_domain="配置基础设施", disposition="excluded", reason="系统设置界面保存同步间隔与水印，不作为模型业务工具。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/system-settings/watermark-preview", business_domain="配置基础设施", disposition="excluded", reason="仅人工设置界面预览当前水印表单，不保存配置、不上传图片。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/image-hosting/save", business_domain="配置基础设施", disposition="excluded", reason="仅人工设置界面管理托管目标、凭据与显式网络测试；发布工具通过统一交付服务使用已配置目标。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/image-hosting/default", business_domain="配置基础设施", disposition="excluded", reason="仅人工设置界面管理托管目标、凭据与显式网络测试；发布工具通过统一交付服务使用已配置目标。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/image-hosting/delete", business_domain="配置基础设施", disposition="excluded", reason="仅人工设置界面管理托管目标、凭据与显式网络测试；发布工具通过统一交付服务使用已配置目标。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/image-hosting/test", business_domain="配置基础设施", disposition="excluded", reason="仅人工设置界面管理托管目标、凭据与显式网络测试；发布工具通过统一交付服务使用已配置目标。"),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/ai/approval-mode", business_domain="授权基础设施",
        disposition="excluded", reason="仅受信 UI 可设置工具审批偏好，模型不能自行授权。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/collect-verification",
        business_domain="浏览器调试",
        disposition="excluded",
        reason="采集页面等待人工验证时的只读状态探测，由前端驱动，不作为 AI 工具轮询或验证码操作能力。",
    ),
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
        path="/online-products",
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
    AiCapabilityCoverageEntry(method="GET", path="/api/online-products", business_domain="在线商品", disposition="capability", capability_names=("online_products_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/online-products/yandex-shop-names", business_domain="在线商品", disposition="internal_only",
        reason="在线商品价格提示按已连接账号读取平台店名。"),
    AiCapabilityCoverageEntry(method="GET", path="/api/online-products/source-images", business_domain="在线商品", disposition="capability", capability_names=("online_products_read",)),
    AiCapabilityCoverageEntry(method="GET", path="/api/online-products/attributes", business_domain="在线商品", disposition="capability", capability_names=("online_products_attributes",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/sync", business_domain="在线商品", disposition="capability", capability_names=("online_products_sync",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/refresh-status", business_domain="在线商品", disposition="capability", capability_names=("online_products_refresh_status",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/refresh-sales", business_domain="在线商品", disposition="internal_only",
        reason="在线列表按当前可见范围刷新已过期的销售数据；AI 复用同步与商品读取入口。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/refresh-detail", business_domain="在线商品", disposition="capability", capability_names=("online_products_refresh_detail",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/change", business_domain="在线商品", disposition="capability", capability_names=("online_products_change",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/reconcile", business_domain="在线商品", disposition="capability", capability_names=("online_products_reconcile",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/close", business_domain="在线商品", disposition="capability", capability_names=("online_products_close",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/retry", business_domain="在线商品", disposition="capability", capability_names=("online_products_retry",)),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/ozon-export/preview", business_domain="在线商品", disposition="internal_only", reason="界面上传类目模板并人工检查导出资料；当前文件上传与下载流程仅面向可信界面，不开放 AI 工具。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/online-products/ozon-export/download", business_domain="在线商品", disposition="internal_only", reason="界面确认预览后生成本次 XLSX 文件；当前文件上传与下载流程仅面向可信界面，不开放 AI 工具。"),
    AiCapabilityCoverageEntry(method="GET", path="/api/orders/integrations", business_domain="授权基础设施", disposition="internal_only", reason="本机可信界面读取回调地址，含专用接入凭据，不向模型提供。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/orders/configure", business_domain="授权基础设施", disposition="internal_only", reason="用户配置公网回调接入地址。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/ozon/notifications", business_domain="订单通知", disposition="excluded", reason="Ozon 外部通知协议入口。"),
    AiCapabilityCoverageEntry(method="POST", path="/api/yandex/notifications", business_domain="订单通知", disposition="excluded", reason="Yandex 外部通知协议入口。"),
    # -------------------------------------------------- 平台查询（GET）


    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/orders",
        business_domain="平台商品与订单",
        disposition="capability",
        capability_names=("platform_orders_query",),
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
        path="/file",
        business_domain="静态资源",
        disposition="excluded",
        reason="本地图片静态文件服务，基础设施。",
    ),
    AiCapabilityCoverageEntry(
        method="GET",
        path="/licenses/THIRD-PARTY-NOTICES.txt",
        business_domain="静态资源",
        disposition="excluded",
        reason="开源许可证声明下载，不属于业务能力。",
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
        method="GET", path="/api/v1/ai-work/conversations/<conversation_id>/title",
        business_domain="AI 会话运输", disposition="excluded", reason="读取当前账号的对话展示标题。",
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
        path="/api/v1/ai-work/conversations/<conversation_id>/events",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="活动 conversation 官方事件订阅 SSE，chat/会话 transport。",
    ),
    # -------------------------------------------------- 主 Agent 运输（POST）
    AiCapabilityCoverageEntry(
        method="GET",
        path="/api/v1/ai-work/conversations/<conversation_id>/image-batch",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="图片批次只读进度投影，不启动或重试模型请求。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-chat/cancel",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="用户停止入口，向 Pydantic 原生取消令牌传递信号，不作为模型工具。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/v1/ai-chat/manage", business_domain="AI 会话运输",
        disposition="excluded", reason="由用户置顶、命名或删除自己的对话。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-chat/queue",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="用户处理已接收的排队输入，由对话入口领取或删除。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-chat/fork",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="用户从已完成回复创建会话分支，仅复制原生历史，不作为模型业务工具。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/ai-chat/runs",
        business_domain="AI 会话运输",
        disposition="excluded",
        reason="主 Agent 对话 SSE transport；能力经 global.chat ToolSet 暴露。",
    ),
    # -------------------------------------------------- 全局任务门面（POST）
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
    *(AiCapabilityCoverageEntry(
        method="POST",
        path=path,
        business_domain="商品与草稿",
        disposition="internal_only",
        reason="采集箱商品 JSON 文件导入导出由界面人工下载/上传文件完成，不经模型传输大体积图片数据。",
    ) for path in ("/api/products/export", "/api/products/import")),
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
        path="/api/duplicate-draft",
        business_domain="商品与草稿",
        disposition="capability",
        capability_names=("draft_duplicate",),
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
        path="/api/draft-pricing/preview",
        business_domain="定价与 UPC",
        disposition="capability",
        capability_names=("draft_pricing_preview",),
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/draft-pricing/apply", business_domain="定价与 UPC",
        disposition="capability", capability_names=("draft_pricing_apply",),
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/draft-pricing/restore", business_domain="定价与 UPC",
        disposition="internal_only", reason="核价页面返回时只读恢复有效预览；AI 沿用预览回执中的标识应用售价。",
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
        path="/api/v1/category-match",
        business_domain="类目与属性",
        disposition="capability",
        capability_names=("category_match",),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/category-match/batch",
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
        path="/api/claim-products",
        business_domain="采集与认领",
        disposition="capability",
        capability_names=("claim_products",),
    ),
    # 管理入口只把新增动作开放给 AI。编辑、移除及旧清单导入仍由页面执行。
    AiCapabilityCoverageEntry(
        method="POST", path="/api/collection-links/manage", business_domain="采集与认领", disposition="capability",
        capability_names=("collection_links_add",),
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/collection-links/run", business_domain="采集与认领", disposition="internal_only",
        reason="页面按选中链接的版本执行采集，沿用采集回执。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/collection-links/verify", business_domain="采集与认领", disposition="internal_only",
        reason="页面检查该链接绑定的原商品页人工验证状态。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/collect-receipt", business_domain="采集与认领", disposition="internal_only",
        reason="桌面采集界面查询本次请求回执，避免断线时重复采集。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/collect-resume", business_domain="采集与认领", disposition="internal_only",
        reason="URL 采集验证码完成后读取同一桌面上的原商品页，沿用原请求身份。",
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
        method="POST", path="/api/draft-images/plan-set", business_domain="图片",
        disposition="internal_only", reason="草稿图片工作台的整组素材规划，供用户调整套图方案。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/draft-images/generate-set", business_domain="图片",
        disposition="internal_only", reason="草稿工作台按方案异步生成和单页重做，生成结果须在工作台预览采用。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/draft-images/state", business_domain="图片",
        disposition="internal_only", reason="草稿工作台按草稿恢复既有图片领域批次的只读展示。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/draft-images/process", business_domain="图片",
        disposition="internal_only", reason="草稿工作台异步受理与幂等边界；模型继续使用 image_translate / image_edit 原生能力入口。",
    ),
    AiCapabilityCoverageEntry(
        method="POST", path="/api/image-processing/continue", business_domain="图片",
        disposition="internal_only", reason="桌面继续已验证的原图片工具回执，复用原请求范围与批次标识。",
    ),
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
        disposition="internal_only",
        reason=(
            "受信发布页面工作流：复用只读发布校验并持久化当前目标的预检状态；"
            "模型可见 product_publish_validate 本身保持无副作用。"
        ),
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/publish-payload-preview",
        business_domain="发布",
        disposition="internal_only",
        reason=(
            "受信发布页面的显式素材准备与确认入口；可能上传平台图片、写回 picture ID、"
            "保存预检并记录预览日志，不伪装成只读 Capability。"
        ),
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
        path="/api/publish-bus/reconcile",
        business_domain="发布",
        disposition="internal_only",
        reason=(
            "发布任务 UI 手动或查看触发的只读确认门面；会根据远端 task 终态释放"
            "持久发布锁，不允许 Agent 自主触发或把它当成发布重试。"
        ),
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
        path="/api/ai-config/canvas/account-login",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="Gemini Canvas 本机 Google 账号登录与状态核验，凭据流程不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/ai-config/canvas/account-logout",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="Gemini Canvas 本机 Google 账号退出与分区清理，凭据流程不包装为能力。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/ai-config/canvas/account-toggle",
        business_domain="配置基础设施",
        disposition="excluded",
        reason="Gemini Canvas 本机 Google 账号启用开关，配置管理基础设施。",
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
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/store-auth/currency",
        business_domain="授权基础设施",
        disposition="excluded",
        reason="店铺发布币种人工选择/填写，授权配置写入不包装为能力。",
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
        path="/api/v1/product-research/keywords/search",
        business_domain="选品调研",
        disposition="internal_only",
        reason="用户在商品关键词旁主动查询并选择热搜词；缓存和实际额度回执由界面展示。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/product-research/suppliers/search",
        business_domain="商品研究",
        disposition="internal_only",
        reason="首期货源核对工作台入口，需用户选择具体候选和找货方式；尚未开放 Agent 自动找货。",
    ),
    AiCapabilityCoverageEntry(
        method="POST",
        path="/api/v1/product-research/suppliers/import",
        business_domain="商品研究",
        disposition="internal_only",
        reason="人工核对货源后的确认入口；不得由 Agent 代替用户声明已经核对同款。",
    ),
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

    return frozenset(
        (entry.method, entry.path) for entry in AI_CAPABILITY_COVERAGE_MANIFEST
    )


__all__ = [
    "AI_CAPABILITY_COVERAGE_MANIFEST",
    "AiCapabilityCoverageEntry",
    "all_handled_endpoints",
    "coverage_manifest_endpoints",
]
