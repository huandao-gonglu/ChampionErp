# -*- coding: utf-8 -*-
from __future__ import annotations

"""跨平台类目候选召回。

本模块只负责确定性查询变体、候选召回、评分、去重和语料身份。AI rerank、业务阈值、
人工确认和发布决策不属于本层。
"""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Sequence

from erp_web.marketplaces.category_provider import (
    CategoryProvider,
    FullTreeCategoryProvider,
    RemoteDiscoveryCategoryProvider,
)
from erp_web.schemas.category import (
    CategoryCandidate,
    CategoryCandidateResult,
    CategoryCorpusInfo,
    CategoryProviderPreflight,
    CategoryQueryVariant,
    CategoryRetrievalRequest,
)

from .category_providers import require_category_provider


_MAX_QUERY_VARIANTS = 6
_MAX_CANDIDATES = 50
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "con",
        "de",
        "del",
        "для",
        "el",
        "en",
        "for",
        "la",
        "las",
        "los",
        "of",
        "para",
        "por",
        "the",
        "with",
        "без",
        "в",
        "для",
        "из",
        "на",
        "с",
        "со",
        "и",
        "商品",
        "产品",
    }
)
_COMMON_MODIFIERS = frozenset(
    {
        "best",
        "compact",
        "desktop",
        "electric",
        "mini",
        "new",
        "portable",
        "smart",
        "usb",
        "wireless",
        "настольный",
        "настольная",
        "настольное",
        "мини",
        "новый",
        "портативный",
        "портативная",
        "портативное",
        "беспроводной",
        "беспроводная",
        "compacto",
        "compacta",
        "eléctrico",
        "eléctrica",
        "inalámbrico",
        "inalámbrica",
        "nuevo",
        "nueva",
        "portátil",
        "便携",
        "无线",
        "智能",
        "桌面",
        "迷你",
    }
)
_RU_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ыми",
    "ими",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ый",
    "ий",
    "ой",
    "ов",
    "ев",
    "ам",
    "ям",
    "ах",
    "ях",
    "ы",
    "и",
    "а",
    "я",
    "у",
    "ю",
    "е",
    "о",
)
_DEFAULT_PLATFORM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "автомобильные шины": ("шины для легковых автомобилей",),
    "бутылка для воды": ("бутылка",),
    "настольная лампа": ("настольный светильник",),
    "ожерелье": ("колье", "бусы"),
    "llanta para auto": ("llantas de autos y camionetas",),
    "lámpara de mesa": ("veladores y lámparas de mesa",),
    "funda para celular": ("fundas y carcasas",),
    "vaso térmico": ("tazas y vasos térmicos",),
}
_HEAD_PREPOSITIONS = frozenset({"de", "del", "para", "для"})


class CategoryRetrievalError(RuntimeError):
    """类目召回失败；失败不会被降级成空候选。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
    ) -> None:
        self.code = str(code)
        self.stage = str(stage)
        self.retryable = bool(retryable)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "retryable": self.retryable,
        }


def normalize_category_text(value: Any) -> str:
    """执行 Unicode、大小写与标点归一化，保留跨语言字母和数字。"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(_TOKEN_PATTERN.findall(normalized))


def category_tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in normalize_category_text(value).split()
        if token and token not in _QUERY_STOPWORDS
    )


def _search_token(token: str) -> str:
    if not re.search(r"[а-яё]", token) or len(token) < 6:
        return token
    for suffix in _RU_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 5:
            return token[: -len(suffix)]
    return token


def _token_matches(query_token: str, candidate_token: str) -> bool:
    query_stem = _search_token(query_token)
    candidate_stem = _search_token(candidate_token)
    if query_stem == candidate_stem:
        return True
    shortest = min(len(query_stem), len(candidate_stem))
    if shortest < 5:
        return False
    if query_stem.startswith(candidate_stem) or candidate_stem.startswith(
        query_stem
    ):
        return True
    common_prefix = 0
    for query_char, candidate_char in zip(query_stem, candidate_stem):
        if query_char != candidate_char:
            break
        common_prefix += 1
    return common_prefix >= 5 and common_prefix / shortest >= 0.65


