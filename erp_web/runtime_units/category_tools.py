"""类目匹配领域的只读 ToolSet。

平台和站点已经在任务入口绑定：
- 同时具备本地检索与树导航能力的平台（Ozon / Yandex）进入 ``hybrid`` 混合模式，
  同时暴露 ``search_categories`` 与 ``browse_categories``；
- 仅具备树导航能力的检索器进入 ``tree_navigation`` 模式；
- 仅具备关键字发现能力的平台（Mercado Libre）进入 ``keyword_search`` 模式。
工具参数均不接收 platform/site。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from erp_web.marketplaces.category_provider import CategoryNavigator, CategorySearcher
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.schemas.ai_tools import AiToolDefinition, AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryBrowseResult,
    CategoryCandidateLedger,
)
from erp_web.schemas.category_search_language import category_search_language
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)

RetrievalMode = Literal["keyword_search", "tree_navigation", "hybrid"]

_AI_CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["category_id", "name", "path_segments", "matched_keywords"],
    "properties": {
        "category_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "name": {"type": "string", "maxLength": 500},
        "name_zh": {"type": "string", "maxLength": 500},
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
        "path_zh": {"type": "string", "maxLength": 1000},
        "forbidden": {"type": "boolean"},
        "forbidden_reason": {"type": "string", "maxLength": 500},
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
        "name_zh": {"type": "string", "maxLength": 500},
        "level": {"type": "string", "enum": ["branch", "product_type"]},
        "depth": {"type": "integer", "minimum": 1, "maximum": 20},
        "parent_id": {"type": "string", "maxLength": 160},
        "path_segments": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 20,
        },
        "path_zh": {"type": "string", "maxLength": 1000},
        "child_count": {"type": "integer", "minimum": 0},
        "category_id": {"type": "string", "maxLength": 160},
    },
    "additionalProperties": False,
}

_MAX_NAVIGATION_CALLS = 4
_MAX_BROWSE_NODES = 500

CATEGORY_SEARCH_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="search_categories",
        version="6",
        description=(
            "先填写 product_type 确认实物通用名，再在 alternative_names / keywords 提交同一实物的当地别称及规范品名。"
            "程序自动合并 product_type、alternative_names 与 keywords；一个词的功能改写不算新别称，不能扩大为另一种商品。"
            "candidates 只包含此前未返回的候选全文，repeated_candidate_ids 引用此前已返回的类目。"
            "若首轮预召回或已有候选中已有合适类目，请直接提交最终结果；只有具体缺口才补查。"
            "按完整命中集合 RRF 融合去重，超过回执字节预算才分页。has_more=true 时保持全部搜索参数不变，以 next_offset 作为 offset 继续读取。"
            "total_candidate_count 是本组去重命中数，returned_candidate_count 是本页候选与历史引用总数，remaining_candidate_count 是后续页数量。"
            "query_candidate_counts 是各词完整取回的候选数；Yandex/Ozon 为本地树全部命中，美客多为远端预测接口返回量，不代表全平台类目总数。"
        ),
        input_schema={
            "type": "object",
            "required": ["product_identity", "product_type", "alternative_names"],
            "properties": {
                "product_identity": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": "先用中文提取原始标题/规格中的实物结构、佩戴或工作方式、用途；未知的结构不要编造。原始规格优先于译文和营销描述。后续所有品名必须保持此身份，不得换成结构不同的邻近商品。",
                },
                "alternative_names": {
                    "type": "array",
                    "maxItems": 6,
                    "description": "同一实物在当地市场的其他通用名称，通常 1 至 2 个不同叫法。不是给 product_type 换形容词，也不是新用途或另一种商品；确无别称时为空。程序会与 product_type、keywords 一起查询。",
                    "items": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                "product_type": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "description": "固定搜索语言的实物通用名，去掉人群、营销功能和型号。实际卖的是什么物件？程序一定查询此词。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "首轮为 0；续页保持全部搜索参数不变，填写上次 next_offset。",
                },
                "keywords": {
                    "type": "array",
                    "default": [],
                    "items": {"type": "string", "minLength": 1, "maxLength": 300},
                    "minItems": 0,
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
                },
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "keywords",
                "candidates",
                "repeated_candidate_ids",
                "errors",
                "truncated",
                "search_language",
                "remaining_candidate_count",
                "query_candidate_counts",
                "total_candidate_count",
                "returned_candidate_count",
                "has_more",
                "next_offset",
            ],
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "maxItems": CATEGORY_SEARCH_MAX_KEYWORDS_PER_CALL,
                },
                "candidates": {
                    "type": "array",
                    "items": _AI_CANDIDATE_SCHEMA,
                },
                "repeated_candidate_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 160},
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
                "has_more": {"type": "boolean"},
                "next_offset": {"type": ["integer", "null"], "minimum": 0},
                "total_candidate_count": {"type": "integer", "minimum": 0},
                "returned_candidate_count": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
                "search_language": {"type": "string"},
                "remaining_candidate_count": {"type": "integer", "minimum": 0},
                "query_candidate_counts": {
                    "type": "object",
                    "additionalProperties": {"type": "integer", "minimum": 0},
                },
            },
            "additionalProperties": False,
        },
        required_permission=CATEGORY_SEARCH_PERMISSION,
    ),
)


CATEGORY_NAVIGATION_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="browse_categories",
        version="2",
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
                "total_node_count",
                "truncated",
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
                    "maxItems": _MAX_BROWSE_NODES,
                },
                "total_node_count": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
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
    retrieval_mode: RetrievalMode
    initial_options: list[dict[str, Any]]


def _ai_browse_nodes(result: CategoryBrowseResult) -> tuple[list[dict[str, Any]], int, bool]:
    raw_nodes = result.get("nodes") or []
    total_count = len(raw_nodes)
    truncated = total_count > _MAX_BROWSE_NODES
    nodes: list[dict[str, Any]] = []
    for node in raw_nodes[:_MAX_BROWSE_NODES]:
        node_id = str(node.get("node_id") or "").strip()
        name = str(node.get("name") or "").strip()
        level = str(node.get("level") or "").strip()
        if not node_id or not name or level not in {"branch", "product_type"}:
            continue
        projected: dict[str, Any] = {
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
        name_zh = str(node.get("name_zh") or "").strip()[:500]
        if name_zh:
            projected["name_zh"] = name_zh
        path_zh = str(node.get("path_zh") or "").strip()[:1000]
        if path_zh:
            projected["path_zh"] = path_zh
        if level == "product_type":
            projected["category_id"] = str(
                node.get("category_id") or node_id
            ).strip()[:160]
        nodes.append(projected)
    return nodes, total_count, truncated


def _ai_browse_result(
    result: CategoryBrowseResult,
    *,
    navigation_calls_used: int,
    total_calls_used: int | None = None,
) -> dict[str, Any]:
    used = max(0, min(_MAX_NAVIGATION_CALLS, int(navigation_calls_used)))
    effective_used = max(used, min(_MAX_NAVIGATION_CALLS, int(total_calls_used))) if total_calls_used is not None else used
    nodes, total_node_count, truncated = _ai_browse_nodes(result)
    return {
        "parent_ids": [
            str(parent_id).strip()[:160]
            for parent_id in (result.get("parent_ids") or [])[:2]
            if str(parent_id).strip()
        ],
        "nodes": nodes,
        "total_node_count": total_node_count,
        "truncated": truncated,
        "navigation_calls_used": used,
        "navigation_calls_remaining": max(0, _MAX_NAVIGATION_CALLS - effective_used),
        "must_finalize": effective_used >= _MAX_NAVIGATION_CALLS,
    }


def _build_keyword_search_components(
    *,
    searcher: CategorySearcher,
    ledger: CategoryCandidateLedger,
    platform: str,
    site: str,
    calls_used: list[int] | None = None,
):
    language = category_search_language(platform, site)
    batch_search = CategoryKeywordBatchSearch(
        searcher=searcher,
        ledger=ledger,
        search_language=language,
    )
    definitions = tuple(
        replace(
            definition,
            description=(
                f"本次所有关键词固定使用 {language}，禁止中文或换语言试搜。"
                + definition.description
            ),
        )
        for definition in CATEGORY_SEARCH_TOOL_DEFINITIONS
    )

    def _collect_keywords(arguments: dict[str, Any]) -> list[str]:
        return [
            arguments["product_type"],
            *(arguments.get("alternative_names") or []),
            *(arguments.get("keywords") or []),
        ]

    def validate_match_search(arguments: dict[str, Any]) -> None:
        batch_search.validate_arguments({"keywords": _collect_keywords(arguments)})

    def execute_match_search(
        arguments: dict[str, Any],
        context: AiExecutionContext,
    ) -> dict[str, Any]:
        validate_match_search(arguments)
        if calls_used is not None:
            calls_used[0] += 1
        return batch_search.execute(
            {
                "keywords": _collect_keywords(arguments),
                "offset": arguments.get("offset", 0),
            },
            context,
        )

    return definitions, execute_match_search, validate_match_search


def _build_navigation_components(
    *,
    navigator: CategoryNavigator,
    ledger: CategoryCandidateLedger,
    calls_used: list[int] | None = None,
):
    roots = navigator.root_categories()
    root_options, _, _ = _ai_browse_nodes(roots)
    if not root_options:
        raise AiToolExecutionError(
            "CATEGORY_ROOTS_UNAVAILABLE",
            "类目树未返回可导航的顶层节点。",
            retryable=True,
        )
    available_parent_ids = {
        str(node.get("node_id") or "").strip()
        for node in roots.get("nodes") or []
        if node.get("level") == "branch" and str(node.get("node_id") or "").strip()
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
            result = navigator.browse_categories(parent_ids)
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
        if calls_used is not None:
            calls_used[0] += 1
        return _ai_browse_result(
            result,
            navigation_calls_used=ledger.navigation_count,
            total_calls_used=calls_used[0] if calls_used is not None else None,
        )

    return root_options, browse_executor


def build_category_match_toolset(
    *,
    searcher: CategorySearcher,
    ledger: CategoryCandidateLedger,
    platform: str,
    site: str,
) -> CategoryMatchToolBundle:
    """按绑定对象能力选择混合检索（hybrid）、纯树导航或关键字发现。"""

    is_navigator = isinstance(searcher, CategoryNavigator)
    is_searcher = callable(getattr(searcher, "search_categories", None))

    if is_navigator and is_searcher:
        ledger.retrieval_mode = "hybrid"
        calls_used = [0]
        root_options, browse_executor = _build_navigation_components(
            navigator=searcher,
            ledger=ledger,
            calls_used=calls_used,
        )
        search_defs, execute_match_search, validate_match_search = (
            _build_keyword_search_components(
                searcher=searcher,
                ledger=ledger,
                platform=platform,
                site=site,
                calls_used=calls_used,
            )
        )
        toolset = AiToolSet.bind(
            CATEGORY_SEARCH_TOOLSET_ID,
            (*search_defs, *CATEGORY_NAVIGATION_TOOL_DEFINITIONS),
            {
                "search_categories": deadline_aware_tool_executor(execute_match_search),
                "browse_categories": deadline_aware_tool_executor(browse_executor),
            },
            arguments_validators={"search_categories": validate_match_search},
        )
        return CategoryMatchToolBundle(
            toolset=toolset,
            retrieval_mode="hybrid",
            initial_options=root_options,
        )

    if is_navigator:
        ledger.retrieval_mode = "tree_navigation"
        root_options, browse_executor = _build_navigation_components(
            navigator=searcher,
            ledger=ledger,
        )
        toolset = AiToolSet.bind(
            CATEGORY_SEARCH_TOOLSET_ID,
            CATEGORY_NAVIGATION_TOOL_DEFINITIONS,
            {
                "browse_categories": deadline_aware_tool_executor(browse_executor),
            },
        )
        return CategoryMatchToolBundle(
            toolset=toolset,
            retrieval_mode="tree_navigation",
            initial_options=root_options,
        )

    ledger.retrieval_mode = "keyword_search"
    search_defs, execute_match_search, validate_match_search = (
        _build_keyword_search_components(
            searcher=searcher,
            ledger=ledger,
            platform=platform,
            site=site,
        )
    )
    toolset = AiToolSet.bind(
        CATEGORY_SEARCH_TOOLSET_ID,
        search_defs,
        {"search_categories": deadline_aware_tool_executor(execute_match_search)},
        arguments_validators={"search_categories": validate_match_search},
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
    "RetrievalMode",
    "build_category_match_toolset",
]
