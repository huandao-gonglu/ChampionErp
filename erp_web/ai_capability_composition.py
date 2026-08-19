"""应用级 Capability 组合根。

唯一 ``AiToolCatalog`` 实例在这里由各领域显式导出的 Capability tuple 组合而成；
Direct / Task / Internal 三个不可变名称集合从同一个 Catalog 投影 ToolSet。
不扫描包、不依赖 import side effect 注册、不做运行时动态发现。
"""

from __future__ import annotations

from typing import Collection

from erp_web.runtime_units.attribute_fill_capabilities import (
    ATTRIBUTE_FILL_AI_CAPABILITIES,
)
from erp_web.runtime_units.category_capabilities import CATEGORY_AI_CAPABILITIES
from erp_web.runtime_units.category_query_capabilities import (
    CATEGORY_QUERY_AI_CAPABILITIES,
)
from erp_web.runtime_units.collect_capabilities import COLLECTION_AI_CAPABILITIES
from erp_web.runtime_units.content_capabilities import CONTENT_AI_CAPABILITIES
from erp_web.runtime_units.draft_capabilities import DRAFT_QUERY_AI_CAPABILITIES
from erp_web.runtime_units.image_capabilities import IMAGE_AI_CAPABILITIES
from erp_web.runtime_units.logistics_capabilities import LOGISTICS_AI_CAPABILITIES
from erp_web.runtime_units.market_prepare_capabilities import (
    MARKET_PREPARE_AI_CAPABILITIES,
)
from erp_web.runtime_units.platform_query_capabilities import (
    PLATFORM_QUERY_AI_CAPABILITIES,
)
from erp_web.runtime_units.pricing_upc_capabilities import (
    PRICING_UPC_AI_CAPABILITIES,
)
from erp_web.runtime_units.product_capabilities import PRODUCT_AI_CAPABILITIES
from erp_web.runtime_units.product_write_capabilities import (
    DRAFT_WRITE_AI_CAPABILITIES,
    PRODUCT_WRITE_AI_CAPABILITIES,
)
from erp_web.runtime_units.publish_capabilities import PUBLISH_AI_CAPABILITIES
from erp_web.runtime_units.publish_admin_capabilities import (
    PUBLISH_ADMIN_AI_CAPABILITIES,
)
from erp_web.runtime_units.research_capabilities import RESEARCH_AI_CAPABILITIES
from erp_web.runtime_units.store_auth_capabilities import (
    STORE_AUTH_AI_CAPABILITIES,
)
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_registry import AiToolSet


PRODUCT_CAPABILITIES = PRODUCT_AI_CAPABILITIES
PRODUCT_WRITE_CAPABILITIES = PRODUCT_WRITE_AI_CAPABILITIES
CATEGORY_CAPABILITIES = CATEGORY_AI_CAPABILITIES
CATEGORY_QUERY_CAPABILITIES = CATEGORY_QUERY_AI_CAPABILITIES
ATTRIBUTE_FILL_CAPABILITIES = ATTRIBUTE_FILL_AI_CAPABILITIES
MARKET_PREPARE_CAPABILITIES = MARKET_PREPARE_AI_CAPABILITIES
PUBLISH_CAPABILITIES = PUBLISH_AI_CAPABILITIES
DRAFT_QUERY_CAPABILITIES = DRAFT_QUERY_AI_CAPABILITIES
DRAFT_WRITE_CAPABILITIES = DRAFT_WRITE_AI_CAPABILITIES
CONTENT_CAPABILITIES = CONTENT_AI_CAPABILITIES
IMAGE_CAPABILITIES = IMAGE_AI_CAPABILITIES
PLATFORM_QUERY_CAPABILITIES = PLATFORM_QUERY_AI_CAPABILITIES
PRICING_UPC_CAPABILITIES = PRICING_UPC_AI_CAPABILITIES
STORE_AUTH_CAPABILITIES = STORE_AUTH_AI_CAPABILITIES
LOGISTICS_CAPABILITIES = LOGISTICS_AI_CAPABILITIES
COLLECTION_CAPABILITIES = COLLECTION_AI_CAPABILITIES
RESEARCH_CAPABILITIES = RESEARCH_AI_CAPABILITIES
PUBLISH_ADMIN_CAPABILITIES = PUBLISH_ADMIN_AI_CAPABILITIES

