"""``category.match`` 的稳定业务能力入口。

任务入口按当前平台创建一个绑定式 ``CategorySearcher``，AI 首轮只接收裁剪后的
商品事实，并通过唯一工具 ``search_categories(keyword)`` 逐轮搜索。最终选择必须
来自本次工具真实返回的候选；详情和属性只在服务端终检，不暴露给模型。
"""

from __future__ import annotations

from html import unescape
import json
import re
import time
from typing import Any, Callable, Mapping

from erp_web.context import get_context
from erp_web.marketplaces.category_provider import CategorySearcher
from erp_web.runtime_units.category_searchers import (
    CategorySearchError,
    create_category_searcher,
)
from erp_web.runtime_units.category_store import (
    fetch_category_record,
)
from erp_web.runtime_units.category_tools import (
    CATEGORY_SEARCH_PERMISSION,
    CategoryCandidateLedger,
    build_category_search_toolset,
)
from erp_web.schemas.category import (
    CategoryCandidate,
    CategoryMatchDecision,
    CategoryMatchFailure,
    CategoryMatchResult,
    CategoryMatchStatus,
    CategoryMatchTrace,
)
from erp_web.services.ai_gateway_providers import AiProviderClient
from erp_web.services.ai_prompt_templates import (
    load_ai_use_case_prompt_pair,
    render_prompt_template,
)
from erp_web.services.ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    CAPABILITY_TOOL_TURN,
    AiChatProvider,
)
from erp_web.services.ai_task_runner import AiTaskExecutionError, AiTaskRunner
from erp_web.services.ai_tool_provider_adapters import JsonToolTurnProviderAdapter
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.stores.product_store import normalize_product_fields


CATEGORY_MATCH_USE_CASE_ID = "category.product_match"
CATEGORY_MATCH_BUDGET_PROFILE = "category.match.default"
CATEGORY_MATCH_DEADLINE_SECONDS = 60
CATEGORY_MATCH_RESULT_SCHEMA = {
    "type": "object",
    "required": [
        "selected_category_id",
        "abstained",
        "model_confidence",
        "evidence",
    ],
    "properties": {
        "selected_category_id": {"type": "string", "maxLength": 160},
        "abstained": {"type": "boolean"},
        "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "maxItems": 8,
        },
    },
    "additionalProperties": False,
}

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

ModelRunner = Callable[
    [dict[str, Any], AiToolSet],
    tuple[dict[str, Any], CategoryMatchTrace],
]
CategorySearcherFactory = Callable[..., CategorySearcher]


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


class _ModelRunError(RuntimeError):
    def __init__(self, cause: Exception, trace: CategoryMatchTrace) -> None:
        self.cause = cause
        self.code = str(getattr(cause, "code", "") or "MODEL_PROVIDER_ERROR")
        self.trace = trace
        super().__init__(str(cause) or cause.__class__.__name__)


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
        "method": "tool_loop",
        "confidence_band": "low",
        "model_confidence": 0.0,
        "decision_score": 0.0,
        "abstained": True,
        "evidence": [],
        "search_count": 0,
    }


def _failure_from_exception(exc: Exception, *, stage: str) -> CategoryMatchFailure:
    if isinstance(exc, CategoryMatchError):
        return exc.to_dict()
    cause = exc.cause if isinstance(exc, _ModelRunError) else exc
    if isinstance(cause, CategorySearchError):
        return {
            "code": cause.code,
            "message": str(cause),
            "stage": stage,
            "retryable": cause.retryable,
        }
    if isinstance(cause, AiTaskExecutionError):
        return {
            "code": cause.code,
            "message": str(cause),
            "stage": stage,
            "retryable": cause.code
            in {
                "MODEL_TIMEOUT",
                "TASK_DEADLINE_EXCEEDED",
                "TOOL_CALL_BUDGET_EXCEEDED",
            },
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
) -> CategoryMatchResult:
    public_candidates: list[CategoryCandidate] = [
        {
            "category_id": str(candidate.get("category_id") or ""),
            "name": str(candidate.get("name") or ""),
            "path_segments": list(candidate.get("path_segments") or []),
        }
        for candidate in ledger.candidates()
    ]
    return {
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
        "trace": trace or {"conversation_id": "", "task_run_id": ""},
    }


def _messages_for_match(payload: dict[str, Any]) -> list[dict[str, str]]:
    context = get_context()
    app_config = context.config.load_app_config()
    prompt = load_ai_use_case_prompt_pair(
        context.paths.app_dir,
        app_config,
        CATEGORY_MATCH_USE_CASE_ID,
    )
    input_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    user_prompt = render_prompt_template(
        prompt.get("user") or "请根据商品事实搜索并匹配类目：{$input_json}",
        {"input_json": input_json},
    )
    system_prompt = prompt.get("system") or (
        "必须先调用 search_categories；只能选择工具返回的 category_id，"
        "没有合适结果时继续换词搜索或 abstain。最终仅返回 JSON。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _run_match_ai(
    payload: dict[str, Any],
    toolset: AiToolSet,
    *,
    timeout_seconds: int,
) -> tuple[dict[str, Any], CategoryMatchTrace]:
    context = get_context()
    app_config = context.config.load_app_config()
    resolved_client = AiProviderClient.for_use_case(
        context.paths.app_dir,
        app_config,
        CATEGORY_MATCH_USE_CASE_ID,
        timeout_seconds=timeout_seconds,
        default_timeout_seconds=CATEGORY_MATCH_DEADLINE_SECONDS,
    )
    client = AiProviderClient(
        app_dir=resolved_client.app_dir,
        use_case_id=resolved_client.use_case_id,
        model=resolved_client.model,
        required_capabilities=resolved_client.required_capabilities,
        timeout_seconds=min(timeout_seconds, resolved_client.timeout_seconds),
    )
    chat_provider = client.provider_for(CAPABILITY_CHAT_JSON)
    if not isinstance(chat_provider, AiChatProvider):
        raise AiTaskExecutionError(
            "TOOL_PROTOCOL_UNSUPPORTED",
            "当前 Provider 未实现 JSON tool protocol 所需的 chat_json。",
        )
    adapter = JsonToolTurnProviderAdapter(chat_provider, app_dir=context.paths.app_dir)
    messages = _messages_for_match(payload)
    invocation = client.start_invocation(
        CAPABILITY_TOOL_TURN,
        adapter,
        {"mode": "tool_loop", "target": payload["target"], "messages": messages},
        budget_profile=CATEGORY_MATCH_BUDGET_PROFILE,
        permissions={CATEGORY_SEARCH_PERMISSION},
    )
    trace: CategoryMatchTrace = {
        "conversation_id": invocation.recorder.conversation_id,
        "task_run_id": invocation.execution_context.task_run_id,
    }
    try:
        result = AiTaskRunner(
            max_tool_rounds=3,
            max_tool_calls=3,
            max_tool_output_bytes=32 * 1024,
        ).run(
            invocation,
            messages=messages,
            toolset=toolset,
            result_schema=CATEGORY_MATCH_RESULT_SCHEMA,
        )
    except Exception as exc:
        raise _ModelRunError(exc, trace) from exc
    return result, trace


