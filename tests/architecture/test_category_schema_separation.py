# -*- coding: utf-8 -*-
"""类目 Schema 分离的架构守卫（计划第 14 节）。

约束：
1. Product/PlatformDraft/DraftTargetSite canonical 模型不含规则副本字段；
2. 只有平台 Provider 实现（与授权测试）可导入平台类目 API runtime unit；
3. 类目匹配/属性填充/发布/Agent 工具/HTTP facade 只依赖 Catalog/Provider
   抽象或注入 Loader，不直接导入平台类目 API；
4. 发布代码不读取草稿 Schema 或商品级规则副本；
5. Agent/公共 schema 不含 raw、完整 values 或 platform_binding；
6. fingerprint 不依赖 fetched_at 等易变元数据；
7. 列表/索引/持久化层不调用 CategoryProvider；
8. 保存入口显式拒绝退役字段。
"""

from __future__ import annotations

import ast

from erp_web.schemas.category_definition import (
    CategoryAttributePage,
    CategoryAttributeSummary,
    CategoryAttributeValuePage,
    CategoryCacheState,
    CategoryDefinition,
    definition_fingerprint,
    definition_fingerprint_projection,
)
from erp_web.schemas.product import DraftTargetSite, PlatformDraft, Product

from .support import ROOT, imported_targets, parse_python, python_files


def _relative_posix(path) -> str:
    return path.relative_to(ROOT).as_posix()


#: 平台类目 API runtime unit（规则的唯一所有权属于 Provider 实现）。
PLATFORM_CATEGORY_API_UNITS = (
    "erp_web.runtime_units.ozon_category_api",
    "erp_web.runtime_units.yandex_category_api",
)

ALLOWED_CATEGORY_API_IMPORTERS = frozenset(
    {
        "erp_web/runtime_units/category_providers.py",
        # 授权在线校验使用类目树 summary 作为 live API 探针。
        "erp_web/runtime_units/store_credentials.py",
    }
)


def test_canonical_models_have_no_rule_copy_fields() -> None:
    for model, banned in (
        (Product, {"local_platform_categories"}),
        (PlatformDraft, {"category_attribute_schema"}),
        (DraftTargetSite, {"category_attribute_schema"}),
    ):
        fields = set(getattr(model, "__annotations__", {}))
        leaked = fields & banned
        assert not leaked, f"{model.__name__} 不得保留规则副本字段：{leaked}"


def test_only_providers_import_platform_category_api() -> None:
    offenders = [
        f"{_relative_posix(path)} -> {target}"
        for path, target in imported_targets(python_files("erp_web"))
        if any(target.startswith(unit) for unit in PLATFORM_CATEGORY_API_UNITS)
        and _relative_posix(path) not in ALLOWED_CATEGORY_API_IMPORTERS
    ]
    assert not offenders, (
        "平台类目 API 只允许 Provider 实现导入：\n" + "\n".join(offenders)
    )


BUSINESS_CONSUMER_FILES = (
    "erp_web/runtime_units/category_capabilities.py",
    "erp_web/runtime_units/attribute_fill_capabilities.py",
    "erp_web/runtime_units/category_attribute_ai_fill.py",
    "erp_web/runtime_units/category_query_capabilities.py",
    "erp_web/runtime_units/market_prepare_capabilities.py",
    "erp_web/runtime_units/publish_helpers.py",
    "erp_web/runtime_units/publish_validation.py",
    "erp_web/runtime_units/publish_ozon.py",
    "erp_web/runtime_units/publish_yandex.py",
    "erp_web/runtime_units/publish_mercadolibre.py",
    "erp_web/runtime_units/publish_adapter.py",
    "erp_web/runtime_units/publish_capabilities.py",
    "erp_web/runtime_units/publish_workflows.py",
    "erp_web/runtime_units/runtime_api.py",
    "erp_web/facades/category_facade.py",
    "erp_web/facades/category_match_facade.py",
)