ALL_AI_CAPABILITIES = (
    *PRODUCT_CAPABILITIES,
    *PRODUCT_WRITE_CAPABILITIES,
    *CATEGORY_CAPABILITIES,
    *CATEGORY_QUERY_CAPABILITIES,
    *ATTRIBUTE_FILL_CAPABILITIES,
    *MARKET_PREPARE_CAPABILITIES,
    *PUBLISH_CAPABILITIES,
    *DRAFT_QUERY_CAPABILITIES,
    *DRAFT_WRITE_CAPABILITIES,
    *CONTENT_CAPABILITIES,
    *IMAGE_CAPABILITIES,
    *PLATFORM_QUERY_CAPABILITIES,
    *PRICING_UPC_CAPABILITIES,
    *STORE_AUTH_CAPABILITIES,
    *LOGISTICS_CAPABILITIES,
    *COLLECTION_CAPABILITIES,
    *RESEARCH_CAPABILITIES,
    *PUBLISH_ADMIN_CAPABILITIES,
)

APPLICATION_CAPABILITY_CATALOG = AiToolCatalog.compile(ALL_AI_CAPABILITIES)

#: 主 Agent 可直接调用的只读/纯计算能力。
GLOBAL_CHAT_DIRECT_CAPABILITIES = frozenset(
    {
        "drafts_query",
        "product_read",
        "product_publish_validate",
        "draft_read",
        "products_index_query",
        "platform_published_items_query",
        "platform_orders_query",
        "publish_logs_query",
        "publish_jobs_query",
        "publish_job_status_query",
        "category_search",
        "category_attributes_query",
        "category_attribute_values_query",
        "category_precheck",
        "pricing_calculate",
        "store_auth_checklist",
        "store_auth_check",
        "logistics_shipment_preview",
        "collect_1688_clean",
        "research_run_status_query",
    }
)

#: 可以作为 Global Task step 执行的能力。
GLOBAL_TASK_CAPABILITIES = frozenset(
    {
        "drafts_query",
        "product_read",
        "draft_read",
        "product_save",
        "product_delete",
        "draft_save",
        "draft_delete",
        "product_attributes_update",
        "product_images_prepare",
        "category_match",
        "product_attributes_fill",
        "draft_prepare_for_market",
        "product_publish_validate",
        "product_publish_request",
        "copy_generate",
        "copy_generate_batch",
        "image_prompts_generate",
        "text_translate",
        "image_pool_upload",
        "image_pool_save",
        "image_pool_action",
        "image_pool_sync_generated",
        "image_translate",
        "image_edit",
        "logistics_shipment_preview",
        "logistics_shipment_create",
        "upc_assign",
        "upc_import",
        "source_collect",
        "collect_batch",
        "collect_from_browser_tab",
        "collect_1688",
        "collect_1688_clean",
        "claim_products",
        "research_hot_products_search",
        "research_run_status_query",
        "product_publish_direct",
        "publish_real_confirm",
        "platform_item_close",
    }
)

#: 仅供其他 Capability/focused Agent 内部使用；与 Direct/Task 互斥。
INTERNAL_ONLY_CAPABILITIES = frozenset[str]()

GLOBAL_CHAT_DIRECT_TOOLSET_ID = "global.chat.direct"
GLOBAL_TASK_TOOLSET_ID = "global.task"


def application_capability_permissions() -> frozenset[str]:
    """从唯一 Catalog 机械推导全部 Capability 所需权限集合。"""

    return frozenset(
        tool.definition.required_permission
        for tool in APPLICATION_CAPABILITY_CATALOG.tools.values()
    )


