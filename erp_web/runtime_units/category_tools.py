"""类目匹配领域的单一只读 ToolSet。

平台和站点已经在任务入口绑定到 ``CategorySearcher``。AI 只能提交关键字，
工具层既不接收也不判断 platform/site。
"""

from __future__ import annotations

from typing import Any

from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryCandidateLedger,
    CategorySearchResult,
)
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)


_AI_CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["category_id", "name", "path_segments"],
    "properties": {
        "category_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "name": {"type": "string", "maxLength": 500},
        "path_segments": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 20,
        },
    },
    "additionalProperties": False,
}

CATEGORY_SEARCH_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="search_categories",
        version="1",
        description=(
            "使用一个目标市场语言的简短商品关键字搜索当前平台类目。"
            "若结果不合适，请更换关键字再次搜索。"
        ),
        input_schema={
            "type": "object",
            "required": ["keyword"],
            "properties": {
                "keyword": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["keyword", "candidates"],
            "properties": {
                "keyword": {"type": "string", "maxLength": 300},
                "candidates": {
                    "type": "array",
                    "items": _AI_CANDIDATE_SCHEMA,
                    "maxItems": 8,
                },
            },
            "additionalProperties": False,
        },
        required_permission=CATEGORY_SEARCH_PERMISSION,
    ),
)


def _ai_result(result: CategorySearchResult) -> dict[str, Any]:
    """裁剪工具返回，避免把平台内部字段和缓存元数据送给模型。"""

    return {
        "keyword": str(result.get("keyword") or "").strip()[:300],
        "candidates": [
            {
                "category_id": str(candidate.get("category_id") or "")[:160],
                "name": str(candidate.get("name") or "")[:500],
                "path_segments": [
                    str(segment)[:500]
                    for segment in (candidate.get("path_segments") or [])[:20]
                ],
            }
            for candidate in (result.get("candidates") or [])[:8]
            if str(candidate.get("category_id") or "").strip()
        ],
    }


def build_category_search_toolset(
    *,
    searcher: CategorySearcher,
    ledger: CategoryCandidateLedger,
) -> AiToolSet:
    """把一个已绑定作用域的搜索器暴露为唯一 AI 工具。"""

    def search_executor(
        arguments: dict[str, Any],
        context: AiExecutionContext,
    ) -> dict[str, Any]:
        context.bounded_timeout_seconds()
        keyword = str(arguments["keyword"])
        ledger.record_attempt(keyword)
        try:
            result = searcher.search_categories(keyword)
        except Exception as exc:
            ledger.record_error(exc)
            raise
        context.bounded_timeout_seconds()
        ledger.add_result(result)
        return _ai_result(result)

    return AiToolSet.bind(
        CATEGORY_SEARCH_TOOLSET_ID,
        CATEGORY_SEARCH_TOOL_DEFINITIONS,
        {
            "search_categories": deadline_aware_tool_executor(search_executor),
        },
    )


__all__ = [
    "CATEGORY_SEARCH_PERMISSION",
    "CATEGORY_SEARCH_TOOLSET_ID",
    "CATEGORY_SEARCH_TOOL_DEFINITIONS",
    "CategoryCandidateLedger",
    "build_category_search_toolset",
]
