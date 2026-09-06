"""类目属性枚举的类型化能力与显式 AI Tool Catalog。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Annotated, Any

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_attribute import (
    CATEGORY_ATTRIBUTE_VALUE_PERMISSION,
    CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
    CategoryAttributeValueCandidate,
    CategoryAttributeValueLedger,
    CategoryAttributeValueLookupResult,
    CategoryAttributeValueSearchRequest,
    CategoryAttributeValueSearchResult,
)
from erp_web.schemas.category_brand import (
    is_brand_attribute,
    is_no_brand_fact,
    no_brand_query_term,
)
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.ai_tool_registry import AiToolSet

from .category_store import fetch_category_attribute_values


CATEGORY_ATTRIBUTE_VALUE_SEARCH_TOOL = "category_attribute_values_search"
CATEGORY_ATTRIBUTE_FILL_TOOLS = (CATEGORY_ATTRIBUTE_VALUE_SEARCH_TOOL,)


@dataclass(frozen=True)
class CategoryAttributeToolScope:
    """模型不可见的平台、类目与 request-scoped 候选账本。"""

    platform: str
    category_id: str
    site: str
    ledger: CategoryAttributeValueLedger


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _platform_values(
    scope: CategoryAttributeToolScope,
    attribute_id: str,
    query: str,
    execution: AiExecutionContext,
) -> list[CategoryAttributeValueCandidate]:
    result = fetch_category_attribute_values(
        scope.platform,
        scope.category_id,
        attribute_id,
        site=scope.site,
        query=query,
        limit=20,
        timeout_seconds=execution.bounded_timeout_seconds(15),
    )
    values: list[CategoryAttributeValueCandidate] = []
    for item in (
        result.get("values") if isinstance(result.get("values"), list) else []
    ):
        if not isinstance(item, dict):
            continue
        value_id = _text(item.get("id") or item.get("dictionary_value_id"), 160)
        value = _text(item.get("value") or item.get("name"), 500)
        if value_id and value:
            values.append(
                CategoryAttributeValueCandidate(
                    dictionary_value_id=value_id,
                    value=value,
                )
            )
    return values[:20]


@ai_tool(
    name=CATEGORY_ATTRIBUTE_VALUE_SEARCH_TOOL,
    description=(
        "批量查询当前类目强制枚举属性的真实平台候选。每项使用目标市场语言的"
        "简短核心词搜索；品牌查询接受 Generic、无品牌、no brand 等语义别名并"
        "转换为平台官方检索词。最终只能选择本工具返回的 dictionary_value_id 和 value。"
    ),
    permission=CATEGORY_ATTRIBUTE_VALUE_PERMISSION,
    side_effect="none",
    version="5",
)
def search_category_attribute_values(
    request: CategoryAttributeValueSearchRequest,
    scope: Annotated[CategoryAttributeToolScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryAttributeValueSearchResult:
    """查询真实候选并更新本次调用安全终检使用的 request-scoped Ledger。"""

    results: list[CategoryAttributeValueLookupResult] = []
    for item in request.requests:
        execution.bounded_timeout_seconds()
        attribute_id = _text(item.attribute_id, 160)
        query = _text(item.query, 255)
        definition = scope.ledger.definition(attribute_id)
        error_code = ""
        error_message = ""
        if definition is None:
            error_code = "ATTRIBUTE_NOT_IN_CURRENT_CATEGORY"
            error_message = "只能查询 queryable_attribute_ids 列出的当前类目属性。"
        elif definition.get("value_mode") != "strict_enum":
            error_code = "ATTRIBUTE_VALUES_NOT_QUERYABLE"
            error_message = "该属性不是强制枚举，请直接按商品事实填写文本，dictionary_value_id 留空。"
        elif (
            scope.platform in {"ozon", "yandex"}
            and re.search(r"[\u3400-\u9fff]", query)
            and not is_brand_attribute(definition, platform=scope.platform)
        ):
            error_code = "ATTRIBUTE_QUERY_LANGUAGE_MISMATCH"
            examples = "、".join(str(value) for value in (definition.get("options") or [])[:6])
            error_message = "平台字典使用俄语，请把查询词翻译成俄语后批量重查。"
            if examples:
                error_message += "可参考平台原文：" + examples
        if error_code:
            results.append(CategoryAttributeValueLookupResult(
                attribute_id=attribute_id, query=query, values=[],
                error_code=error_code, error_message=error_message[:300],
            ))
            continue
        scope.ledger.record_attempt(attribute_id, query)
        platform_query = query
        if (
            query
            and is_brand_attribute(definition, platform=scope.platform)
            and is_no_brand_fact(query)
        ):
            platform_query = no_brand_query_term(scope.platform) or query
        try:
            values = _platform_values(
                scope,
                attribute_id,
                platform_query,
                execution,
            )
        except Exception:
            values = []
            error_code = "ATTRIBUTE_VALUE_LOOKUP_FAILED"
            error_message = "平台字典查询失败；可重试，仍失败时必填属性进入 need_review，可选属性跳过。"
            scope.ledger.record_failure(attribute_id)
        else:
            scope.ledger.failed_attribute_ids.discard(attribute_id)
            if not values:
                error_message = "没有匹配候选。换用平台语言的简短核心词；不要省略字典 ID 提交结果。"
        scope.ledger.add_values(
            attribute_id,
            [value.model_dump(mode="json") for value in values],
        )
        results.append(
            CategoryAttributeValueLookupResult(
                attribute_id=attribute_id,
                query=query,
                values=values,
                error_code=error_code,
                error_message=error_message,
            )
        )
    execution.bounded_timeout_seconds()
    return CategoryAttributeValueSearchResult(results=results)


CATEGORY_ATTRIBUTE_AI_TOOLS = (search_category_attribute_values,)
CATEGORY_ATTRIBUTE_TOOL_CATALOG = AiToolCatalog.compile(CATEGORY_ATTRIBUTE_AI_TOOLS)


def build_category_attribute_value_toolset(
    *,
    platform: str,
    category_record: dict[str, Any] | None,
    ledger: CategoryAttributeValueLedger,
) -> AiToolSet:
    """按显式场景 allowlist 绑定平台、类目和候选账本。"""

    record = category_record if isinstance(category_record, dict) else {}
    scope = CategoryAttributeToolScope(
        platform=_text(platform, 80).lower(),
        category_id=_text(record.get("category_id"), 160),
        site=_text(record.get("site"), 80),
        ledger=ledger,
    )
    return CATEGORY_ATTRIBUTE_TOOL_CATALOG.bind(
        toolset_id=CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
        allowed_tools=CATEGORY_ATTRIBUTE_FILL_TOOLS,
        scope=AiToolBindingScope.from_values(scope),
        declared_permissions={CATEGORY_ATTRIBUTE_VALUE_PERMISSION},
    )


__all__ = [
    "CATEGORY_ATTRIBUTE_AI_TOOLS",
    "CATEGORY_ATTRIBUTE_FILL_TOOLS",
    "CATEGORY_ATTRIBUTE_TOOL_CATALOG",
    "CATEGORY_ATTRIBUTE_VALUE_SEARCH_TOOL",
    "CategoryAttributeToolScope",
    "build_category_attribute_value_toolset",
    "search_category_attribute_values",
]