def _remove_terms(value: str, terms: Iterable[str]) -> str:
    excluded = {
        token
        for term in terms
        for token in category_tokens(term)
    }
    return " ".join(
        token for token in category_tokens(value) if token not in excluded
    )


def _query_from_attributes(product_type: str, attributes: Mapping[str, Any]) -> str:
    values = [
        normalize_category_text(value)
        for key, value in sorted(attributes.items(), key=lambda item: str(item[0]))
        if normalize_category_text(value)
    ]
    return " ".join([product_type, *values[:2]]).strip()


def _product_type_head(product_type: str) -> str:
    tokens = normalize_category_text(product_type).split()
    if len(tokens) <= 1:
        return product_type
    for index, token in enumerate(tokens):
        if token in _HEAD_PREPOSITIONS and index > 0:
            return tokens[index - 1]
    return tokens[-1]


def build_category_query_variants(
    request: CategoryRetrievalRequest | Mapping[str, Any],
) -> tuple[CategoryQueryVariant, ...]:
    """按从宽到窄顺序生成受控查询，重复语义只保留最高权重来源。"""

    query = normalize_category_text(request.get("query"))
    product_type = normalize_category_text(request.get("product_type"))
    explicit_synonyms = [
        normalize_category_text(item)
        for item in (
            request.get("synonyms")
            if isinstance(request.get("synonyms"), (list, tuple))
            else []
        )
        if normalize_category_text(item)
    ]
    synonyms = list(explicit_synonyms)
    synonyms.extend(_DEFAULT_PLATFORM_SYNONYMS.get(product_type, ()))
    explicit_modifiers = [
        str(item)
        for item in (
            request.get("modifiers")
            if isinstance(request.get("modifiers"), (list, tuple))
            else []
        )
    ]
    brand = str(request.get("brand") or "")
    model = str(request.get("model") or "")
    key_attributes = (
        request.get("key_attributes")
        if isinstance(request.get("key_attributes"), Mapping)
        else {}
    )
    if not query and not product_type and not synonyms:
        raise CategoryRetrievalError(
            "INPUT_INVALID",
            "类目召回至少需要 query、product_type 或 synonym。",
            stage="input",
        )

    candidates: list[tuple[str, str, float]] = []
    if product_type:
        product_type_head = _product_type_head(product_type)
        candidates.append((product_type_head, "head_noun", 1.0))
        if product_type_head != product_type:
            candidates.append((product_type, "product_type", 0.92))
            first_product_term = category_tokens(product_type)
            if first_product_term and first_product_term[0] != product_type_head:
                candidates.append(
                    (first_product_term[0], "product_type_term", 0.78)
                )
    candidates.extend((item, "synonym", 0.94) for item in synonyms)

    without_identity = _remove_terms(query, (brand, model))
    if without_identity and without_identity != query:
        candidates.append((without_identity, "without_brand_model", 0.9))

    modifier_terms = {*_COMMON_MODIFIERS}
    modifier_terms.update(
        token for item in explicit_modifiers for token in category_tokens(item)
    )
    broad_query = " ".join(
        token
        for token in category_tokens(without_identity or query)
        if token not in modifier_terms
    )
    if broad_query and broad_query not in {query, without_identity}:
        candidates.append((broad_query, "without_modifiers", 0.86))

    if query:
        content_tokens = category_tokens(broad_query or query)
        if content_tokens and not product_type:
            candidates.append((content_tokens[-1], "head_noun", 0.82))
        candidates.append((query, "specific_query", 0.74))

    attribute_query = _query_from_attributes(product_type, key_attributes)
    if product_type and attribute_query != product_type:
        candidates.append((attribute_query, "key_attributes", 0.68))

    variants: list[CategoryQueryVariant] = []
    seen: set[str] = set()
    for value, source, weight in candidates:
        normalized = normalize_category_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        variants.append(
            {
                "query": normalized,
                "source": source,
                "weight": weight,
            }
        )
        if len(variants) >= _MAX_QUERY_VARIANTS:
            break
    return tuple(variants)