def validate_capability_exposure() -> None:
    """校验 exposure 覆盖规则；架构测试直接调用。

    - 集合只能引用 Catalog 已编译能力；
    - 每个 Catalog Capability 至少进入 Direct、Task 或 Internal 之一；
    - Internal 与 Direct/Task 互斥；
    - Direct allowlist 不包含 write Capability。
    """

    catalog_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    for label, names in (
        ("GLOBAL_CHAT_DIRECT_CAPABILITIES", GLOBAL_CHAT_DIRECT_CAPABILITIES),
        ("GLOBAL_TASK_CAPABILITIES", GLOBAL_TASK_CAPABILITIES),
        ("INTERNAL_ONLY_CAPABILITIES", INTERNAL_ONLY_CAPABILITIES),
    ):
        unknown = sorted(names - catalog_names)
        if unknown:
            raise ValueError(
                f"{label} 引用了 Catalog 未收录能力：{', '.join(unknown)}"
            )
    overlap = sorted(
        INTERNAL_ONLY_CAPABILITIES
        & (GLOBAL_CHAT_DIRECT_CAPABILITIES | GLOBAL_TASK_CAPABILITIES)
    )
    if overlap:
        raise ValueError(
            f"Internal 能力不得同时进入 Direct/Task：{', '.join(overlap)}"
        )
    unexposed = sorted(
        catalog_names
        - (
            GLOBAL_CHAT_DIRECT_CAPABILITIES
            | GLOBAL_TASK_CAPABILITIES
            | INTERNAL_ONLY_CAPABILITIES
        )
    )
    if unexposed:
        raise ValueError(
            f"Catalog 能力未进入任何 exposure 集合：{', '.join(unexposed)}"
        )
    write_direct = sorted(
        name
        for name in GLOBAL_CHAT_DIRECT_CAPABILITIES
        if APPLICATION_CAPABILITY_CATALOG.tools[name].definition.side_effect
        == "write"
    )
    if write_direct:
        raise ValueError(
            f"direct allowlist 不能包含 write Capability：{', '.join(write_direct)}"
        )


def bind_global_chat_direct_toolset(
    *,
    scope: AiToolBindingScope,
    declared_permissions: Collection[str],
) -> AiToolSet:
    """为主 Agent 绑定只读 Direct ToolSet。"""

    validate_capability_exposure()
    return APPLICATION_CAPABILITY_CATALOG.bind(
        toolset_id=GLOBAL_CHAT_DIRECT_TOOLSET_ID,
        allowed_tools=sorted(GLOBAL_CHAT_DIRECT_CAPABILITIES),
        scope=scope,
        declared_permissions=declared_permissions,
        allow_write=False,
    )


def bind_global_task_toolset(
    *,
    scope: AiToolBindingScope,
    declared_permissions: Collection[str],
) -> AiToolSet:
    """为 Global Task Controller 绑定可写 Task ToolSet。"""

    validate_capability_exposure()
    return APPLICATION_CAPABILITY_CATALOG.bind(
        toolset_id=GLOBAL_TASK_TOOLSET_ID,
        allowed_tools=sorted(GLOBAL_TASK_CAPABILITIES),
        scope=scope,
        declared_permissions=declared_permissions,
        allow_write=True,
    )


__all__ = [
    "ALL_AI_CAPABILITIES",
    "APPLICATION_CAPABILITY_CATALOG",
    "ATTRIBUTE_FILL_CAPABILITIES",
    "CATEGORY_CAPABILITIES",
    "CATEGORY_QUERY_CAPABILITIES",
    "COLLECTION_CAPABILITIES",
    "CONTENT_CAPABILITIES",
    "DRAFT_QUERY_CAPABILITIES",
    "DRAFT_WRITE_CAPABILITIES",
    "GLOBAL_CHAT_DIRECT_CAPABILITIES",
    "GLOBAL_CHAT_DIRECT_TOOLSET_ID",
    "GLOBAL_TASK_CAPABILITIES",
    "GLOBAL_TASK_TOOLSET_ID",
    "IMAGE_CAPABILITIES",
    "INTERNAL_ONLY_CAPABILITIES",
    "LOGISTICS_CAPABILITIES",
    "MARKET_PREPARE_CAPABILITIES",
    "PLATFORM_QUERY_CAPABILITIES",
    "PRICING_UPC_CAPABILITIES",
    "PRODUCT_CAPABILITIES",
    "PRODUCT_WRITE_CAPABILITIES",
    "PUBLISH_ADMIN_CAPABILITIES",
    "PUBLISH_CAPABILITIES",
    "RESEARCH_CAPABILITIES",
    "STORE_AUTH_CAPABILITIES",
    "application_capability_permissions",
    "bind_global_chat_direct_toolset",
    "bind_global_task_toolset",
    "validate_capability_exposure",
]
