from __future__ import annotations

"""稳定草稿上的 focused ``category.match`` Capability。"""

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
import threading
from typing import Annotated, Any

from erp_web.context import get_context
from erp_web.product_model import apply_category_selection
from erp_web.runtime_units.category_store import fetch_category_record
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
from erp_web.services.forbidden_category_service import forbidden_category_issue
from erp_web.runtime_units.market_capability_support import (
    CategoryRecordLoader,
    MarketPrepareStore,
    assert_target_mutable,
    category_path,
    invalidate_target_publish_preparation,
    load_category_record,
    load_draft,
    persist_target_projection,
    product_with_target,
    require_platform,
    select_target,
    text,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchBatchItem,
    CategoryMatchBatchOption,
    CategoryMatchBatchRequest,
    CategoryMatchBatchResult,
    CategoryMatchCapabilityResult,
    CategoryMatchRequest,
)
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_input_provenance import require_category_user_selection
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputOption,
    CapabilityInputRequired,
)


CategoryMatcher = Callable[..., Mapping[str, Any]]
_INPUT_OPTION_LABEL_MAX_LENGTH = 1000


def _category_candidate_option(
    candidate: Mapping[str, Any],
) -> CapabilityInputOption | None:
    """把类目候选转换为“可读文案 + 稳定 ID”的待选项。"""

    category_id = text(candidate.get("category_id"))
    if not category_id:
        return None
    raw_path = candidate.get("path_segments")
    path_segments = (
        [text(segment) for segment in raw_path if text(segment)]
        if isinstance(raw_path, (list, tuple))
        else []
    )
    description = " › ".join(path_segments) or text(candidate.get("name"))
    if not description or description.casefold() == category_id.casefold():
        return CapabilityInputOption(value=category_id, label=category_id)
    suffix = f"（{category_id}）"
    description = description[
        : max(1, _INPUT_OPTION_LABEL_MAX_LENGTH - len(suffix))
    ].rstrip()
    return CapabilityInputOption(
        value=category_id,
        label=f"{description}{suffix}",
    )


