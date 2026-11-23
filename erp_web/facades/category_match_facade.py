"""``category.match`` 的稳定业务能力入口（1+N 多模态标准化 + 预召回 + 单轮精排）。

执行管线：
1. Phase 1（1× 跨平台共享）：提取分类优先的商品事实，并在模型支持识图时自动附带主图，
   一次结构化调用输出商品实物身份与各平台检索语言的规范品名；
2. 确定性预召回（Ozon / Yandex 本地 SQLite FTS5 + 同父兄弟叶扩展；Mercado Libre 并发发现 + 路径补全）；
3. Phase 2（Pydantic AI 单轮精排 + 原生工具回退）：把预召回候选注入首轮上下文，
   命中即 0 工具调用直接输出；存在缺口时自主调用 ``search_categories`` / ``browse_categories``；
4. 终检（复用校验期详情缓存，避免重复查详情）+ L2 词典零耗时补全候选中文名。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic_ai.messages import BinaryContent

from erp_web.context import get_context
from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
from erp_web.runtime_units.category_localization import enrich_category_candidates_l2
from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    create_category_searcher,
)
from erp_web.runtime_units.category_store import fetch_category_record
from erp_web.runtime_units.category_tools import (
    CategoryCandidateLedger,
    build_category_match_toolset,
)
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchFailure,
    CategoryMatchResult,
    CategoryMatchStatus,
    CategoryMatchTrace,
)
from erp_web.schemas.category_search_language import category_search_language
from erp_web.services.ai_agent_factory import AiAgentExecutionError
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.category_match_agent_service import (
    CATEGORY_MATCH_BUDGET_PROFILE,
    CATEGORY_MATCH_DEADLINE_SECONDS,
    CATEGORY_MATCH_USE_CASE_ID,
    CategoryMatchAgentRun,
    run_category_match_agent,
)
from erp_web.services.category_product_canonicalizer import (
    ProductCanonicalIdentity,
    build_product_facts,
    canonicalize_product_for_category_match,
    resolve_product_main_image,
)
from erp_web.services.forbidden_category_service import (
    forbidden_category_issue,
    forbidden_category_reason,
)

_PREFETCH_SEARCH_LIMIT = 28
_PREFETCH_SIBLING_SEED_LIMIT = 3
_PREFETCH_SIBLING_MAX_EXTRA = 10
_PREFETCH_PAYLOAD_MAX_CANDIDATES = 35

CategorySearcherFactory = Callable[..., CategorySearcher]


class CategoryMatchAgentService(Protocol):
    def __call__(
        self,
        payload: Mapping[str, Any],
        toolset: AiToolSet,
        ledger: CategoryCandidateLedger,
        *,
        timeout_seconds: float,
        candidate_detail_loader: Callable[..., dict[str, Any]],
    ) -> CategoryMatchAgentRun:
        ...


class CategoryMatchError(RuntimeError):
    """类目匹配编排错误，保留稳定错误码和阶段。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.stage = stage
        self.retryable = retryable
        super().__init__(message)

    def to_dict(self) -> CategoryMatchFailure:
        return {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "retryable": self.retryable,
        }


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def category_product_facts(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成首次模型请求；只保留能帮助识别商品的结构化事实。"""

    return build_product_facts(product, draft, target)


def _empty_decision() -> CategoryMatchDecision:
    return {
        "confidence_band": "low",
        "model_confidence": 0.0,
        "decision_score": 0.0,
        "abstained": True,
        "evidence": [],
        "search_count": 0,
    }


def failure_from_exception(exc: Exception, *, stage: str) -> CategoryMatchFailure:
    if isinstance(exc, CategoryMatchError):
        return exc.to_dict()
    cause = exc
    if isinstance(cause, CategorySearchError):
        return {
            "code": cause.code,
            "message": str(cause),
            "stage": stage,
            "retryable": cause.retryable,
        }
    if isinstance(cause, AiAgentExecutionError):
        return {
            "details": dict(cause.details),
            "code": cause.code,
            "message": str(cause),
            "stage": stage,
            "retryable": cause.retryable,
        }
    message = str(cause) or cause.__class__.__name__
    lowered = message.casefold()
    if isinstance(cause, TimeoutError) or "timeout" in lowered or "超时" in message:
        code = "TASK_DEADLINE_EXCEEDED"
        retryable = True
    elif any(
        marker in lowered
        for marker in ("api key", "credential", "请先填写", "未配置")
    ):
        code = "CATEGORY_CREDENTIALS_MISSING"
        retryable = False
    else:
        code = "MODEL_PROVIDER_ERROR" if stage == "model" else "CATEGORY_PROVIDER_ERROR"
        retryable = True
    return {
        "code": code,
        "message": message,
        "stage": stage,
        "retryable": retryable,
    }


_MAX_PUBLIC_CANDIDATES = 20


def _curate_public_candidates(
    ledger: CategoryCandidateLedger,
    selected_category_id: str | None,
    *,
    limit: int = _MAX_PUBLIC_CANDIDATES,
) -> list[CategoryCandidate]:
    """裁剪并排序返回给页面的候选：AI 首选置顶 → 同父级近义兄弟 → 合规高相关 → 禁售兜底。"""

    all_candidates = ledger.candidates()
    if not all_candidates:
        return []
    selected_id = str(selected_category_id or "").strip()
    selected_candidate = ledger.get(selected_id) if selected_id else None
    selected_parent: tuple[str, ...] = ()
    if selected_candidate is not None:
        segs = [str(s).strip() for s in (selected_candidate.get("path_segments") or []) if str(s).strip()]
        if len(segs) >= 2:
            selected_parent = tuple(segs[:-1])

    selected_bucket: list[CategoryCandidate] = []
    sibling_bucket: list[CategoryCandidate] = []
    compliant_bucket: list[CategoryCandidate] = []
    forbidden_bucket: list[CategoryCandidate] = []
    seen: set[str] = set()

    if selected_candidate is not None:
        cid = str(selected_candidate.get("category_id") or "").strip()
        if cid:
            selected_bucket.append(selected_candidate)
            seen.add(cid)

    for row in all_candidates:
        cid = str(row.get("category_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if row.get("forbidden"):
            forbidden_bucket.append(row)
            continue
        segs = [str(s).strip() for s in (row.get("path_segments") or []) if str(s).strip()]
        if selected_parent and len(segs) >= 2 and tuple(segs[:-1]) == selected_parent:
            sibling_bucket.append(row)
        else:
            compliant_bucket.append(row)

    ordered = selected_bucket + sibling_bucket + compliant_bucket + forbidden_bucket
    return ordered[: max(1, int(limit))]


def _result(
    *,
    ok: bool,
    status: CategoryMatchStatus,
    target: Mapping[str, Any],
    ledger: CategoryCandidateLedger,
    decision: CategoryMatchDecision,
    selected_category_id: str | None = None,
    failure: CategoryMatchFailure | None = None,
    trace: CategoryMatchTrace | None = None,
) -> CategoryMatchResult:
    platform = _text(target.get("platform"), 80).lower()
    public_candidates: list[CategoryCandidate] = []
    candidates = _curate_public_candidates(ledger, selected_category_id)
    for candidate in candidates:
        public_candidate: CategoryCandidate = {
            "category_id": str(candidate.get("category_id") or ""),
            "name": str(candidate.get("name") or ""),
            "path_segments": list(candidate.get("path_segments") or []),
        }
        if candidate.get("name_zh"):
            public_candidate["name_zh"] = str(candidate["name_zh"])
        if candidate.get("path_zh"):
            public_candidate["path_zh"] = str(candidate["path_zh"])
        elif candidate.get("path_segments_zh"):
            public_candidate["path_zh"] = " / ".join(candidate["path_segments_zh"])
        if "description_category_id" in candidate:
            public_candidate["description_category_id"] = str(
                candidate.get("description_category_id") or ""
            )
        if "type_id" in candidate:
            public_candidate["type_id"] = str(candidate.get("type_id") or "")
        if candidate.get("forbidden"):
            public_candidate["forbidden"] = True
            reason = str(candidate.get("forbidden_reason") or "").strip()
            if reason:
                public_candidate["forbidden_reason"] = reason
        public_candidates.append(public_candidate)

    if platform and public_candidates:
        enriched = enrich_category_candidates_l2(
            platform,
            [dict(item) for item in public_candidates],
        )
        public_candidates = [
            {
                **orig,
                **({"name_zh": row["name_zh"]} if row.get("name_zh") else {}),
                **({"path_zh": row["path_zh"]} if row.get("path_zh") else {}),
            }
            for orig, row in zip(public_candidates, enriched, strict=True)
        ]

    return {
        "ok": ok,
        "status": status,
        "target": {
            "platform": platform,
            "site": _text(target.get("site") or target.get("site_id"), 80),
        },
        "selected_category_id": selected_category_id,
        "query": ledger.last_keyword,
        "candidates": public_candidates,
        "decision": decision,
        "failure": failure,
        "trace": trace or {"task_run_id": ""},
    }


def _confidence_band(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def remaining_deadline_seconds(deadline_at: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise CategoryMatchError(
            "TASK_DEADLINE_EXCEEDED",
            "类目匹配总 deadline 已耗尽。",
            stage="model",
            retryable=True,
        )
    return remaining


def _validation_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> CategoryMatchError:
    return CategoryMatchError(
        code,
        message,
        stage="validation",
        retryable=retryable,
    )


def _forbidden_categories_map() -> dict[str, str]:
    settings = get_context().config.forbidden_categories()
    rows = settings.get("categories") if isinstance(settings, dict) else []
    return {
        str(item.get("category_id") or "").strip(): str(item.get("name") or "").strip()
        for item in (rows if isinstance(rows, list) else [])
        if isinstance(item, dict) and str(item.get("category_id") or "").strip()
    }


def _forbidden_issue_for_category(
    platform: str,
    category_id: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if platform != "yandex":
        return None
    categories = _forbidden_categories_map()
    if not categories:
        return None
    raw_ancestors = detail.get("ancestor_ids") if isinstance(detail, Mapping) else None
    ancestors = (
        tuple(str(item).strip() for item in raw_ancestors if str(item).strip())
        if isinstance(raw_ancestors, (list, tuple)) and raw_ancestors
        else get_context().category_catalog.cached_category_ancestor_ids(platform, category_id)
    )
    return forbidden_category_issue(platform, category_id, categories, ancestor_ids=ancestors)


def _validate_selected_category(
    *,
    selected_category_id: str,
    platform: str,
    site: str,
    ledger: CategoryCandidateLedger,
    detail_loader: Callable[..., dict[str, Any]],
    ensure_deadline: Callable[[], float],
    verified_details: Mapping[str, dict[str, Any]] | None = None,
) -> CategoryCandidate:
    candidate = ledger.get(selected_category_id)
    if candidate is None:
        raise _validation_error(
            "MODEL_SELECTED_UNKNOWN_CATEGORY",
            "模型选择了本次工具结果中从未出现过的 category_id。",
        )
    if str(candidate.get("platform") or "").lower() != platform:
        raise _validation_error("SITE_RULE_VIOLATION", "候选 platform 与当前目标不一致。")
    candidate_site = str(candidate.get("site") or "")
    if candidate_site and site and candidate_site.casefold() != site.casefold():
        raise _validation_error("SITE_RULE_VIOLATION", "候选 site 与当前目标不一致。")
    if not candidate.get("publishable"):
        raise _validation_error("CATEGORY_NOT_PUBLISHABLE", "所选类目当前不可发布。")
    if candidate.get("forbidden"):
        reason = str(candidate.get("forbidden_reason") or "在禁售名单中").strip()
        raise _validation_error(
            "CATEGORY_FORBIDDEN",
            f"所选类目 {selected_category_id} 属于禁售范围（{reason}），不能选择。",
        )

    cached_detail = (
        verified_details.get(selected_category_id)
        if isinstance(verified_details, Mapping)
        else None
    )
    if isinstance(cached_detail, dict):
        detail = cached_detail
    else:
        try:
            detail = detail_loader(
                platform,
                selected_category_id,
                site=site,
                include_attributes=False,
                timeout_seconds=ensure_deadline(),
            )
        except (TimeoutError, CategoryMatchError):
            raise
        except Exception as exc:
            raise _validation_error(
                "CATEGORY_NOT_FOUND",
                str(exc) or "类目详情不可读取。",
                retryable=True,
            ) from exc
    resolved_id = str(detail.get("category_id") or selected_category_id).strip()
    if resolved_id != selected_category_id or bool(detail.get("disabled")):
        raise _validation_error(
            "CATEGORY_NO_LONGER_AVAILABLE",
            "类目详情已变化或不再可发布。",
            retryable=True,
        )
    if issue := _forbidden_issue_for_category(platform, selected_category_id, detail):
        raise _validation_error("CATEGORY_FORBIDDEN", str(issue["message"]))
    detail_platform = str(detail.get("platform") or "").strip().lower()
    detail_site = str(detail.get("site") or "").strip()
    if detail_platform and detail_platform != platform:
        raise _validation_error("SITE_RULE_VIOLATION", "类目详情 platform 与目标不一致。")
    if detail_site and site and detail_site.casefold() != site.casefold():
        raise _validation_error("SITE_RULE_VIOLATION", "类目详情 site 与目标不一致。")
    if platform == "ozon":
        type_id = str(detail.get("type_id") or candidate.get("type_id") or "").strip()
        description_category_id = str(
            detail.get("description_category_id")
            or candidate.get("description_category_id")
            or ""
        ).strip()
        if not type_id or not description_category_id:
            raise _validation_error(
                "CATEGORY_NOT_PUBLISHABLE",
                "Ozon 类目缺少 type_id 与 description_category_id 配对。",
            )

    ensure_deadline()
    return candidate


# -- 确定性预召回 ------------------------------------------------------------


def _prefetch_candidates_for_target(
    *,
    searcher: CategorySearcher,
    ledger: CategoryCandidateLedger,
    platform: str,
    site: str,
    canonical_identity: ProductCanonicalIdentity | None,
    deadline_at: float,
) -> list[dict[str, Any]]:
    """根据标准化检索词执行确定性预召回（FTS5/API + 同父兄弟叶扩展）。"""

    if canonical_identity is None:
        return []
    if not callable(getattr(searcher, "search_categories", None)):
        return []
    try:
        language = category_search_language(platform, site)
    except Exception:
        return []
    terms = list(canonical_identity.search_terms_by_language.get(language) or [])
    if not terms:
        return []

    temp_ledger = CategoryCandidateLedger()
    batch = CategoryKeywordBatchSearch(
        searcher=searcher,
        ledger=temp_ledger,
        search_language=language,
        limit=_PREFETCH_SEARCH_LIMIT,
    )
    remaining = max(1.0, min(20.0, deadline_at - time.monotonic()))
    exec_ctx = AiExecutionContext.create(
        timeout_seconds=remaining,
        budget_profile="category_prefetch",
    )
    try:
        result = batch.execute({"keywords": terms, "offset": 0}, exec_ctx)
    except Exception:
        return []

    recalled: list[CategoryCandidate] = []
    seen_ids: set[str] = set()
    for row in result.get("candidates") or []:
        cid = str(row.get("category_id") or "").strip()
        if not cid or cid in seen_ids:
            continue
        full = temp_ledger.get(cid)
        if full is None:
            continue
        recalled.append(full)
        seen_ids.add(cid)

    # 对本地完整树（Ozon / Yandex），扩展前几名命中的同父级兄弟叶子类目，补齐近义叶子。
    sibling_fn = getattr(searcher, "sibling_leaves", None)
    if recalled and callable(sibling_fn):
        seed_ids = [
            str(item.get("category_id") or "")
            for item in recalled[:_PREFETCH_SIBLING_SEED_LIMIT]
            if item.get("category_id")
        ]
        if seed_ids:
            try:
                siblings = sibling_fn(seed_ids, limit=_PREFETCH_SIBLING_MAX_EXTRA)
            except Exception:
                siblings = []
            for sib in siblings:
                cid = str(sib.get("category_id") or "").strip()
                if not cid or cid in seen_ids:
                    continue
                recalled.append(sib)
                seen_ids.add(cid)

    if not recalled:
        return []

    ledger.record_prefetched(
        recalled,
        keywords=terms,
        source=f"{platform}_prefetched",
    )
    compact: list[dict[str, Any]] = []
    for item in recalled[:_PREFETCH_PAYLOAD_MAX_CANDIDATES]:
        entry: dict[str, Any] = {
            "category_id": str(item.get("category_id") or ""),
            "name": str(item.get("name") or ""),
            "path_segments": list(item.get("path_segments") or []),
        }
        if item.get("name_zh"):
            entry["name_zh"] = str(item["name_zh"])
        if item.get("path_zh"):
            entry["path_zh"] = str(item["path_zh"])
        if item.get("matched_keywords"):
            entry["matched_keywords"] = list(item["matched_keywords"])
        if item.get("forbidden"):
            entry["forbidden"] = True
            if item.get("forbidden_reason"):
                entry["forbidden_reason"] = str(item["forbidden_reason"])
        compact.append(entry)
    return compact


# -- 同步/流式共享阶段 ------------------------------------------------------


def prepare_category_match_input(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], CategoryMatchFailure | None]:
    """归一化目标与商品事实；输入不合法时返回失败描述。"""

    platform = _text(target.get("platform"), 80).lower()
    site = _text(target.get("site") or target.get("site_id"), 80)
    normalized_target = {
        "platform": platform,
        "site": site,
        "language": _text(target.get("language") or target.get("locale"), 80),
    }
    if not platform or not site:
        return normalized_target, {}, {
            "code": "TARGET_REQUIRED",
            "message": "类目匹配需要 platform 和 site。",
            "stage": "input",
            "retryable": False,
        }
    try:
        category_search_language(platform, site)
    except AiToolExecutionError as exc:
        return normalized_target, {}, {
            "code": exc.code,
            "message": str(exc),
            "stage": "input",
            "retryable": False,
        }
    facts = category_product_facts(product, draft, normalized_target)
    if not facts["source"]["title"] and not facts["target"]["title"]:
        return normalized_target, facts, {
            "code": "INPUT_INVALID",
            "message": "商品缺少可用于类目匹配的原文或目标语言标题。",
            "stage": "input",
            "retryable": False,
        }
    return normalized_target, facts, None


def setup_category_match_search(
    normalized_target: Mapping[str, Any],
    facts: Mapping[str, Any],
    ledger: CategoryCandidateLedger,
    deadline_at: float,
    *,
    searcher: CategorySearcher | None = None,
    searcher_factory: CategorySearcherFactory = create_category_searcher,
    canonical_identity: ProductCanonicalIdentity | None = None,
) -> tuple[dict[str, Any], AiToolSet]:
    """创建绑定式 searcher 与 ToolSet，执行确定性预召回并组装首轮 payload。"""

    platform = str(normalized_target.get("platform") or "")
    site = str(normalized_target.get("site") or "")
    scoped_searcher = searcher or searcher_factory(
        platform,
        site=site,
        limit=None,
        timeout_seconds=min(8, remaining_deadline_seconds(deadline_at)),
        deadline_at=deadline_at,
    )
    tool_bundle = build_category_match_toolset(
        searcher=scoped_searcher,
        ledger=ledger,
        platform=platform,
        site=site,
    )
    payload: dict[str, Any] = {
        "target": dict(normalized_target),
        "product": dict(facts),
    }
    if canonical_identity is not None:
        payload["canonical_identity"] = {
            "physical_form": canonical_identity.physical_form,
            "material_or_structure": canonical_identity.material_or_structure,
            "primary_use": canonical_identity.primary_use,
        }
        prefetched = _prefetch_candidates_for_target(
            searcher=scoped_searcher,
            ledger=ledger,
            platform=platform,
            site=site,
            canonical_identity=canonical_identity,
            deadline_at=deadline_at,
        )
        if prefetched:
            payload["prefetched_candidates"] = prefetched
    if tool_bundle.retrieval_mode in {"tree_navigation", "hybrid"}:
        payload["category_navigation"] = {
            "mode": tool_bundle.retrieval_mode,
            "root_nodes": tool_bundle.initial_options,
            "max_parent_ids_per_call": 2,
            "max_navigation_calls": 4,
        }
    return payload, tool_bundle.toolset


def failed_category_match(
    *,
    normalized_target: Mapping[str, Any],
    ledger: CategoryCandidateLedger,
    failure: CategoryMatchFailure,
) -> CategoryMatchResult:
    """Agent 启动前阶段（输入/检索装配）的类型化失败结果。"""

    return _result(
        ok=False,
        status="failed",
        target=normalized_target,
        ledger=ledger,
        decision=_empty_decision(),
        failure=failure,
    )


def finalize_category_match(
    *,
    normalized_target: Mapping[str, Any],
    ledger: CategoryCandidateLedger,
    deadline_at: float,
    detail_loader: Callable[..., dict[str, Any]] = fetch_category_record,
    agent_run: CategoryMatchAgentRun | None = None,
    agent_error: Exception | None = None,
) -> CategoryMatchResult:
    """Agent 运行结束（或运行失败）后的共享业务终检。"""

    platform = _text(normalized_target.get("platform"), 80).lower()
    site = _text(normalized_target.get("site"), 80)
    decision = _empty_decision()
    trace: CategoryMatchTrace = {"task_run_id": ""}

    if agent_error is not None:
        exc = agent_error
        if isinstance(exc, AiAgentExecutionError):
            trace = {
                "task_run_id": exc.task_run_id,
                "run_id": exc.run_id,
                "trace_id": exc.trace_id,
            }
        decision["search_count"] = ledger.search_count
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=failure_from_exception(exc, stage="model"),
            trace=trace,
        )

    assert agent_run is not None
    model_result = agent_run.output
    trace = {
        key: value
        for key in ("task_run_id", "run_id", "trace_id")
        if (value := _text(agent_run.trace.get(key), 200))
    }
    try:
        remaining_deadline_seconds(deadline_at)
    except Exception as exc:
        decision["search_count"] = ledger.search_count
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=failure_from_exception(exc, stage="model"),
            trace=trace,
        )

    decision["search_count"] = ledger.search_count
    if ledger.search_count == 0:
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "CATEGORY_SEARCH_REQUIRED",
                "message": "模型必须至少调用一次类目检索工具后才能结束任务。",
                "stage": "model",
                "retryable": False,
            },
            trace=trace,
        )
    if ledger.successful_search_count == 0 and ledger.last_error is not None:
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=failure_from_exception(ledger.last_error, stage="search"),
            trace=trace,
        )
    if not isinstance(model_result, Mapping):
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "MODEL_RESPONSE_SCHEMA_INVALID",
                "message": "类目匹配模型结果必须是对象。",
                "stage": "model",
                "retryable": False,
            },
            trace=trace,
        )

    selected_category_id = _text(model_result.get("selected_category_id"), 160)
    try:
        model_confidence = max(
            0.0,
            min(1.0, float(model_result.get("model_confidence") or 0)),
        )
    except (TypeError, ValueError):
        model_confidence = 0.0
    evidence = [
        _text(item, 300)
        for item in (
            model_result.get("evidence")
            if isinstance(model_result.get("evidence"), list)
            else []
        )
        if _text(item, 300)
    ][:8]
    abstained = bool(model_result.get("abstained")) or not selected_category_id
    decision.update(
        confidence_band=_confidence_band(model_confidence),
        model_confidence=model_confidence,
        decision_score=model_confidence,
        abstained=abstained,
        evidence=evidence,
    )
    if abstained:
        if not ledger.can_abstain:
            return _result(
                ok=False,
                status="failed",
                target=normalized_target,
                ledger=ledger,
                decision=decision,
                failure={
                    "code": "CATEGORY_SEARCH_INCOMPLETE",
                    "message": (
                        "树导航必须先到达商品类型，必要时回退改选分支后再 abstain。"
                        if ledger.retrieval_mode == "tree_navigation"
                        else "请先完成一次实际类目检索，再根据商品事实判断是否 abstain。"
                    ),
                    "stage": "model",
                    "retryable": False,
                },
                trace=trace,
            )
        abstain_reason = _text(model_result.get("abstain_reason"), 40)
        forbidden_candidates = [
            row for row in ledger.candidates() if row.get("forbidden")
        ]
        if abstain_reason == "forbidden_only" and forbidden_candidates:
            forbidden_labels = []
            for row in forbidden_candidates[:3]:
                cid = str(row.get("category_id") or "").strip()
                name = str(row.get("name") or "").strip()
                reason = str(row.get("forbidden_reason") or "").strip()
                if not reason and platform == "yandex":
                    reason = forbidden_category_reason(
                        platform,
                        cid,
                        _forbidden_categories_map(),
                        ancestor_ids=get_context().category_catalog.cached_category_ancestor_ids(platform, cid),
                    )
                label = (
                    f"{cid}（{name}：{reason}）"
                    if name and reason
                    else f"{cid}（{reason or name or '禁售类目'}）"
                )
                forbidden_labels.append(label)
            return _result(
                ok=True,
                status="unresolved",
                target=normalized_target,
                ledger=ledger,
                decision=decision,
                failure={
                    "code": "CATEGORY_FORBIDDEN_NO_ALTERNATIVE",
                    "message": f"最匹配的类目属于禁售范围，且无合规替代类目：{'；'.join(forbidden_labels)}",
                    "stage": "decision",
                    "retryable": False,
                },
                trace=trace,
            )
        return _result(
            ok=True,
            status="unresolved",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "ABSTAIN_NO_MATCH",
                "message": "已检索的候选不足以可靠确认类目，请核对商品实物与完整路径；这不代表平台没有该类目。",
                "stage": "decision",
                "retryable": False,
            },
            trace=trace,
        )

    try:
        _validate_selected_category(
            selected_category_id=selected_category_id,
            platform=platform,
            site=site,
            ledger=ledger,
            detail_loader=detail_loader,
            ensure_deadline=lambda: remaining_deadline_seconds(deadline_at),
            verified_details=agent_run.verified_details,
        )
    except Exception as exc:
        failure = failure_from_exception(exc, stage="validation")
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=failure,
            trace=trace,
        )

    return _result(
        ok=True,
        status="completed",
        target=normalized_target,
        ledger=ledger,
        decision=decision,
        selected_category_id=selected_category_id,
        trace=trace,
    )


def _canonicalize_for_languages(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    facts: Mapping[str, Any],
    languages: Sequence[str],
    *,
    timeout_seconds: float,
) -> tuple[ProductCanonicalIdentity | None, BinaryContent | None]:
    context = get_context()
    app_config = context.config.load_app_config()
    image_part = resolve_product_main_image(
        product,
        draft,
        app_dir=context.paths.app_dir,
        app_config=app_config,
    )
    identity = canonicalize_product_for_category_match(
        facts,
        languages,
        app_dir=context.paths.app_dir,
        app_config=app_config,
        image_content_part=image_part,
        timeout_seconds=min(45.0, max(10.0, timeout_seconds * 0.3)),
    )
    return identity, image_part


def match_category(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    searcher: CategorySearcher | None = None,
    searcher_factory: CategorySearcherFactory = create_category_searcher,
    agent_service: CategoryMatchAgentService = run_category_match_agent,
    detail_loader: Callable[..., dict[str, Any]] = fetch_category_record,
    canonical_identity: ProductCanonicalIdentity | None = None,
    image_content: BinaryContent | None = None,
    enable_canonicalizer: bool = True,
) -> CategoryMatchResult:
    """运行一次同步、支持 1+N 预召回与单轮精排的 ``category.match``。"""

    deadline_at = time.monotonic() + CATEGORY_MATCH_DEADLINE_SECONDS
    ledger = CategoryCandidateLedger()
    normalized_target, facts, failure = prepare_category_match_input(
        product, draft, target
    )
    if failure is not None:
        return failed_category_match(
            normalized_target=normalized_target,
            ledger=ledger,
            failure=failure,
        )

    resolved_identity = canonical_identity
    resolved_image = image_content
    if (
        enable_canonicalizer
        and resolved_identity is None
        and agent_service is run_category_match_agent
    ):
        try:
            lang = category_search_language(
                normalized_target["platform"],
                normalized_target["site"],
            )
            resolved_identity, resolved_image = _canonicalize_for_languages(
                product,
                draft,
                facts,
                [lang],
                timeout_seconds=remaining_deadline_seconds(deadline_at),
            )
        except Exception:
            resolved_identity = None
            resolved_image = None

    try:
        payload, toolset = setup_category_match_search(
            normalized_target,
            facts,
            ledger,
            deadline_at,
            searcher=searcher,
            searcher_factory=searcher_factory,
            canonical_identity=resolved_identity,
        )
    except Exception as exc:
        return failed_category_match(
            normalized_target=normalized_target,
            ledger=ledger,
            failure=failure_from_exception(exc, stage="searcher_setup"),
        )

    agent_run: CategoryMatchAgentRun | None = None
    try:
        remaining_seconds = remaining_deadline_seconds(deadline_at)
        if remaining_seconds < 1:
            raise CategoryMatchError(
                "TASK_DEADLINE_EXCEEDED",
                "类目匹配剩余 deadline 不足以启动模型调用。",
                stage="model",
                retryable=True,
            )
        agent_kwargs: dict[str, Any] = {
            "timeout_seconds": remaining_seconds,
            "candidate_detail_loader": lambda category_id, *, timeout_seconds: detail_loader(
                normalized_target["platform"],
                category_id,
                site=normalized_target["site"],
                include_attributes=False,
                timeout_seconds=min(timeout_seconds, remaining_deadline_seconds(deadline_at)),
            ),
        }
        if agent_service is run_category_match_agent:
            agent_kwargs["forbidden_checker"] = lambda category_id, detail: _forbidden_issue_for_category(
                normalized_target["platform"], category_id, detail,
            )
            if resolved_image is not None:
                agent_kwargs["image_content"] = resolved_image
        agent_run = agent_service(
            payload,
            toolset,
            ledger,
            **agent_kwargs,
        )
    except Exception as exc:
        return finalize_category_match(
            normalized_target=normalized_target,
            ledger=ledger,
            deadline_at=deadline_at,
            detail_loader=detail_loader,
            agent_run=None,
            agent_error=exc,
        )
    return finalize_category_match(
        normalized_target=normalized_target,
        ledger=ledger,
        deadline_at=deadline_at,
        detail_loader=detail_loader,
        agent_run=agent_run,
    )


def match_categories_batch(
    subjects: Sequence[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]],
    *,
    searcher_factory: CategorySearcherFactory = create_category_searcher,
    agent_service: CategoryMatchAgentService = run_category_match_agent,
    detail_loader: Callable[..., dict[str, Any]] = fetch_category_record,
) -> list[CategoryMatchResult]:
    """1+N 批量类目匹配：1 次跨平台多模态标准化 + N 平台并行预召回与单轮精排。"""

    if not subjects:
        return []
    if len(subjects) == 1:
        product, draft, target = subjects[0]
        return [
            match_category(
                product,
                draft,
                target,
                searcher_factory=searcher_factory,
                agent_service=agent_service,
                detail_loader=detail_loader,
            )
        ]

    # 收集本批所有有效目标涉及的检索语言（通常 1-2 种，如 ["ru", "es"]）
    languages: list[str] = []
    first_valid: tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]] | None = None
    for product, draft, target in subjects:
        normalized_target, facts, failure = prepare_category_match_input(product, draft, target)
        if failure is not None:
            continue
        if first_valid is None:
            first_valid = (product, draft, facts)
        try:
            lang = category_search_language(
                normalized_target["platform"],
                normalized_target["site"],
            )
            if lang not in languages:
                languages.append(lang)
        except Exception:
            continue

    shared_identity: ProductCanonicalIdentity | None = None
    shared_image: BinaryContent | None = None
    if (
        first_valid is not None
        and languages
        and agent_service is run_category_match_agent
    ):
        check_cancellation()
        try:
            shared_identity, shared_image = _canonicalize_for_languages(
                first_valid[0],
                first_valid[1],
                first_valid[2],
                languages,
                timeout_seconds=CATEGORY_MATCH_DEADLINE_SECONDS,
            )
        except Exception:
            shared_identity = None
            shared_image = None

    def _run_one(item: tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]) -> CategoryMatchResult:
        check_cancellation()
        prod, drf, tgt = item
        return match_category(
            prod,
            drf,
            tgt,
            searcher_factory=searcher_factory,
            agent_service=agent_service,
            detail_loader=detail_loader,
            canonical_identity=shared_identity,
            image_content=shared_image,
            enable_canonicalizer=False,
        )

    workers = max(1, min(3, len(subjects)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="category-match-batch") as pool:
        futures = [pool.submit(copy_context().run, _run_one, subject) for subject in subjects]
        return [future.result() for future in futures]


__all__ = [
    "CATEGORY_MATCH_BUDGET_PROFILE",
    "CATEGORY_MATCH_DEADLINE_SECONDS",
    "CATEGORY_MATCH_USE_CASE_ID",
    "CategoryMatchError",
    "category_product_facts",
    "failed_category_match",
    "failure_from_exception",
    "finalize_category_match",
    "match_categories_batch",
    "match_category",
    "prepare_category_match_input",
    "remaining_deadline_seconds",
    "setup_category_match_search",
]