def _confidence_band(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def _remaining_deadline_seconds(deadline_at: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise AiTaskExecutionError(
            "TASK_DEADLINE_EXCEEDED",
            "类目匹配总 deadline 已耗尽",
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


def match_category(
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    searcher: CategorySearcher | None = None,
    searcher_factory: CategorySearcherFactory = create_category_searcher,
    model_runner: ModelRunner | None = None,
    detail_loader: Callable[..., dict[str, Any]] = fetch_category_record,
) -> CategoryMatchResult:
    """运行一次同步、最多三次搜索且允许 abstain 的 ``category.match``。"""

    deadline_at = time.monotonic() + CATEGORY_MATCH_DEADLINE_SECONDS
    platform = _text(target.get("platform"), 80).lower()
    site = _text(target.get("site") or target.get("site_id"), 80)
    normalized_target = {
        "platform": platform,
        "site": site,
        "language": _text(target.get("language") or target.get("locale"), 80),
    }
    ledger = CategoryCandidateLedger()
    decision = _empty_decision()
    trace: CategoryMatchTrace = {"conversation_id": "", "task_run_id": ""}
    if not platform or not site:
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "TARGET_REQUIRED",
                "message": "类目匹配需要 platform 和 site。",
                "stage": "input",
                "retryable": False,
            },
        )

    facts = category_product_facts(product, draft, normalized_target)
    if not facts["source"]["title"] and not facts["target"]["title"]:
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure={
                "code": "INPUT_INVALID",
                "message": "商品缺少可用于类目匹配的原文或目标语言标题。",
                "stage": "input",
                "retryable": False,
            },
        )

    try:
        scoped_searcher = searcher or searcher_factory(
            platform,
            site=site,
            limit=8,
            timeout_seconds=min(8, _remaining_deadline_seconds(deadline_at)),
            deadline_at=deadline_at,
        )
        toolset = build_category_search_toolset(
            searcher=scoped_searcher,
            ledger=ledger,
        )
    except Exception as exc:
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=_failure_from_exception(exc, stage="searcher_setup"),
        )

    payload = {
        "mode": "tool_loop",
        "target": normalized_target,
        "product": facts,
    }
    try:
        remaining_seconds = _remaining_deadline_seconds(deadline_at)
        if model_runner is None:
            if remaining_seconds < 1:
                raise AiTaskExecutionError(
                    "TASK_DEADLINE_EXCEEDED",
                    "类目匹配剩余 deadline 不足以启动模型调用",
                )
            model_result, trace = _run_match_ai(
                payload,
                toolset,
                timeout_seconds=int(remaining_seconds),
            )
        else:
            model_result, trace = model_runner(payload, toolset)
        _remaining_deadline_seconds(deadline_at)
    except Exception as exc:
        if isinstance(exc, _ModelRunError):
            trace = exc.trace
        decision["search_count"] = ledger.search_count
        return _result(
            ok=False,
            status="failed",
            target=normalized_target,
            ledger=ledger,
            decision=decision,
            failure=_failure_from_exception(exc, stage="model"),
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
                "message": "模型必须至少调用一次 search_categories 后才能结束任务。",
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
            failure=_failure_from_exception(ledger.last_error, stage="search"),
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
        if ledger.search_count < 3:
            return _result(
                ok=False,
                status="failed",
                target=normalized_target,
                ledger=ledger,
                decision=decision,
                failure={
                    "code": "CATEGORY_SEARCH_INCOMPLETE",
                    "message": "未匹配到类目时必须更换关键字并完成最多 3 次搜索后再 abstain。",
                    "stage": "model",
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
                "message": "搜索后仍没有足够匹配的类目，请人工确认。",
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
            ensure_deadline=lambda: _remaining_deadline_seconds(deadline_at),
        )
    except Exception as exc:
        failure = _failure_from_exception(exc, stage="validation")
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
            )
        return _result(
            ok=True,
            status="unresolved",
            target=normalized_target,
            ledger=ledger,
            decision={**decision, "abstained": True},
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


__all__ = [
    "CATEGORY_MATCH_BUDGET_PROFILE",
    "CATEGORY_MATCH_DEADLINE_SECONDS",
    "CATEGORY_MATCH_RESULT_SCHEMA",
    "CATEGORY_MATCH_USE_CASE_ID",
    "CategoryMatchError",
    "category_product_facts",
    "match_category",
]
