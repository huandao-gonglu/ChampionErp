from __future__ import annotations

"""类目搜索与匹配的规范化数据形状。"""

from typing import Literal, TypedDict


class CategoryCorpusInfo(TypedDict, total=False):
    """平台搜索实现内部使用的缓存语料身份，不进入 AI 上下文。"""

    corpus_hash: str
    taxonomy_version: str | None
    locale: str
    retrieved_at: str
    expires_at: str
    credential_scope_hash: str


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


CategoryMatchStatus = Literal["completed", "unresolved", "failed"]
CategoryMatchMethod = Literal["tool_loop"]
CategoryConfidenceBand = Literal["high", "medium", "low"]


class CategoryMatchFailure(TypedDict, total=False):
    code: str
    message: str
    stage: str
    retryable: bool


class CategoryMatchDecision(TypedDict):
    method: CategoryMatchMethod
    confidence_band: CategoryConfidenceBand
    model_confidence: float
    decision_score: float
    abstained: bool
    evidence: list[str]
    search_count: int


class CategoryMatchTrace(TypedDict):
    conversation_id: str
    task_run_id: str


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
    "CategoryCandidate",
    "CategoryCorpusInfo",
    "CategorySearchResult",
    "CategoryConfidenceBand",
    "CategoryMatchDecision",
    "CategoryMatchFailure",
    "CategoryMatchMethod",
    "CategoryMatchResult",
    "CategoryMatchStatus",
    "CategoryMatchTrace",
]
