"""类目匹配领域的只读 ToolSet。

平台和站点已经在任务入口绑定：导航器逐层展开类目树，搜索器接收一组
关键字并合并候选。工具参数均不接收 platform/site。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from erp_web.marketplaces.category_provider import CategoryNavigator, CategorySearcher
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.schemas.ai_tools import AiToolDefinition, AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD,
    CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
    CATEGORY_SEARCH_MAX_CANDIDATES,
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryBrowseResult,
    CategoryCandidateLedger,
)
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)


_AI_CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["category_id", "name", "path_segments", "matched_keywords"],
    "properties": {
        "category_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "name": {"type": "string", "maxLength": 500},
        "matched_keywords": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
        },
        "path_segments": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 20,
        },
    },
    "additionalProperties": False,
}

_AI_TREE_NODE_SCHEMA = {
    "type": "object",
    "required": [
        "node_id",
        "name",
        "level",
        "depth",
        "parent_id",
        "path_segments",
        "child_count",
    ],
    "properties": {
        "node_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "name": {"type": "string", "minLength": 1, "maxLength": 500},
        "level": {"type": "string", "enum": ["branch", "product_type"]},
        "depth": {"type": "integer", "minimum": 1, "maximum": 20},
        "parent_id": {"type": "string", "maxLength": 160},
        "path_segments": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 20,
        },
        "child_count": {"type": "integer", "minimum": 0},
        "category_id": {"type": "string", "maxLength": 160},
    },
    "additionalProperties": False,
}

_MAX_NAVIGATION_CALLS = 4

CATEGORY_SEARCH_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="search_categories",
        version="3",
        description=(
            "先按商品实物规划主要相关品名，一次通过 keywords 批量提交，避免每次只更换功能修饰语。"
            "candidates 只包含此前未返回的候选全文，repeated_candidate_ids 引用此前已返回的类目。"
            "已有合适候选就提交最终结果；只有具体缺口才补查。"
            "truncated=true 表示还有未返回的新候选，必要时可缩小词组查询。"
        ),
        input_schema={
            "type": "object",
            "required": ["keywords"],
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 300},
                    "minItems": 1,
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["keywords", "candidates", "repeated_candidate_ids", "errors", "truncated"],
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
                },
                "candidates": {
                    "type": "array",
                    "items": _AI_CANDIDATE_SCHEMA,
                    "maxItems": CATEGORY_SEARCH_MAX_CANDIDATES,
                },
                "repeated_candidate_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL * CATEGORY_SEARCH_CANDIDATES_PER_KEYWORD,
                },
                "errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["keyword", "code", "message", "retryable"],
                        "properties": {
                            "keyword": {"type": "string", "maxLength": 300},
                            "code": {"type": "string", "maxLength": 160},
                            "message": {"type": "string", "maxLength": 500},
                            "retryable": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
                },
                "truncated": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        required_permission=CATEGORY_SEARCH_PERMISSION,
    ),
)


CATEGORY_NAVIGATION_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="browse_categories",
        version="1",
        description=(
            "展开真实类目树中的一到两个分支。只能传入首次输入或上次结果中"
            "level=branch 的 node_id；level=product_type 的 category_id 才能最终选择。"
            "没有合适叶子时可改选之前保留的分支，最多四次展开。"
        ),
        input_schema={
            "type": "object",
            "required": ["parent_ids"],
            "properties": {
                "parent_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    "minItems": 1,
                    "maxItems": 2,
                    "uniqueItems": True,
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "parent_ids",
                "nodes",
                "navigation_calls_used",
                "navigation_calls_remaining",
                "must_finalize",
            ],
            "properties": {
                "parent_ids": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 160},
                    "maxItems": 2,
                },
                "nodes": {
                    "type": "array",
                    "items": _AI_TREE_NODE_SCHEMA,
                    "maxItems": 500,
                },
                "navigation_calls_used": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4,
                },
                "navigation_calls_remaining": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4,
                },
                "must_finalize": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        required_permission=CATEGORY_SEARCH_PERMISSION,
    ),
)


@dataclass(frozen=True)
class CategoryMatchToolBundle:
    toolset: AiToolSet
    retrieval_mode: Literal["keyword_search", "tree_navigation"]
    initial_options: list[dict[str, Any]]


def _ai_browse_nodes(result: CategoryBrowseResult) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node in (result.get("nodes") or [])[:500]:
        node_id = str(node.get("node_id") or "").strip()
        name = str(node.get("name") or "").strip()
        level = str(node.get("level") or "").strip()
        if not node_id or not name or level not in {"branch", "product_type"}:
            continue
        projected = {
            "node_id": node_id[:160],
            "name": name[:500],
            "level": level,
            "depth": max(1, min(20, int(node.get("depth") or 1))),
            "parent_id": str(node.get("parent_id") or "").strip()[:160],
            "path_segments": [
                str(segment).strip()[:500]
                for segment in (node.get("path_segments") or [])[:20]
                if str(segment).strip()
            ],
            "child_count": max(0, int(node.get("child_count") or 0)),
        }
        if level == "product_type":
            projected["category_id"] = str(
                node.get("category_id") or node_id
            ).strip()[:160]
        nodes.append(projected)
    return nodes


def _ai_browse_result(
    result: CategoryBrowseResult,
    *,
    navigation_calls_used: int,
) -> dict[str, Any]:
    used = max(0, min(_MAX_NAVIGATION_CALLS, int(navigation_calls_used)))
    return {
        "parent_ids": [
            str(parent_id).strip()[:160]
            for parent_id in (result.get("parent_ids") or [])[:2]
            if str(parent_id).strip()
        ],
        "nodes": _ai_browse_nodes(result),
        "navigation_calls_used": used,
        "navigation_calls_remaining": _MAX_NAVIGATION_CALLS - used,
        "must_finalize": used >= _MAX_NAVIGATION_CALLS,
    }


def build_category_match_toolset(
    *,
    searcher: CategorySearcher,
    ledger: CategoryCandidateLedger,
) -> CategoryMatchToolBundle:
    """按绑定对象能力选择树导航或关键字发现，不暴露平台参数。"""

    if isinstance(searcher, CategoryNavigator):
        ledger.retrieval_mode = "tree_navigation"
        roots = searcher.root_categories()
        root_options = _ai_browse_nodes(roots)
        if not root_options:
            raise AiToolExecutionError(
                "CATEGORY_ROOTS_UNAVAILABLE",
                "类目树未返回可导航的顶层节点。",
                retryable=True,
            )
        available_parent_ids = {
            str(node.get("node_id") or "").strip()
            for node in roots.get("nodes") or []
            if node.get("level") == "branch"
            and str(node.get("node_id") or "").strip()
        }
        expanded_parent_ids: set[str] = set()

        def browse_executor(
            arguments: dict[str, Any],
            context: AiExecutionContext,
        ) -> dict[str, Any]:
            context.bounded_timeout_seconds()
            parent_ids = [str(item).strip() for item in arguments["parent_ids"]]
            if any(parent_id not in available_parent_ids for parent_id in parent_ids):
                raise AiToolExecutionError(
                    "CATEGORY_BRANCH_NOT_AVAILABLE",
                    "只能展开首次输入或之前工具结果中真实返回的 branch node_id。",
                )
            if any(parent_id in expanded_parent_ids for parent_id in parent_ids):
                raise AiToolExecutionError(
                    "CATEGORY_BRANCH_ALREADY_EXPANDED",
                    "类目分支已经展开，必须改选其他备选分支。",
                )
            ledger.record_attempt("tree:" + ",".join(parent_ids))
            try:
                result = searcher.browse_categories(parent_ids)
            except Exception as exc:
                ledger.record_error(exc)
                raise
            context.bounded_timeout_seconds()
            expanded_parent_ids.update(parent_ids)
            ledger.add_browse_result(result)
            available_parent_ids.update(
                str(node.get("node_id") or "").strip()
                for node in result.get("nodes") or []
                if node.get("level") == "branch"
                and str(node.get("node_id") or "").strip()
            )
            return _ai_browse_result(
                result,
                navigation_calls_used=ledger.navigation_count,
            )

        toolset = AiToolSet.bind(
            CATEGORY_SEARCH_TOOLSET_ID,
            CATEGORY_NAVIGATION_TOOL_DEFINITIONS,
            {
                "browse_categories": deadline_aware_tool_executor(
                    browse_executor
                ),
            },
        )
        return CategoryMatchToolBundle(
            toolset=toolset,
            retrieval_mode="tree_navigation",
            initial_options=root_options,
        )

    ledger.retrieval_mode = "keyword_search"

    batch_search = CategoryKeywordBatchSearch(searcher=searcher, ledger=ledger)
    toolset = AiToolSet.bind(
        CATEGORY_SEARCH_TOOLSET_ID,
        CATEGORY_SEARCH_TOOL_DEFINITIONS,
        {"search_categories": deadline_aware_tool_executor(batch_search.execute)},
    )
    return CategoryMatchToolBundle(
        toolset=toolset,
        retrieval_mode="keyword_search",
        initial_options=[],
    )


__all__ = [
    "CATEGORY_SEARCH_PERMISSION",
    "CATEGORY_SEARCH_TOOLSET_ID",
    "CATEGORY_NAVIGATION_TOOL_DEFINITIONS",
    "CATEGORY_SEARCH_TOOL_DEFINITIONS",
    "CategoryMatchToolBundle",
    "CategoryCandidateLedger",
    "build_category_match_toolset",
]