def match_category(
    request: CategoryMatchRequest,
    *,
    product_store: MarketPrepareStore,
    matcher: CategoryMatcher,
    category_record_loader: CategoryRecordLoader = fetch_category_record,
) -> CategoryMatchCapabilityResult:
    """复用现有 focused Agent，并把终检通过的类目写入稳定目标草稿。"""

    platform = require_platform(request.target_platform)
    draft, product = load_draft(product_store, request.draft_id)
    target = select_target(draft, platform=platform, site=request.site)
    target_draft = draft_for_publish_target(draft, target)
    assert_target_mutable(target_draft)
    # 未指定类目时始终重新匹配，已有结果不能吞掉用户的复核请求。
    selected_category_id = request.category_id
    query = ""
    confidence = 0.0

    if not selected_category_id:
        try:
            match_args = (
                product_with_target(product, platform, target_draft),
                target_draft,
                target,
            )
            match_result = dict(matcher(*match_args))
        except (BusinessCapabilityError, CapabilityInputRequired):
            raise
        except Exception as exc:
            raise BusinessCapabilityError(
                "CATEGORY_MATCH_EXECUTION_FAILED",
                f"类目匹配执行失败：{exc}",
                retryable=True,
            ) from exc
        status = text(match_result.get("status")).lower()
        failure = (
            match_result.get("failure")
            if isinstance(match_result.get("failure"), Mapping)
            else {}
        )
        candidates = (
            match_result.get("candidates")
            if isinstance(match_result.get("candidates"), list)
            else []
        )
        if status == "unresolved":
            failure_code = text(failure.get("code"))
            if failure_code == "CATEGORY_FORBIDDEN_NO_ALTERNATIVE":
                raise BusinessCapabilityError(
                    failure_code,
                    text(failure.get("message")) or "最匹配的类目属于禁售范围，且无合规替代类目。",
                    retryable=False,
                )
            candidate_options: list[CapabilityInputOption] = []
            for candidate in candidates:
                if not isinstance(candidate, Mapping) or candidate.get("forbidden"):
                    continue
                option = _category_candidate_option(candidate)
                if option is not None:
                    candidate_options.append(option)
            raise CapabilityInputRequired(
                failure_code or "CATEGORY_MATCH_UNRESOLVED",
                text(failure.get("message")) or "类目匹配需要人工确认。",
                key="category_id",
                label="平台类目",
                reason=text(failure.get("message")) or "请选择一个候选类目。",
                options=candidate_options,
                input_type="select" if candidate_options else "text",
            )
        if status != "completed" or not bool(match_result.get("ok")):
            code = text(failure.get("code")) or "CATEGORY_MATCH_FAILED"
            message = text(failure.get("message")) or "类目匹配失败。"
            if code == "INPUT_INVALID":
                raise CapabilityInputRequired(
                    code,
                    message,
                    key="title",
                    label="商品标题",
                    reason="请先补充能识别商品类型的标题。",
                )
            if code == "TARGET_REQUIRED":
                raise CapabilityInputRequired(
                    code,
                    message,
                    key="site",
                    label="目标站点",
                    reason="请明确类目匹配的目标站点。",
                )
            failure_details = (
                dict(failure.get("details"))
                if isinstance(failure.get("details"), Mapping)
                else None
            )
            raise BusinessCapabilityError(
                code,
                message,
                retryable=bool(failure.get("retryable")),
                details=failure_details,
            )
        selected_category_id = text(match_result.get("selected_category_id"))
        if not selected_category_id:
            raise BusinessCapabilityError(
                "CATEGORY_MATCH_RESULT_INVALID",
                "类目匹配完成但没有返回 category_id。",
            )
        query = text(match_result.get("query"))
        decision = (
            match_result.get("decision")
            if isinstance(match_result.get("decision"), Mapping)
            else {}
        )
        try:
            confidence = float(decision.get("model_confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
    record = load_category_record(
        category_record_loader,
        platform=platform,
        site=text(target.get("site")),
        selected_category_id=selected_category_id,
    )
    if platform == "yandex":
        settings = get_context().config.forbidden_categories()
        categories = {item["category_id"]: item["name"] for item in settings["categories"]}
        raw_ancestors = record.get("ancestor_ids") if isinstance(record, Mapping) else None
        ancestors = (
            tuple(text(item) for item in raw_ancestors if text(item))
            if isinstance(raw_ancestors, (list, tuple)) and raw_ancestors
            else get_context().category_catalog.cached_category_ancestor_ids(platform, selected_category_id)
        )
        if issue := forbidden_category_issue(
            platform, selected_category_id, categories, ancestor_ids=ancestors,
        ):
            raise BusinessCapabilityError("CATEGORY_FORBIDDEN", str(issue["message"]), retryable=False)
    projected = product_with_target(product, platform, target_draft)
    updated_product = apply_category_selection(projected, platform, record)
    updated_draft = deepcopy(updated_product["drafts"][platform])
    updated_draft.update(
        {
            "category_id": selected_category_id,
            "description_category_id": text(
                record.get("description_category_id")
            ),
            "category_path": category_path(record),
        }
    )
    invalidated = invalidate_target_publish_preparation(
        product_store=product_store,
        product=updated_product,
        draft=draft,
        target=target,
        target_draft=updated_draft,
    )
    invalidated_target = select_target(
        invalidated,
        platform=platform,
        site=text(target.get("site")),
    )
    invalidated_projection = draft_for_publish_target(
        invalidated,
        invalidated_target,
    )
    for key in (
        "validation_errors",
        "category_precheck",
        "last_precheck",
        "last_precheck_target",
        "last_publish_task",
        "publish_status",
        "status",
    ):
        updated_draft[key] = deepcopy(invalidated_projection.get(key))
    changed = any(
        target_draft.get(key) != updated_draft.get(key)
        for key in (
            "category_id",
            "description_category_id",
            "category_path",
            "category_precheck",
            "last_precheck",
            "last_precheck_target",
            "last_publish_task",
            "publish_status",
            "status",
        )
    )
    saved = persist_target_projection(
        product_store=product_store,
        product=product,
        draft=draft,
        target=target,
        updated_product=updated_product,
        updated_target_draft=updated_draft,
    )
    saved_target = select_target(
        saved,
        platform=platform,
        site=text(target.get("site")),
    )
    saved_projection = draft_for_publish_target(saved, saved_target)
    if text(saved_projection.get("category_id")) != selected_category_id:
        raise BusinessCapabilityError(
            "CATEGORY_MATCH_PERSIST_INCOMPLETE",
            "类目匹配结果保存后无法从目标草稿验证。",
        )
    return CategoryMatchCapabilityResult(
        draft_id=request.draft_id,
        platform=platform,
        site=text(saved_target.get("site")),
        category_id=selected_category_id,
        category_path=text(saved_projection.get("category_path")),
        query=query,
        model_confidence=confidence,
        changed=changed,
    )


@dataclass(frozen=True)
class CategoryCapabilityScope:
    """类目匹配 Capability 的可信依赖边界。"""

    products: MarketPrepareStore
    matcher: CategoryMatcher


CATEGORY_MATCH_TOOL = "category_match"
CATEGORY_MATCH_BATCH_TOOL = "category_match_batch"
_CATEGORY_MATCH_BATCH_MAX_WORKERS = 10
_CATEGORY_MATCH_BATCH_RETRYABLE_FAILURE_LIMIT = 3


def match_categories_for_drafts(
    request: CategoryMatchBatchRequest,
    *,
    product_store: MarketPrepareStore,
    matcher: CategoryMatcher,
    category_record_loader: CategoryRecordLoader = fetch_category_record,
    max_workers: int = _CATEGORY_MATCH_BATCH_MAX_WORKERS,
) -> CategoryMatchBatchResult:
    """用滑动窗口线程池并发匹配多份草稿类目，避免分批同步屏障被单个慢尾阻塞。"""

    platform = require_platform(request.target_platform)
    draft_ids = list(dict.fromkeys(request.draft_ids))
    failure_lock = threading.Lock()
    retryable_failures = 0

    def _run_one(draft_id: str) -> CategoryMatchBatchItem:
        nonlocal retryable_failures
        check_cancellation()
        with failure_lock:
            if retryable_failures >= _CATEGORY_MATCH_BATCH_RETRYABLE_FAILURE_LIMIT:
                return CategoryMatchBatchItem(
                    draft_id=draft_id,
                    ok=False,
                    status="failed",
                    platform=platform,
                    site=request.site,
                    error_code="CATEGORY_SERVICE_UNAVAILABLE",
                    error="本批该平台类目服务已连续失败多次，停止继续请求相同依赖；网络或服务恢复后可重试未完成项。",
                )
        try:
            matched = match_category(
                CategoryMatchRequest(
                    draft_id=draft_id,
                    target_platform=platform,
                    site=request.site,
                ),
                product_store=product_store,
                matcher=matcher,
                category_record_loader=category_record_loader,
            )
            return CategoryMatchBatchItem(
                draft_id=matched.draft_id,
                ok=True,
                status="matched",
                platform=matched.platform,
                site=matched.site,
                category_id=matched.category_id,
                category_path=matched.category_path,
                query=matched.query,
                model_confidence=matched.model_confidence,
                changed=matched.changed,
            )
        except CapabilityInputRequired as exc:
            options = [
                CategoryMatchBatchOption(value=opt.value, label=opt.label)
                for opt in exc.options
                if opt.value
            ]
            return CategoryMatchBatchItem(
                draft_id=draft_id,
                ok=False,
                status="unresolved",
                platform=platform,
                site=request.site,
                error_code=exc.code,
                error=str(exc),
                needs_confirmation=True,
                candidate_options=options,
            )
        except BusinessCapabilityError as exc:
            if exc.retryable:
                with failure_lock:
                    retryable_failures += 1
            is_forbidden = exc.code in {"CATEGORY_FORBIDDEN", "CATEGORY_FORBIDDEN_NO_ALTERNATIVE"}
            return CategoryMatchBatchItem(
                draft_id=draft_id,
                ok=False,
                status="forbidden" if is_forbidden else "failed",
                platform=platform,
                site=request.site,
                error_code=exc.code,
                error=str(exc),
            )
        except Exception as exc:
            with failure_lock:
                retryable_failures += 1
            return CategoryMatchBatchItem(
                draft_id=draft_id,
                ok=False,
                status="failed",
                platform=platform,
                site=request.site,
                error_code="CATEGORY_MATCH_EXECUTION_FAILED",
                error=str(exc) or "类目匹配执行失败",
            )

    workers = min(max(1, int(max_workers or 1)), len(draft_ids))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="category-match-drafts") as pool:
            futures = [pool.submit(copy_context().run, _run_one, draft_id) for draft_id in draft_ids]
            items = [future.result() for future in futures]
    else:
        items = [_run_one(draft_id) for draft_id in draft_ids]

    matched_count = sum(1 for item in items if item.status == "matched")
    unresolved_count = sum(1 for item in items if item.status == "unresolved")
    forbidden_count = sum(1 for item in items if item.status == "forbidden")
    failed_count = len(items) - matched_count - unresolved_count - forbidden_count
    return CategoryMatchBatchResult(
        total=len(items),
        matched_count=matched_count,
        unresolved_count=unresolved_count,
        forbidden_count=forbidden_count,
        failed_count=failed_count,
        items=items,
    )


@ai_tool(
    name=CATEGORY_MATCH_TOOL,
    description="为单个草稿目标市场匹配并保存平台类目；无法消歧时返回候选。多份草稿批量自动匹配请优先使用 category_match_batch。",
    permission="category.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def category_match(
    request: CategoryMatchRequest,
    scope: Annotated[CategoryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryMatchCapabilityResult:
    require_category_user_selection(request.category_id, execution,
                                    source_message_id=request.source_message_id, draft_id=request.draft_id)
    return match_category(
        request,
        product_store=scope.products,
        matcher=scope.matcher,
    )


@ai_tool(
    name=CATEGORY_MATCH_BATCH_TOOL,
    description=(
        "批量并发为多份草稿自动匹配并保存同一目标市场平台类目（服务端滑动窗口并发，不因单个慢尾阻塞整批）；"
        "逐草稿返回 matched/unresolved/forbidden/failed 结果与候选选项。多份草稿自动匹配类目时优先使用本工具，"
        "无需在脚本里分批循环调用单个 category_match。人工明确指定具体 category_id 覆盖时仍用 category_match。"
    ),
    permission="category.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def category_match_batch(
    request: CategoryMatchBatchRequest,
    scope: Annotated[CategoryCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryMatchBatchResult:
    del execution
    return match_categories_for_drafts(
        request,
        product_store=scope.products,
        matcher=scope.matcher,
    )


CATEGORY_AI_CAPABILITIES = (category_match, category_match_batch)


__all__ = [
    "CATEGORY_AI_CAPABILITIES",
    "CATEGORY_MATCH_BATCH_TOOL",
    "CATEGORY_MATCH_TOOL",
    "CategoryCapabilityScope",
    "CategoryMatcher",
    "category_match",
    "category_match_batch",
    "match_categories_for_drafts",
    "match_category",
]
