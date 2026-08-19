from __future__ import annotations

"""Endpoint Coverage Manifest 覆盖治理测试（规划 §6.3）。

要求：
1. 汇总全部 ``HANDLED_PATHS``（含 image POST 与 GET route units）；
2. 与 Manifest 比较，新 endpoint 未分类时失败；
3. Manifest 引用不存在的 Capability 时失败；
4. 同一 endpoint 的重复或冲突分类失败；
5. ``excluded`` / ``internal_only`` 没有理由时失败；
6. 最终要求是“零 unclassified”，不是“零 excluded”。
"""

import pytest
from pydantic import ValidationError

from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
from erp_web.ai_capability_coverage import (
    AI_CAPABILITY_COVERAGE_MANIFEST,
    AiCapabilityCoverageEntry,
    all_handled_endpoints,
    coverage_manifest_endpoints,
)


def test_every_handled_endpoint_is_classified_exactly_once() -> None:
    handled = all_handled_endpoints()
    manifest = coverage_manifest_endpoints()

    unclassified = sorted(handled - manifest)
    assert unclassified == [], f"新增 endpoint 未分类：{unclassified}"
    unknown = sorted(manifest - handled)
    assert unknown == [], f"Manifest 声明了不存在的 endpoint：{unknown}"

    seen: set[tuple[str, str]] = set()
    for entry in AI_CAPABILITY_COVERAGE_MANIFEST:
        key = (entry.method, entry.path)
        assert key not in seen, f"endpoint 重复分类：{key}"
        seen.add(key)
    assert len(seen) == len(AI_CAPABILITY_COVERAGE_MANIFEST)


def test_manifest_capability_names_exist_in_catalog() -> None:
    catalog_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    for entry in AI_CAPABILITY_COVERAGE_MANIFEST:
        if entry.disposition != "capability":
            continue
        assert entry.capability_names, (
            f"{entry.method} {entry.path} capability 分类必须列出能力"
        )
        for name in entry.capability_names:
            assert name in catalog_names, (
                f"{entry.method} {entry.path} 引用了 Catalog 未收录能力：{name}"
            )


def test_manifest_dispositions_are_consistent() -> None:
    for entry in AI_CAPABILITY_COVERAGE_MANIFEST:
        if entry.disposition == "capability":
            assert entry.capability_names
            assert entry.reason == ""
        else:
            assert entry.reason.strip(), (
                f"{entry.method} {entry.path} {entry.disposition} 必须给出理由"
            )
            assert entry.capability_names == (), (
                f"{entry.method} {entry.path} {entry.disposition} 不得引用能力"
            )


def test_entry_validation_rejects_misshaped_classifications() -> None:
    with pytest.raises(ValidationError):
        AiCapabilityCoverageEntry(
            method="POST",
            path="/api/new-endpoint",
            business_domain="测试",
            disposition="excluded",
            reason="",
        )
    with pytest.raises(ValidationError):
        AiCapabilityCoverageEntry(
            method="POST",
            path="/api/new-endpoint",
            business_domain="测试",
            disposition="capability",
        )
    with pytest.raises(ValidationError):
        AiCapabilityCoverageEntry(
            method="POST",
            path="/api/new-endpoint",
            business_domain="测试",
            disposition="internal_only",
            capability_names=("product_read",),
            reason="内部入口",
        )


def test_business_endpoints_are_capability_or_internal_only() -> None:
    """覆盖治理底线：业务 API 不允许被静默 excluded。

    规划 §7 明确的协议级例外：webhook/notification 接收与扩展原始
    payload 接收属于外部协议入口，允许 excluded，但必须显式登记。
    """

    infrastructure_domains = {
        "前端页面",
        "聚合基础设施",
        "配置基础设施",
        "授权基础设施",
        "静态资源",
        "浏览器调试",
        "AI 会话运输",
    }
    allowed_business_exclusions = {
        ("POST", "/api/mercadolibre/notifications"),
        ("POST", "/api/collect-extension-payload"),
    }
    for entry in AI_CAPABILITY_COVERAGE_MANIFEST:
        if entry.disposition != "excluded":
            continue
        if entry.business_domain in infrastructure_domains:
            continue
        key = (entry.method, entry.path)
        assert key in allowed_business_exclusions, (
            f"业务域入口不得 excluded：{entry.method} {entry.path}"
        )
