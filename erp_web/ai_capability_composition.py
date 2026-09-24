"""应用级 Capability 组合根。

唯一 ``AiToolCatalog`` 实例在这里由各领域显式导出的 Capability tuple 组合而成；
主 Agent / Internal 三个不可变名称集合从同一个 Catalog 投影 ToolSet。
不扫描包、不依赖 import side effect 注册、不做运行时动态发现。
"""

from __future__ import annotations

from typing import Collection
from erp_web.runtime_units.conversation_fact_capabilities import (
    CONVERSATION_FACT_CAPABILITIES,
)

from erp_web.runtime_units.category_capabilities import CATEGORY_AI_CAPABILITIES
from erp_web.runtime_units.category_query_capabilities import (
    CATEGORY_QUERY_AI_CAPABILITIES,
)
from erp_web.runtime_units.collect_capabilities import COLLECTION_AI_CAPABILITIES
from erp_web.runtime_units.content_capabilities import CONTENT_AI_CAPABILITIES
from erp_web.runtime_units.draft_capabilities import DRAFT_QUERY_AI_CAPABILITIES
from erp_web.runtime_units.draft_edit_capabilities import DRAFT_EDIT_AI_CAPABILITIES
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
from erp_web.runtime_units.source_inspect_capability import (
    SOURCE_INSPECT_AI_CAPABILITIES,
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
SOURCE_INSPECT_CAPABILITIES = SOURCE_INSPECT_AI_CAPABILITIES
PUBLISH_ADMIN_CAPABILITIES = PUBLISH_ADMIN_AI_CAPABILITIES

ALL_AI_CAPABILITIES = (
    *CONVERSATION_FACT_CAPABILITIES,
    *PRODUCT_CAPABILITIES,
    *PRODUCT_WRITE_CAPABILITIES,
    *CATEGORY_CAPABILITIES,
    *CATEGORY_QUERY_CAPABILITIES,
    *MARKET_PREPARE_CAPABILITIES,
    *PUBLISH_CAPABILITIES,
    *DRAFT_QUERY_CAPABILITIES,
    *DRAFT_WRITE_CAPABILITIES,
    *DRAFT_EDIT_AI_CAPABILITIES,
    *CONTENT_CAPABILITIES,
    *IMAGE_CAPABILITIES,
    *PLATFORM_QUERY_CAPABILITIES,
    *PRICING_UPC_CAPABILITIES,
    *STORE_AUTH_CAPABILITIES,
    *LOGISTICS_CAPABILITIES,
    *COLLECTION_CAPABILITIES,
    *RESEARCH_CAPABILITIES,
    *SOURCE_INSPECT_CAPABILITIES,
    *PUBLISH_ADMIN_CAPABILITIES,
)

APPLICATION_CAPABILITY_CATALOG = AiToolCatalog.compile(ALL_AI_CAPABILITIES)

#: 主 Agent 的查询和纯计算能力。
GLOBAL_CHAT_CAPABILITIES = frozenset(
    {
        "conversation_facts_query",
        "drafts_query",
        "product_read",
        "draft_attributes_read",
        "inspect_source_facts",
        "product_publish_validate",
        "draft_read",
        "products_index_query",
        "mercadolibre_user_products_query",
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

#: 主 Agent 的 focused 写能力。
#:
#: 高频写入已迁移到 focused Capability：库存/售价以平台草稿为 owner
#: （draft_stock_update / draft_pricing_apply），商品主档走部分补丁
#: （product_profile_patch）。通用 product_save / draft_save 容易误选
#: owner 并膨胀上下文，已从常用 allowlist 移除，只保留为 internal。
_WRITE_CAPABILITIES = frozenset(
    {
        "drafts_query",
        "product_read",
        "draft_attributes_read",
        "draft_read",
        "product_profile_patch",
        "product_delete",
        "draft_stock_update",
        "draft_duplicate",
        "draft_sku_selection_update",
        "draft_sku_package_update",
        "draft_pricing_apply",
        "draft_delete",
        "product_attributes_update",
        "draft_sku_attributes_update",
        "product_images_prepare",
        "category_match",
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
        "mercadolibre_user_product_pause",
    }
)

#: 仅供其他 Capability/focused Agent 内部使用；与 主 Agent 互斥。
#: product_save / draft_save 是通用整对象保存，已被 focused write 取代，
#: 不再暴露给模型做任务规划；HTTP 门面走独立 facade，不经过 Capability。
INTERNAL_ONLY_CAPABILITIES = frozenset[str](
    {
        "product_save",
        "draft_save",
    }
)

GLOBAL_CHAT_TOOLSET_ID = "global.chat"


def application_capability_permissions() -> frozenset[str]:
    """从唯一 Catalog 机械推导全部 Capability 所需权限集合。"""

    return frozenset(
        tool.definition.required_permission
        for tool in APPLICATION_CAPABILITY_CATALOG.tools.values()
    )


def validate_capability_exposure() -> None:
    """校验 exposure 覆盖规则；架构测试直接调用。

    - 集合只能引用 Catalog 已编译能力；
    - 每个 Catalog Capability 至少进入 主 Agent 或 Internal 之一；
    - Internal 与 主 Agent 互斥；
    """

    catalog_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    for label, names in (
        ("GLOBAL_CHAT_CAPABILITIES", GLOBAL_CHAT_CAPABILITIES),
        ("_WRITE_CAPABILITIES", _WRITE_CAPABILITIES),
        ("INTERNAL_ONLY_CAPABILITIES", INTERNAL_ONLY_CAPABILITIES),
    ):
        unknown = sorted(names - catalog_names)
        if unknown:
            raise ValueError(f"{label} 引用了 Catalog 未收录能力：{', '.join(unknown)}")
    overlap = sorted(
        INTERNAL_ONLY_CAPABILITIES & (GLOBAL_CHAT_CAPABILITIES | _WRITE_CAPABILITIES)
    )
    if overlap:
        raise ValueError(f"Internal 能力不得同时进入 主 Agent：{', '.join(overlap)}")
    unexposed = sorted(
        catalog_names
        - (GLOBAL_CHAT_CAPABILITIES | _WRITE_CAPABILITIES | INTERNAL_ONLY_CAPABILITIES)
    )
    if unexposed:
        raise ValueError(
            f"Catalog 能力未进入任何 exposure 集合：{', '.join(unexposed)}"
        )


def bind_global_chat_toolset(
    *,
    scope: AiToolBindingScope,
    declared_permissions: Collection[str],
) -> AiToolSet:
    """为主 Agent 绑定查询、准备与原生审批业务工具。"""

    validate_capability_exposure()
    return APPLICATION_CAPABILITY_CATALOG.bind(
        toolset_id=GLOBAL_CHAT_TOOLSET_ID,
        allowed_tools=sorted(GLOBAL_CHAT_CAPABILITIES | _WRITE_CAPABILITIES),
        scope=scope,
        declared_permissions=declared_permissions,
        allow_write=True,
    )


__all__ = [
    "ALL_AI_CAPABILITIES",
    "APPLICATION_CAPABILITY_CATALOG",
    "CATEGORY_CAPABILITIES",
    "CATEGORY_QUERY_CAPABILITIES",
    "COLLECTION_CAPABILITIES",
    "CONTENT_CAPABILITIES",
    "DRAFT_QUERY_CAPABILITIES",
    "DRAFT_WRITE_CAPABILITIES",
    "GLOBAL_CHAT_CAPABILITIES",
    "GLOBAL_CHAT_TOOLSET_ID",
    "_WRITE_CAPABILITIES",
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
    "SOURCE_INSPECT_CAPABILITIES",
    "STORE_AUTH_CAPABILITIES",
    "application_capability_permissions",
    "bind_global_chat_toolset",
    "validate_capability_exposure",
]