def _path_segments(record: Mapping[str, Any]) -> list[str]:
    raw_path = record.get("path_original")
    if isinstance(raw_path, (list, tuple)):
        path = [str(item).strip() for item in raw_path if str(item).strip()]
        if path:
            return path
    path_text = str(record.get("category_path") or "").strip()
    if path_text:
        return [
            item.strip()
            for item in re.split(r"\s*/\s*|\s*>\s*", path_text)
            if item.strip()
        ]
    name = str(
        record.get("name_original")
        or record.get("name")
        or record.get("category_id")
        or ""
    ).strip()
    return [name] if name else []


def _matched_terms(
    query_terms: Sequence[str],
    candidate_terms: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        term
        for term in query_terms
        if any(_token_matches(term, candidate) for candidate in candidate_terms)
    )


def _candidate_from_record(
    record: Mapping[str, Any],
    *,
    platform: str,
    site: str,
    score: float,
    sources: Iterable[str],
    matched_terms: Iterable[str],
) -> CategoryCandidate:
    category_id = str(
        record.get("category_id") or record.get("type_id") or ""
    ).strip()
    path_segments = _path_segments(record)
    name = str(
        record.get("name_original")
        or record.get("name")
        or (path_segments[-1] if path_segments else category_id)
    ).strip()
    candidate: CategoryCandidate = {
        "category_id": category_id,
        "name": name,
        "path_segments": path_segments or [name or category_id],
        "retrieval_score": round(max(0.0, min(1.0, score)), 6),
        "retrieval_sources": list(dict.fromkeys(str(item) for item in sources)),
        "matched_terms": list(
            dict.fromkeys(str(item) for item in matched_terms if str(item))
        ),
        "publishable": bool(category_id) and not bool(record.get("disabled")),
        "platform": platform,
        "site": site,
    }
    description_category_id = str(
        record.get("description_category_id") or ""
    ).strip()
    type_id = str(record.get("type_id") or "").strip()
    if description_category_id:
        candidate["description_category_id"] = description_category_id
    if type_id:
        candidate["type_id"] = type_id
    return candidate


