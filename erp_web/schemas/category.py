from __future__ import annotations

"""类目召回层的规范化数据形状。

旧类目搜索接口仍可在 HTTP 边界返回 ``id/path`` 等兼容字段；新的召回层内部只使用
``category_id/path_segments``，避免同一语义出现两套字段。
"""

from typing import Any, Literal, TypedDict


CategoryRetrievalMode = Literal["full_tree_local", "remote_discovery", "hybrid"]


class CategoryQueryVariant(TypedDict):
    query: str
    source: str
    weight: float


class CategoryCorpusInfo(TypedDict, total=False):
    corpus_hash: str
    taxonomy_version: str | None
    locale: str
    retrieved_at: str
    expires_at: str
    credential_scope_hash: str


class CategoryRetrievalRequest(TypedDict, total=False):
    platform: str
    site: str
    locale: str
    query: str
    product_type: str
    brand: str
    model: str
    modifiers: list[str]
    synonyms: list[str]
    key_attributes: dict[str, Any]
    limit: int


class CategoryCandidate(TypedDict, total=False):
    category_id: str
    name: str
    path_segments: list[str]
    retrieval_score: float
    retrieval_sources: list[str]
    matched_terms: list[str]
    publishable: bool
    platform: str
    site: str
    description_category_id: str
    type_id: str


class CategoryRetrievalCoverage(TypedDict, total=False):
    query_variant_count: int
    matched_query_variant_count: int
    candidate_count: int
    corpus_record_count: int
    top_score: float


class CategoryCandidateResult(TypedDict):
    candidates: list[CategoryCandidate]
    retrieval_mode: CategoryRetrievalMode
    corpus_info: CategoryCorpusInfo
    coverage: CategoryRetrievalCoverage
    warnings: list[dict[str, Any]]
    query_variants: list[CategoryQueryVariant]


class CategoryProviderPreflight(TypedDict):
    ok: bool
    platform: str
    site: str
    retrieval_mode: CategoryRetrievalMode
    corpus_info: CategoryCorpusInfo


__all__ = [
    "CategoryCandidate",
    "CategoryCandidateResult",
    "CategoryCorpusInfo",
    "CategoryProviderPreflight",
    "CategoryQueryVariant",
    "CategoryRetrievalCoverage",
    "CategoryRetrievalMode",
    "CategoryRetrievalRequest",
]
