"""``category.match`` 的稳定业务能力入口。

任务入口按当前平台创建绑定式类目检索对象。拥有完整树的 Ozon 将真实顶层节点
放入首轮输入，再通过工具逐层展开；Mercado Libre 继续使用远端关键字发现。
最终选择必须来自工具真实返回的商品类型；详情和属性只在服务端终检。

同步入口（Global Task 等 child 场景）与 focused 流式根运行共享同一套输入
校验、检索装配和业务终检（``finalize_category_match``），保证两条路径的
类型化 ``CategoryMatchResult`` 语义一致。
"""

from __future__ import annotations

from html import unescape
import re
import time
from typing import Any, Callable, Mapping, Protocol

from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    create_category_searcher,
)
from erp_web.runtime_units.category_store import (
    fetch_category_record,
)
from erp_web.runtime_units.category_tools import (
    CategoryCandidateLedger,
    build_category_match_toolset,
)
from erp_web.schemas.category import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchFailure,
    CategoryMatchResult,
    CategoryMatchStatus,
    CategoryMatchTrace,
)
from erp_web.services.ai_agent_factory import AiAgentExecutionError
from erp_web.services.category_match_agent_service import (
    CATEGORY_MATCH_BUDGET_PROFILE,
    CATEGORY_MATCH_DEADLINE_SECONDS,
    CATEGORY_MATCH_USE_CASE_ID,
    CategoryMatchAgentRun,
    run_category_match_agent,
)
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.stores.product_store import normalize_product_fields


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_NOISY_ATTRIBUTE_MARKERS = (
    "price",
    "payment",
    "shipping",
    "delivery",
    "logistics",
    "promotion",
    "discount",
    "coupon",
    "warranty policy",
    "disclaimer",
    "价格",
    "付款",
    "支付",
    "运费",
    "物流",
    "发货",
    "促销",
    "优惠",
    "免责声明",
    "цена",
    "доставка",
    "скидка",
    "оплата",
    "envío",
    "precio",
    "descuento",
    "pago",
)

CategorySearcherFactory = Callable[..., CategorySearcher]