def _score_local_record(
    record: Mapping[str, Any],
    variants: Sequence[CategoryQueryVariant],
) -> tuple[
    float,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    path = _path_segments(record)
    leaf = str(record.get("name_original") or (path[-1] if path else ""))
    leaf_terms = category_tokens(leaf)
    path_terms = category_tokens(" ".join(path))
    normalized_leaf = normalize_category_text(leaf)
    normalized_path = normalize_category_text(" ".join(path))
    best_score = 0.0
    sources: list[str] = []
    matched: list[str] = []
    matched_queries: list[str] = []
    for variant in variants:
        query = variant["query"]
        query_terms = category_tokens(query)
        if not query_terms:
            continue
        leaf_matches = _matched_terms(query_terms, leaf_terms)
        path_matches = _matched_terms(query_terms, path_terms)
        if not path_matches:
            continue
        leaf_coverage = len(leaf_matches) / len(query_terms)
        path_coverage = len(path_matches) / len(query_terms)
        phrase_bonus = 0.0
        if query == normalized_leaf:
            phrase_bonus = 0.12
        elif query in normalized_leaf:
            phrase_bonus = 0.08
        elif query in normalized_path:
            phrase_bonus = 0.04
        weighted = float(variant["weight"]) * (
            0.54 * leaf_coverage
            + 0.24 * path_coverage
            + phrase_bonus
        )
        if weighted >= 0.18:
            best_score = max(best_score, weighted)
            sources.append(variant["source"])
            matched.extend(path_matches)
            matched_queries.append(query)
    if not sources:
        return 0.0, (), (), ()
    support_bonus = min(0.12, max(0, len(set(sources)) - 1) * 0.03)
    return (
        min(1.0, best_score + support_bonus),
        tuple(sources),
        tuple(matched),
        tuple(matched_queries),
    )


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _classify_provider_error(
    exc: Exception,
    *,
    stage: str,
) -> CategoryRetrievalError:
    if isinstance(exc, CategoryRetrievalError):
        return exc
    message = str(exc) or exc.__class__.__name__
    lowered = message.casefold()
    if any(
        marker in lowered
        for marker in (
            "请先填写",
            "credentials missing",
            "missing credential",
            "client id and api key",
        )
    ):
        code = "CATEGORY_CREDENTIALS_MISSING"
        retryable = False
    elif any(
        marker in lowered
        for marker in (
            " 401",
            " 403",
            "unauthorized",
            "forbidden",
            "invalid access token",
            "invalid api key",
        )
    ):
        code = "CATEGORY_AUTH_REJECTED"
        retryable = False
    elif " 429" in lowered or "rate limit" in lowered:
        code = "CATEGORY_RATE_LIMITED"
        retryable = True
    elif isinstance(exc, TimeoutError) or any(
        marker in lowered for marker in ("timed out", "timeout")
    ):
        code = "CATEGORY_PROVIDER_TIMEOUT"
        retryable = True
    elif " 400" in lowered or "bad request" in lowered:
        code = "CATEGORY_PROVIDER_BAD_REQUEST"
        retryable = False
    elif stage in {"preflight", "corpus"}:
        code = "CATEGORY_CORPUS_UNAVAILABLE"
        retryable = True
    else:
        code = "CATEGORY_PROVIDER_ERROR"
        retryable = True
    return CategoryRetrievalError(
        code,
        message,
        stage=stage,
        retryable=retryable,
    )


def _validated_preflight(
    provider: CategoryProvider,
    site: str,
) -> CategoryProviderPreflight:
    try:
        preflight = provider.preflight(site)
    except Exception as exc:
        raise _classify_provider_error(exc, stage="preflight") from exc
    if not preflight.get("ok"):
        raise CategoryRetrievalError(
            "CATEGORY_CORPUS_UNAVAILABLE",
            "类目 Provider preflight 未通过。",
            stage="preflight",
            retryable=True,
        )
    return preflight


def _limit(request: Mapping[str, Any]) -> int:
    raw_limit = request.get("limit", 20)
    if isinstance(raw_limit, bool):
        raise CategoryRetrievalError(
            "INPUT_INVALID",
            "limit 必须是整数。",
            stage="input",
        )
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise CategoryRetrievalError(
            "INPUT_INVALID",
            "limit 必须是整数。",
            stage="input",
        ) from exc
    return max(1, min(_MAX_CANDIDATES, parsed))


def _local_retrieve(
    provider: FullTreeCategoryProvider,
    *,
    platform: str,
    site: str,
    variants: Sequence[CategoryQueryVariant],
    limit: int,
    preflight: CategoryProviderPreflight,
) -> CategoryCandidateResult:
    try:
        records, corpus_info = provider.category_corpus(site)
    except Exception as exc:
        raise _classify_provider_error(exc, stage="corpus") from exc
    scored: list[CategoryCandidate] = []
    matched_queries: set[str] = set()
    for record in records:
        score, sources, matched_terms, record_queries = _score_local_record(
            record,
            variants,
        )
        if not score:
            continue
        candidate = _candidate_from_record(
            record,
            platform=platform,
            site=site,
            score=score,
            sources=sources,
            matched_terms=matched_terms,
        )
        if not candidate["category_id"]:
            continue
        matched_queries.update(record_queries)
        scored.append(candidate)
    scored.sort(
        key=lambda candidate: (
            -float(candidate["retrieval_score"]),
            " / ".join(candidate["path_segments"]).casefold(),
            candidate["category_id"],
        )
    )
    deduplicated: list[CategoryCandidate] = []
    seen_ids: set[str] = set()
    for candidate in scored:
        category_id = candidate["category_id"]
        if category_id in seen_ids:
            continue
        seen_ids.add(category_id)
        deduplicated.append(candidate)
        if len(deduplicated) >= limit:
            break
    return {
        "candidates": deduplicated,
        "retrieval_mode": "full_tree_local",
        "corpus_info": corpus_info or preflight["corpus_info"],
        "coverage": {
            "query_variant_count": len(variants),
            "matched_query_variant_count": len(matched_queries),
            "candidate_count": len(deduplicated),
            "corpus_record_count": len(records),
            "top_score": (
                float(deduplicated[0]["retrieval_score"])
                if deduplicated
                else 0.0
            ),
        },
        "warnings": [],
        "query_variants": list(variants),
    }


def _remote_retrieve(
    provider: RemoteDiscoveryCategoryProvider,
    *,
    platform: str,
    site: str,
    variants: Sequence[CategoryQueryVariant],
    limit: int,
    preflight: CategoryProviderPreflight,
) -> CategoryCandidateResult:
    merged: dict[str, dict[str, Any]] = {}
    matched_queries: set[str] = set()
    discovery_snapshot: list[dict[str, Any]] = []
    for variant in variants:
        try:
            discoveries = provider.discover(
                variant["query"],
                site=site,
                limit=min(8, max(5, limit)),
            )
        except Exception as exc:
            raise _classify_provider_error(exc, stage="discovery") from exc
        if discoveries:
            matched_queries.add(variant["query"])
        for fallback_rank, discovery in enumerate(discoveries):
            category_id = str(discovery.get("category_id") or "").strip()
            if not category_id:
                continue
            try:
                rank = max(
                    0,
                    int(discovery.get("provider_rank", fallback_rank)),
                )
            except (TypeError, ValueError):
                rank = fallback_rank
            contribution = min(
                0.95,
                0.95 * float(variant["weight"]) / (rank + 1),
            )
            state = merged.setdefault(
                category_id,
                {
                    "category_id": category_id,
                    "name": str(discovery.get("name") or category_id).strip(),
                    "score": 0.0,
                    "sources": [],
                    "matched_terms": [],
                },
            )
            state["score"] = 1.0 - (
                (1.0 - float(state["score"])) * (1.0 - contribution)
            )
            state["sources"].append(variant["source"])
            state["matched_terms"].extend(category_tokens(variant["query"]))
            discovery_snapshot.append(
                {
                    "query": variant["query"],
                    "category_id": category_id,
                    "name": state["name"],
                    "rank": rank,
                }
            )

    ranked = sorted(
        merged.values(),
        key=lambda state: (
            -float(state["score"]),
            str(state["category_id"]),
        ),
    )
    warnings: list[dict[str, Any]] = []
    candidates: list[CategoryCandidate] = []
    for state in ranked:
        if len(candidates) >= limit:
            break
        category_id = str(state["category_id"])
        try:
            detail = provider.detail(category_id, site=site)
        except Exception as exc:
            error = _classify_provider_error(exc, stage="detail")
            warnings.append(
                {
                    "code": error.code,
                    "category_id": category_id,
                    "message": str(error),
                }
            )
            continue
        candidate = _candidate_from_record(
            detail,
            platform=platform,
            site=site,
            score=float(state["score"]),
            sources=state["sources"],
            matched_terms=state["matched_terms"],
        )
        if candidate["category_id"]:
            candidates.append(candidate)

    if merged and not candidates:
        first_warning = warnings[0] if warnings else {}
        raise CategoryRetrievalError(
            str(first_warning.get("code") or "CATEGORY_PROVIDER_ERROR"),
            str(
                first_warning.get("message")
                or "远端 discovery 返回候选，但类目详情均不可用。"
            ),
            stage="detail",
            retryable=True,
        )

    now = datetime.now(timezone.utc).isoformat()
    corpus_info: CategoryCorpusInfo = {
        **preflight["corpus_info"],
        "corpus_hash": _stable_hash(discovery_snapshot),
        "retrieved_at": now,
        "expires_at": now,
    }
    return {
        "candidates": candidates,
        "retrieval_mode": "remote_discovery",
        "corpus_info": corpus_info,
        "coverage": {
            "query_variant_count": len(variants),
            "matched_query_variant_count": len(matched_queries),
            "candidate_count": len(candidates),
            "corpus_record_count": len(merged),
            "top_score": (
                float(candidates[0]["retrieval_score"])
                if candidates
                else 0.0
            ),
        },
        "warnings": warnings,
        "query_variants": list(variants),
    }


class CategoryCandidateRetriever:
    """稳定的跨平台类目召回入口。"""

    def __init__(
        self,
        provider_resolver: Callable[[str], CategoryProvider] = (
            require_category_provider
        ),
    ) -> None:
        self._provider_resolver = provider_resolver

    def retrieve(
        self,
        request: CategoryRetrievalRequest | Mapping[str, Any],
    ) -> CategoryCandidateResult:
        platform = str(request.get("platform") or "").strip().lower()
        if not platform:
            raise CategoryRetrievalError(
                "TARGET_REQUIRED",
                "类目召回缺少 target platform。",
                stage="input",
            )
        variants = build_category_query_variants(request)
        limit = _limit(request)
        try:
            provider = self._provider_resolver(platform)
        except Exception as exc:
            raise _classify_provider_error(exc, stage="preflight") from exc
        requested_site = str(request.get("site") or "").strip()
        try:
            site = provider.resolve_site(requested_site)
        except Exception as exc:
            raise _classify_provider_error(exc, stage="preflight") from exc
        preflight = _validated_preflight(provider, site)
        if isinstance(provider, FullTreeCategoryProvider):
            return _local_retrieve(
                provider,
                platform=platform,
                site=site,
                variants=variants,
                limit=limit,
                preflight=preflight,
            )
        if isinstance(provider, RemoteDiscoveryCategoryProvider):
            return _remote_retrieve(
                provider,
                platform=platform,
                site=site,
                variants=variants,
                limit=limit,
                preflight=preflight,
            )
        raise CategoryRetrievalError(
            "CATEGORY_PROVIDER_ERROR",
            f"{platform} 类目 Provider 未声明可用的召回能力。",
            stage="preflight",
        )


def retrieve_category_candidates(
    request: CategoryRetrievalRequest | Mapping[str, Any],
    *,
    provider: CategoryProvider | None = None,
) -> CategoryCandidateResult:
    """函数式入口；测试或离线评估可以显式注入 Provider。"""

    if provider is None:
        return CategoryCandidateRetriever().retrieve(request)
    return CategoryCandidateRetriever(lambda platform: provider).retrieve(request)


def retrieval_recall_at(
    candidates: Sequence[Mapping[str, Any]],
    expected_category_id: str,
    acceptable_ancestor_ids: Sequence[str] = (),
    *,
    k: int,
) -> float:
    """离线基线共用的单样本 Recall@K 计算。"""

    acceptable = {
        str(expected_category_id or "").strip(),
        *(str(item or "").strip() for item in acceptable_ancestor_ids),
    }
    acceptable.discard("")
    observed = {
        str(candidate.get("category_id") or "").strip()
        for candidate in candidates[: max(0, int(k))]
    }
    return 1.0 if acceptable & observed else 0.0


def aggregate_retrieval_baseline(
    evaluated_samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """聚合 Recall@5/20、零召回率及平台/语言/难度分层。"""

    sample_count = len(evaluated_samples)
    if not sample_count:
        return {
            "sample_count": 0,
            "recall_at_5": 0.0,
            "recall_at_20": 0.0,
            "zero_retrieval_rate": 0.0,
            "strata": {},
        }

    def average(values: Sequence[float]) -> float:
        return round(math.fsum(values) / len(values), 6) if values else 0.0

    strata: dict[str, list[Mapping[str, Any]]] = {}
    for sample in evaluated_samples:
        for key in ("target_platform", "source_language", "difficulty"):
            value = str(sample.get(key) or "unknown")
            strata.setdefault(f"{key}:{value}", []).append(sample)
    return {
        "sample_count": sample_count,
        "recall_at_5": average(
            [float(sample.get("recall_at_5") or 0.0) for sample in evaluated_samples]
        ),
        "recall_at_20": average(
            [
                float(sample.get("recall_at_20") or 0.0)
                for sample in evaluated_samples
            ]
        ),
        "zero_retrieval_rate": average(
            [
                1.0 if int(sample.get("candidate_count") or 0) == 0 else 0.0
                for sample in evaluated_samples
            ]
        ),
        "strata": {
            key: {
                "sample_count": len(samples),
                "recall_at_20": average(
                    [
                        float(sample.get("recall_at_20") or 0.0)
                        for sample in samples
                    ]
                ),
            }
            for key, samples in sorted(strata.items())
        },
    }


__all__ = [
    "CategoryCandidateRetriever",
    "CategoryRetrievalError",
    "aggregate_retrieval_baseline",
    "build_category_query_variants",
    "category_tokens",
    "normalize_category_text",
    "retrieval_recall_at",
    "retrieve_category_candidates",
]
