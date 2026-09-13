"""检查草稿来源数据的能力。

为 AI Agent 提供访问原始采集数据的接口，用于验证营销声称是否有依据。
"""

from __future__ import annotations

import json
from typing import Annotated

from erp_web.runtime_units.product_capabilities import ProductCapabilityScope
from erp_web.schemas.source_inspect import (
    InspectSourceFactsRequest,
    InspectSourceFactsResult,
    SourceFactEntry,
    SourceFieldType,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


@ai_tool(
    name="inspect_source_facts",
    description=(
        "读取草稿关联商品的原始来源数据（1688/Amazon/eBay 等采集的数据）。"
        "返回所有原始键值对，包括规格、材质、包装、认证等信息。"
        "AI 可以用这些信息验证营销声称是否有依据。"
    ),
    permission="source.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def inspect_source_facts(
    request: InspectSourceFactsRequest,
    scope: Annotated[ProductCapabilityScope, Injected()],
) -> InspectSourceFactsResult:
    """查看草稿的原始采集数据。

    Args:
        request: 包含 draft_id 的请求对象

    Returns:
        完整的来源数据和元信息

    Raises:
        BusinessCapabilityError: 当草稿或商品不存在时抛出
    """

    draft_id = str(request.draft_id or "").strip()
    if not draft_id:
        raise BusinessCapabilityError(
            "DRAFT_ID_REQUIRED",
            "需要提供 draft_id。",
        )

    # 草稿详情已包含关联商品，统一通过存储公开接口读取和检查不存在错误。
    detail, error, _status = scope.products.load_draft_detail_from_index(draft_id)
    if error is not None:
        raise BusinessCapabilityError(
            str(error.get("error_code") or "DRAFT_NOT_FOUND"),
            str(error.get("error") or f"草稿 {draft_id} 不存在。"),
        )
    product_context = detail.get("productContext")
    product = product_context.get("raw") if isinstance(product_context, dict) else None
    if not detail.get("draft") or not isinstance(product, dict) or not product:
        raise BusinessCapabilityError(
            "DRAFT_CONTEXT_INVALID",
            f"草稿 {draft_id} 缺少关联的商品上下文。",
        )

    # 保留来源属性原文，分类仅作为查看提示，不据此认定商品参数或认证。
    source = product.get("source", {}) if isinstance(product.get("source"), dict) else {}
    source_attrs = (
        source.get("attributes", {})
        if isinstance(source.get("attributes"), dict)
        else {}
    )

    source_entries: list[SourceFactEntry] = []
    for key, value in source_attrs.items():
        text_value = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        field_type = _categorize_field(key, text_value)

        source_entries.append(
            SourceFactEntry(key=key, value=text_value, field_type=field_type)
        )

    # collect_logs 允许非结构化条目，只读取最近一条结构化日志的采集时间。
    collect_logs = (
        source.get("collect_logs", [])
        if isinstance(source.get("collect_logs"), list)
        else []
    )
    last_log = next((log for log in reversed(collect_logs) if isinstance(log, dict)), {})

    return InspectSourceFactsResult(
        draft_id=draft_id,
        source_attributes=source_entries,
        source_raw_dict=dict(source_attrs),
        source_platform=str(source.get("source_platform") or "").lower(),
        source_url=str(source.get("source_url") or ""),
        collected_at=str(last_log.get("finished_at") or ""),
        extraction_notes=(
            [f"成功提取 {len(source_attrs)} 个字段"] if len(source_attrs) > 10 else []
        ),
    )


def _categorize_field(key: str, value: str) -> SourceFieldType:
    """根据关键词粗略分类来源字段。

    Args:
        key: 字段键名
        value: 字段值

    Returns:
        分类标签：specification/material/package/certification/commercial/generic
    """
    key_lower = key.lower()
    value_lower = value.lower()

    # 规格参数。
    if any(x in key_lower for x in ["尺寸", "规格", "重量", "压力", "温度", "大小", "长度", "宽度", "高度"]):
        return "specification"

    # 材质。
    if any(x in key_lower or x in value_lower for x in ["材质", "材料", "rubber", "plastic", "metal", "abs", "steel", "iron", "aluminum"]):
        return "material"

    # 包装。
    if any(x in key_lower for x in ["包", "包装", "套装", "set", "pack", "件数"]):
        return "package"

    # 认证相关字段；字段名命中不代表认证已核实。
    if any(x in key_lower for x in ["认证", "certificate", "rohs", "iso", "ce", "fcc", "quality"]):
        return "certification"

    # 商业信息。
    if any(x in key_lower for x in ["平台", "地区", "产地", "origin", "shipping", "delivery"]):
        return "commercial"

    return "generic"


# 供应用组合根显式装配。
SOURCE_INSPECT_AI_CAPABILITIES = (inspect_source_facts,)


__all__ = [
    "inspect_source_facts",
    "SOURCE_INSPECT_AI_CAPABILITIES",
]