def test_business_consumers_do_not_import_platform_category_api() -> None:
    offenders = [
        f"{_relative_posix(path)} -> {target}"
        for path, target in imported_targets(
            [ROOT / item for item in BUSINESS_CONSUMER_FILES]
        )
        if any(target.startswith(unit) for unit in PLATFORM_CATEGORY_API_UNITS)
    ]
    assert not offenders, (
        "业务消费者只能经 CategoryCatalog/Provider 抽象读取类目规则：\n"
        + "\n".join(offenders)
    )


def test_publish_code_does_not_read_persisted_rule_copies() -> None:
    for relative_path in (
        "erp_web/runtime_units/publish_helpers.py",
        "erp_web/runtime_units/publish_validation.py",
        "erp_web/runtime_units/publish_ozon.py",
        "erp_web/runtime_units/publish_yandex.py",
        "erp_web/runtime_units/publish_adapter.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for banned in ("category_attribute_schema", "local_platform_categories"):
            assert banned not in source, (
                f"{relative_path} 不得读取已退役的规则副本字段：{banned}"
            )


def test_public_category_schemas_are_bounded() -> None:
    # 属性摘要/属性页不得携带 raw、完整 values 或 platform_binding。
    for model in (CategoryAttributeSummary, CategoryAttributePage):
        fields = set(model.model_fields)
        leaked = fields & {"raw", "values", "platform_binding"}
        assert not leaked, f"{model.__name__} 公共视图不得包含：{leaked}"
    # 枚举值页只携带当前页 values，不得携带 raw/platform_binding。
    value_fields = set(CategoryAttributeValuePage.model_fields)
    assert not (value_fields & {"raw", "platform_binding"})
    # 属性页与枚举页必须携带分页字段。
    assert {"limit", "cursor", "next_cursor", "has_more"} <= set(
        CategoryAttributePage.model_fields
    )
    assert {"limit", "cursor", "next_cursor", "has_more"} <= set(
        CategoryAttributeValuePage.model_fields
    )


def test_fingerprint_excludes_volatile_metadata() -> None:
    definition = CategoryDefinition(
        platform="ozon",
        site="global",
        category_id="94765",
        fingerprint="preset",
        cache=CategoryCacheState(
            source="stale",
            stale=True,
            retrieved_at="2026-08-22T00:00:00+00:00",
            expires_at="2026-08-23T00:00:00+00:00",
            stale_until="2026-08-29T00:00:00+00:00",
        ),
    )
    projection_text = str(definition_fingerprint_projection(definition))
    for banned in (
        "fetched_at",
        "retrieved_at",
        "expires_at",
        "stale_until",
        "source",
    ):
        assert banned not in projection_text, (
            f"指纹投影不得包含易变元数据：{banned}"
        )
    assert definition_fingerprint(definition)


def test_persistence_and_index_layers_do_not_call_category_providers() -> None:
    layers = [
        path
        for path in python_files("erp_web/stores")
    ] + [ROOT / "erp_web/db.py"]
    offenders = [
        f"{_relative_posix(path)} -> {target}"
        for path, target in imported_targets(layers)
        if target.startswith(
            (
                "erp_web.runtime_units.category_catalog",
                "erp_web.runtime_units.category_providers",
            )
            + PLATFORM_CATEGORY_API_UNITS
        )
    ]
    assert not offenders, (
        "持久化/索引层不得调用 CategoryProvider（列表只读持久化摘要）：\n"
        + "\n".join(offenders)
    )


def test_save_boundaries_reject_retired_category_fields() -> None:
    source = (ROOT / "erp_web/stores/product_store.py").read_text(encoding="utf-8")
    save_product_body = source.split("def save_product(", 1)[1]
    assert "reject_retired_product_category_fields(data)" in save_product_body
    save_draft_body = source.split("def save_draft_detail(", 1)[1]
    assert "reject_retired_draft_schema_fields(draft_payload)" in save_draft_body


def test_retired_category_schema_helper_removed() -> None:
    support = (ROOT / "erp_web/runtime_units/market_capability_support.py").read_text(
        encoding="utf-8"
    )
    assert "def category_schema(" not in support
    providers = (ROOT / "erp_web/runtime_units/category_providers.py").read_text(
        encoding="utf-8"
    )
    assert "def detail(" not in providers
    assert "_yandex_shared_record" not in providers
