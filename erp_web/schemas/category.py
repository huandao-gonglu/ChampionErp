from __future__ import annotations

"""类目搜索与匹配的规范化数据形状。"""

from dataclasses import dataclass, field
from typing import Literal, TypedDict


CATEGORY_SEARCH_PERMISSION = "category.read"
CATEGORY_SEARCH_TOOLSET_ID = "category.search"


class CategoryCorpusInfo(TypedDict, total=False):
    """平台搜索实现内部使用的缓存语料身份，不进入 AI 上下文。"""

    corpus_hash: str
    taxonomy_version: str | None
    locale: str
    retrieved_at: str
    expires_at: str
    stale_until: str
    credential_scope_hash: str
    cache_source: Literal["remote_cache", "persistent_cache", "stale_cache"]
    stale: bool


class CategoryCandidate(TypedDict, total=False):
    category_id: str
    name: str
    path_segments: list[str]
    search_rank: int
    publishable: bool
    platform: str
    site: str
    description_category_id: str
    type_id: str


class CategorySearchResult(TypedDict):
    keyword: str
    candidates: list[CategoryCandidate]
    source: str


CategoryTreeNodeLevel = Literal["branch", "product_type"]


class CategoryTreeNode(TypedDict, total=False):
    """类目树导航节点；只有 ``product_type`` 可以作为最终发布类目。"""

    node_id: str
    name: str
    level: CategoryTreeNodeLevel
    depth: int
    parent_id: str
    path_segments: list[str]
    child_count: int
    category_id: str
    description_category_id: str
    type_id: str
    publishable: bool
    platform: str
    site: str


class CategoryBrowseResult(TypedDict):
    parent_ids: list[str]
    nodes: list[CategoryTreeNode]
    source: str


@dataclass
class CategoryCandidateLedger:
    """记录当前 Agent run 中工具真实返回的叶子候选与检索轨迹。"""

    _candidates: dict[str, CategoryCandidate] = field(default_factory=dict)
    searches: list[CategorySearchResult] = field(default_factory=list)
    browses: list[CategoryBrowseResult] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    retrieval_mode: Literal["keyword_search", "tree_navigation"] = "keyword_search"

    def record_attempt(self, keyword: str) -> None:
        self.attempts.append(str(keyword or "").strip()[:300])

    def record_error(self, exc: Exception) -> None:
        self.errors.append(exc)

    def add_result(self, result: CategorySearchResult) -> None:
        stored_result: CategorySearchResult = {
            "keyword": str(result.get("keyword") or "").strip()[:300],
            "candidates": [],
            "source": str(result.get("source") or "").strip()[:80],
        }
        for row in result.get("candidates") or []:
            category_id = str(row.get("category_id") or "").strip()
            if not category_id:
                continue
            candidate = dict(row)
            stored_result["candidates"].append(candidate)
            self._candidates.setdefault(category_id, candidate)
        self.searches.append(stored_result)

    def add_browse_result(self, result: CategoryBrowseResult) -> None:
        stored_result: CategoryBrowseResult = {
            "parent_ids": [
                str(parent_id).strip()[:160]
                for parent_id in result.get("parent_ids") or []
                if str(parent_id).strip()
            ],
            "nodes": [],
            "source": str(result.get("source") or "").strip()[:80],
        }
        for row in result.get("nodes") or []:
            node = dict(row)
            stored_result["nodes"].append(node)
            if node.get("level") != "product_type":
                continue
            category_id = str(node.get("category_id") or node.get("node_id") or "").strip()
            if not category_id:
                continue
            candidate: CategoryCandidate = {
                "category_id": category_id,
                "name": str(node.get("name") or category_id).strip()[:500],
                "path_segments": [
                    str(segment).strip()[:500]
                    for segment in (node.get("path_segments") or [])[:20]
                    if str(segment).strip()
                ],
                "search_rank": len(self._candidates),
                "publishable": bool(node.get("publishable", True)),
                "platform": str(node.get("platform") or "").strip(),
                "site": str(node.get("site") or "").strip(),
            }
            for field_name in ("description_category_id", "type_id"):
                value = str(node.get(field_name) or "").strip()
                if value:
                    candidate[field_name] = value
            self._candidates.setdefault(category_id, candidate)
        self.browses.append(stored_result)

    @property
    def search_count(self) -> int:
        return len(self.attempts)

    @property
    def successful_search_count(self) -> int:
        return len(self.searches) + len(self.browses)

    @property
    def navigation_count(self) -> int:
        return len(self.browses)

    @property
    def has_leaf_candidates(self) -> bool:
        return bool(self._candidates)

    @property
    def can_abstain(self) -> bool:
        if self.retrieval_mode == "tree_navigation":
            return self.has_leaf_candidates or self.navigation_count >= 4
        return self.search_count >= 3

    @property
    def last_error(self) -> Exception | None:
        return self.errors[-1] if self.errors else None

    @property
    def last_keyword(self) -> str:
        if self.searches:
            return self.searches[-1]["keyword"]
        if self.browses:
            nodes = self.browses[-1].get("nodes") or []
            if nodes:
                parent_path = [
                    str(segment).strip()
                    for segment in (nodes[0].get("path_segments") or [])[:-1]
                    if str(segment).strip()
                ]
                if parent_path:
                    return " > ".join(parent_path)
            return "tree:" + ",".join(self.browses[-1]["parent_ids"])
        return ""

    def get(self, category_id: str) -> CategoryCandidate | None:
        candidate = self._candidates.get(str(category_id or "").strip())
        return dict(candidate) if candidate is not None else None

    def candidates(self, *, limit: int = 24) -> list[CategoryCandidate]:
        return [
            dict(candidate)
            for candidate in list(self._candidates.values())[: max(1, int(limit))]
        ]


CategoryMatchStatus = Literal["completed", "unresolved", "failed"]
CategoryConfidenceBand = Literal["high", "medium", "low"]


class CategoryMatchFailure(TypedDict, total=False):
    code: str
    message: str
    stage: str
    retryable: bool


class CategoryMatchDecision(TypedDict):
    confidence_band: CategoryConfidenceBand
    model_confidence: float
    decision_score: float
    abstained: bool
    evidence: list[str]
    search_count: int


class CategoryMatchTrace(TypedDict, total=False):
    conversation_id: str
    task_run_id: str
    run_id: str
    trace_id: str


class CategoryMatchResult(TypedDict):
    ok: bool
    status: CategoryMatchStatus
    target: dict[str, str]
    selected_category_id: str | None
    query: str
    candidates: list[CategoryCandidate]
    decision: CategoryMatchDecision
    failure: CategoryMatchFailure | None
    trace: CategoryMatchTrace


__all__ = [
    "CATEGORY_SEARCH_PERMISSION",
    "CATEGORY_SEARCH_TOOLSET_ID",
    "CategoryCandidate",
    "CategoryCandidateLedger",
    "CategoryBrowseResult",
    "CategoryCorpusInfo",
    "CategorySearchResult",
    "CategoryTreeNode",
    "CategoryTreeNodeLevel",
    "CategoryConfidenceBand",
    "CategoryMatchDecision",
    "CategoryMatchFailure",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "CategoryMatchTrace",
]