class CategoryMatchAgentService(Protocol):
    def __call__(
        self,
        payload: Mapping[str, Any],
        toolset: AiToolSet,
        ledger: CategoryCandidateLedger,
        *,
        timeout_seconds: float,
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


def _clean_description(value: Any, limit: int = 1600) -> str:
    text = unescape(_HTML_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", text).strip()[:limit]


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _bullets(*values: Any, limit: int = 8) -> list[str]:
    result: list[str] = []
    for value in values:
        rows = value if isinstance(value, (list, tuple)) else []
        for row in rows:
            text = _clean_description(row, 240)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                return result
    return result


def _attribute_value(value: Any) -> str | int | float | bool | list[str] | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = _clean_description(value, 240)
        return text or None
    if isinstance(value, (list, tuple)):
        rows = [_clean_description(item, 120) for item in value[:8]]
        rows = [item for item in rows if item]
        return rows or None
    return None


def _key_attributes(*values: Any, limit: int = 12) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            name = _text(key, 100)
            normalized_name = name.casefold()
            if (
                not name
                or name.isdigit()
                or any(marker in normalized_name for marker in _NOISY_ATTRIBUTE_MARKERS)
                or name in attributes
            ):
                continue
            normalized_value = _attribute_value(item)
            if normalized_value is None:
                continue
            attributes[name] = normalized_value
            if len(attributes) >= limit:
                return attributes
    return attributes


def category_product_facts(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成首次模型请求；只保留能帮助识别商品的事实。"""

    normalized = normalize_product_fields(dict(product))
    source = _first_mapping(normalized.get("source"), product.get("source"))
    draft_data = draft if isinstance(draft, Mapping) else {}
    target_data = target if isinstance(target, Mapping) else {}

    source_title = _text(
        source.get("title") or normalized.get("name") or product.get("name"),
        500,
    )
    target_title = _text(draft_data.get("title"), 500)
    source_description = _clean_description(
        source.get("description")
        or source.get("clean_source_text")
        or source.get("source_text")
        or normalized.get("description")
    )
    target_description = _clean_description(
        draft_data.get("description")
        or draft_data.get("description_html")
        or draft_data.get("body")
    )
    return {
        "source": {
            "language": _text(
                source.get("language")
                or source.get("locale")
                or normalized.get("source_language"),
                80,
            ),
            "title": source_title,
            "description": source_description,
        },
        "target": {
            "language": _text(
                draft_data.get("language")
                or target_data.get("language")
                or target_data.get("locale"),
                80,
            ),
            "title": target_title,
            "description": target_description,
        },
        "facts": {
            "source_category": _text(
                normalized.get("category") or source.get("category"),
                240,
            ),
            "brand": _text(
                draft_data.get("brand")
                or source.get("brand")
                or normalized.get("brand"),
                160,
            ),
            "model": _text(
                draft_data.get("model")
                or source.get("model")
                or normalized.get("model"),
                160,
            ),
            "bullets": _bullets(
                draft_data.get("bullets"),
                draft_data.get("bullet_points"),
                source.get("bullets"),
                source.get("bullet_points"),
            ),
            "attributes": _key_attributes(
                draft_data.get("attributes"),
                source.get("attributes"),
                normalized.get("attributes"),
            ),
        },
    }


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
    agent_run: CategoryMatchAgentRun | None = None,
) -> CategoryMatchResult:
    public_candidates: list[CategoryCandidate] = [
        {
            "category_id": str(candidate.get("category_id") or ""),
            "name": str(candidate.get("name") or ""),
            "path_segments": list(candidate.get("path_segments") or []),
        }
        for candidate in ledger.candidates()
    ]
    result: CategoryMatchResult = {
        "ok": ok,
        "status": status,
        "target": {
            "platform": _text(target.get("platform"), 80).lower(),
            "site": _text(target.get("site") or target.get("site_id"), 80),
        },
        "selected_category_id": selected_category_id,
        "query": ledger.last_keyword,
        "candidates": public_candidates,
        "decision": decision,
        "failure": failure,
        "trace": trace or {"task_run_id": ""},
    }
    if agent_run is not None:
        agent_run.finish_business_result(result)
    return result


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


def _validate_selected_category(
    *,
    selected_category_id: str,
    platform: str,
    site: str,
    ledger: CategoryCandidateLedger,
    detail_loader: Callable[..., dict[str, Any]],
    ensure_deadline: Callable[[], float],
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

    try:
        detail = detail_loader(
            platform,
            selected_category_id,
            site=site,
            include_attributes=True,
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

    attributes = (
        detail.get("attributes")
        if isinstance(detail.get("attributes"), Mapping)
        else {}
    )
    if not isinstance(attributes.get("required"), list) or not isinstance(
        attributes.get("optional"), list
    ):
        raise _validation_error(
            "CATEGORY_ATTRIBUTES_UNAVAILABLE",
            "类目属性响应结构无效。",
            retryable=True,
        )
    ensure_deadline()
    return candidate


# -- 同步/流式共享阶段 ------------------------------------------------------


def prepare_category_match_input(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], CategoryMatchFailure | None]:
    """归一化目标与商品事实；输入不合法时返回失败描述。

    返回 ``(normalized_target, facts, failure)``；``failure`` 非空时不得继续
    启动 Agent。focused start endpoint 在同步阶段执行同一校验。
    """

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
) -> tuple[dict[str, Any], AiToolSet]:
    """创建绑定式 searcher 与 ToolSet，并组装首轮 payload；失败抛异常。"""

    scoped_searcher = searcher or searcher_factory(
        str(normalized_target.get("platform") or ""),
        site=str(normalized_target.get("site") or ""),
        limit=8,
        timeout_seconds=min(8, remaining_deadline_seconds(deadline_at)),
        deadline_at=deadline_at,
    )
    tool_bundle = build_category_match_toolset(
        searcher=scoped_searcher,
        ledger=ledger,
    )
    payload: dict[str, Any] = {
        "target": dict(normalized_target),
        "product": dict(facts),
    }
    if tool_bundle.retrieval_mode == "tree_navigation":
        payload["category_navigation"] = {
            "mode": "tree_navigation",
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
    """Agent 运行结束（或运行失败）后的共享业务终检。

    同步入口与 focused 流式任务共用；业务校验、领域终检和类型化 response
    组装成功后才允许把根运行标记为 completed。
    """

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
        if (
            isinstance(exc, AiAgentExecutionError)
            and exc.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
            and ledger.search_count > 0
        ):
            decision.update(
                confidence_band="low",
                model_confidence=0.0,
                decision_score=0.0,
                abstained=True,
                evidence=["类目检索达到本次运行上限，未静默选择候选。"],
            )
            return _result(
                ok=True,
                status="unresolved",
                target=normalized_target,
                ledger=ledger,
                decision=decision,
                failure={
                    "code": "ABSTAIN_RETRIEVAL_LIMIT",
                    "message": "类目分支仍无法确定，请人工确认。",
                    "stage": "decision",
                    "retryable": False,
                },
                trace=trace,
                agent_run=agent_run,
            )
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=failure_from_exception(exc, stage="model"),
            trace=trace,
            agent_run=agent_run,
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
            agent_run=agent_run,
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
            agent_run=agent_run,
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
            agent_run=agent_run,
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
            agent_run=agent_run,
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
                        else "未匹配到类目时必须更换关键字并完成 3 次搜索后再 abstain。"
                    ),
                    "stage": "model",
                    "retryable": False,
                },
                trace=trace,
                agent_run=agent_run,
            )
        return _result(
            ok=True,
            status="unresolved",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "ABSTAIN_NO_MATCH",
                "message": "搜索后仍没有足够匹配的类目，请人工确认。",
                "stage": "decision",
                "retryable": False,
            },
            trace=trace,
            agent_run=agent_run,
        )

    try:
        _validate_selected_category(
            selected_category_id=selected_category_id,
            platform=platform,
            site=site,
            ledger=ledger,
            detail_loader=detail_loader,
            ensure_deadline=lambda: remaining_deadline_seconds(deadline_at),
        )
    except Exception as exc:
        failure = failure_from_exception(exc, stage="validation")
        if failure["code"] in {
            "MODEL_SELECTED_UNKNOWN_CATEGORY",
            "TASK_DEADLINE_EXCEEDED",
        }:
            return _result(
                ok=False,
                status="failed",
                target=normalized_target,
                ledger=ledger,
                decision=decision,
                failure=failure,
                trace=trace,
                agent_run=agent_run,
            )
        return _result(
            ok=True,
            status="unresolved",
            target=normalized_target,
            ledger=ledger,
            decision={**decision, "abstained": True},
            failure=failure,
            trace=trace,
            agent_run=agent_run,
        )

    return _result(
        ok=True,
        status="completed",
        target=normalized_target,
        ledger=ledger,
        decision=decision,
        selected_category_id=selected_category_id,
        trace=trace,
        agent_run=agent_run,
    )


def match_category(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    searcher: CategorySearcher | None = None,
    searcher_factory: CategorySearcherFactory = create_category_searcher,
    agent_service: CategoryMatchAgentService = run_category_match_agent,
    detail_loader: Callable[..., dict[str, Any]] = fetch_category_record,
) -> CategoryMatchResult:
    """运行一次同步、最多三次搜索且允许 abstain 的 ``category.match``。

    供 Global Task 等 child 场景使用；用户直接触发的业务根运行走 focused
    start/stream/result endpoint 并复用同一套共享阶段。
    """

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
    try:
        payload, toolset = setup_category_match_search(
            normalized_target,
            facts,
            ledger,
            deadline_at,
            searcher=searcher,
            searcher_factory=searcher_factory,
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
        agent_run = agent_service(
            payload,
            toolset,
            ledger,
            timeout_seconds=remaining_seconds,
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


__all__ = [
    "CATEGORY_MATCH_BUDGET_PROFILE",
    "CATEGORY_MATCH_DEADLINE_SECONDS",
    "CATEGORY_MATCH_USE_CASE_ID",
    "CategoryMatchError",
    "category_product_facts",
    "failed_category_match",
    "failure_from_exception",
    "finalize_category_match",
    "match_category",
    "prepare_category_match_input",
    "remaining_deadline_seconds",
    "setup_category_match_search",
]
