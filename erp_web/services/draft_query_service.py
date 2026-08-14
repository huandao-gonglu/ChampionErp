from __future__ import annotations

"""受限草稿查询与基于快照的一基序号解析。"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol
from uuid import uuid4

from erp_web.schemas.draft_capabilities import (
    DraftPublishReadiness,
    DraftQueryCriteria,
    DraftQueryRequest,
    DraftQueryResult,
    DraftQuerySnapshot,
    DraftSummary,
)
from erp_web.schemas.global_tasks import (
    AnswerResolutionScope,
    GlobalPlanningDecision,
    TrustedGlobalAnswer,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


class DraftIndexReader(Protocol):
    def iter_drafts_index(self, scope: str = "active") -> Iterator[dict[str, Any]]:
        ...


class DraftSnapshotRepository(Protocol):
    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        ...

    def load_draft_query_snapshot(
        self,
        snapshot_id: str,
    ) -> DraftQuerySnapshot | None:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _raw_draft(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("raw") if isinstance(item.get("raw"), dict) else {}


def _platforms(item: dict[str, Any]) -> list[str]:
    """返回草稿的目标平台集合。"""

    values = item.get("platforms") if isinstance(item.get("platforms"), list) else []
    primary = _text(item.get("platform")).lower()
    normalized = [
        value
        for raw in ([primary] if primary else []) + values
        if (value := _text(raw).lower())
    ]
    return list(dict.fromkeys(normalized))


def _validation_counts(raw: dict[str, Any]) -> tuple[int, int]:
    precheck = raw.get("last_precheck") if isinstance(raw.get("last_precheck"), dict) else {}
    if precheck:
        errors = precheck.get("errors") if isinstance(precheck.get("errors"), list) else []
        warnings = precheck.get("warnings") if isinstance(precheck.get("warnings"), list) else []
        return len(errors), len(warnings)
    errors = 0
    warnings = 0
    for issue in raw.get("validation_errors") if isinstance(raw.get("validation_errors"), list) else []:
        severity = _text(issue.get("severity") if isinstance(issue, dict) else "error").lower()
        if severity == "warning":
            warnings += 1
        else:
            errors += 1
    return errors, warnings


def _summary(item: dict[str, Any], *, view: str = "detail") -> DraftSummary:
    draft_id = _text(item.get("draft_id"))
    if not draft_id:
        raise BusinessCapabilityError(
            "DRAFT_QUERY_INVALID_RECORD",
            "草稿索引包含缺少 draft_id 的记录。",
        )
    raw = _raw_draft(item)
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    images = raw.get("images") if isinstance(raw.get("images"), list) else []
    validation_errors, validation_warnings = _validation_counts(raw)
    last_precheck = raw.get("last_precheck") if isinstance(raw.get("last_precheck"), dict) else {}
    summary = DraftSummary(
        draft_id=draft_id,
        product_id=_text(item.get("source_product_id") or item.get("product_id")),
        title=_text(item.get("title")),
        product_title=_text(item.get("product_title")),
        source_platform=_text(item.get("source_platform")).lower(),
        target_platform=_text(item.get("platform")).lower(),
        target_platforms=_platforms(item),
        target_site=_text(item.get("site")),
        language=_text(item.get("language")),
        category_id=_text(item.get("category_id")),
        category_path=_text(item.get("category_path")),
        has_description=bool(_text(raw.get("description"))),
        created_at=_text(item.get("created_at")),
        updated_at=_text(item.get("updated_at")),
        readiness=DraftPublishReadiness(
            workflow_status=_text(item.get("status")),
            publish_status=_text(item.get("publish_status")),
            precheck_passed=last_precheck.get("ok") is True,
            image_count=len(images),
            attribute_count=len(attributes),
            validation_error_count=validation_errors,
            validation_warning_count=validation_warnings,
        ),
    )
    if view == "detail":
        return summary

    identity = {
        "draft_id": summary.draft_id,
        "product_id": summary.product_id,
        "title": summary.title,
        "source_platform": summary.source_platform,
        "target_platform": summary.target_platform,
        "target_platforms": summary.target_platforms,
        "target_site": summary.target_site,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }
    status_only = DraftPublishReadiness(
        workflow_status=summary.readiness.workflow_status,
        publish_status=summary.readiness.publish_status,
    )
    if view == "summary":
        return DraftSummary(**identity, readiness=status_only)
    if view == "workflow":
        return DraftSummary(
            **identity,
            readiness=status_only.model_copy(
                update={
                    "precheck_passed": summary.readiness.precheck_passed,
                    "validation_error_count": (
                        summary.readiness.validation_error_count
                    ),
                    "validation_warning_count": (
                        summary.readiness.validation_warning_count
                    ),
                }
            ),
        )
    if view == "publish_readiness":
        return DraftSummary(
            **identity,
            language=summary.language,
            category_id=summary.category_id,
            has_description=summary.has_description,
            readiness=summary.readiness,
        )
    raise BusinessCapabilityError(
        "DRAFT_QUERY_VIEW_INVALID",
        f"不支持的草稿查询视图：{view}。",
    )


def _matches(item: dict[str, Any], query: DraftQueryCriteria) -> bool:
    platform = query.target_platform.lower()
    if platform and platform not in _platforms(item):
        return False
    status = query.status.lower()
    item_statuses = {
        _text(item.get("status")).lower(),
        _text(item.get("publish_status")).lower(),
    }
    if status and status not in item_statuses:
        return False
    keyword = query.keyword.casefold()
    if not keyword:
        return True
    searchable = "\n".join(
        _text(item.get(key))
        for key in (
            "draft_id",
            "product_id",
            "source_product_id",
            "title",
            "product_title",
            "category_id",
            "category_path",
            "source_url",
        )
    ).casefold()
    return keyword in searchable


def _sort_items(
    items: list[dict[str, Any]],
    sort: str,
) -> list[dict[str, Any]]:
    if sort == "title_asc":
        return sorted(
            items,
            key=lambda item: (
                (_text(item.get("title")) or _text(item.get("product_title"))).casefold(),
                _text(item.get("created_at")),
                _text(item.get("draft_id")),
            ),
        )
    reverse = sort == "created_desc"
    return sorted(
        items,
        key=lambda item: (
            _text(item.get("created_at")),
            _text(item.get("draft_id")),
        ),
        reverse=reverse,
    )


def _add_counts(
    item: DraftSummary,
    platform_counts: Counter[str],
    status_counts: Counter[str],
) -> None:
    platforms = list(dict.fromkeys(item.target_platforms or [item.target_platform]))
    if not platforms:
        platforms = ["unknown"]
    platform_counts.update(platform or "unknown" for platform in platforms)
    status_counts.update(
        [item.readiness.workflow_status or item.readiness.publish_status or "unknown"]
    )


def _retain_sorted_item(
    items: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    sort: str,
    limit: int,
) -> None:
    """只保留排序后的前 ``limit`` 条，内存占用不随草稿总量增长。"""

    items.append(item)
    items[:] = _sort_items(items, sort)
    if len(items) > limit:
        items.pop()


def _records_by_id(
    product_store: DraftIndexReader,
    draft_ids: list[str],
) -> dict[str, dict[str, Any]]:
    remaining = set(draft_ids)
    records: dict[str, dict[str, Any]] = {}
    if not remaining:
        return records
    for item in product_store.iter_drafts_index("all"):
        draft_id = _text(item.get("draft_id")) if isinstance(item, dict) else ""
        if draft_id not in remaining:
            continue
        records[draft_id] = item
        remaining.remove(draft_id)
        if not remaining:
            break
    return records


def _snapshot_or_error(
    snapshot_repository: DraftSnapshotRepository,
    snapshot_id: str,
) -> DraftQuerySnapshot:
    snapshot = snapshot_repository.load_draft_query_snapshot(snapshot_id)
    if snapshot is None:
        raise CapabilityInputRequired(
            "DRAFT_QUERY_SNAPSHOT_NOT_FOUND",
            "最近一次草稿查询快照不存在或已经失效。",
            key="draft_query",
            label="草稿查询",
            reason="请先重新查询草稿，再使用“第一个”“第二个”等序号。",
        )
    return DraftQuerySnapshot.model_validate(snapshot)


def resolve_draft_positions(
    snapshot_id: str,
    positions: list[int],
    *,
    product_store: DraftIndexReader,
    snapshot_repository: DraftSnapshotRepository,
) -> list[DraftSummary]:
    """把一基序号解析为快照中的稳定 draft_id，再读取当前事实。"""

    snapshot = _snapshot_or_error(snapshot_repository, snapshot_id)
    if not positions:
        return []
    if len(set(positions)) != len(positions):
        raise BusinessCapabilityError(
            "DRAFT_QUERY_POSITIONS_DUPLICATED",
            "草稿序号不能重复。",
        )
    maximum = len(snapshot.draft_ids)
    out_of_range = [position for position in positions if position < 1 or position > maximum]
    if out_of_range:
        raise CapabilityInputRequired(
            "DRAFT_QUERY_POSITION_OUT_OF_RANGE",
            f"草稿序号超出查询快照范围：{out_of_range}。",
            key="positions",
            label="草稿序号",
            reason=f"当前快照可用序号为 1 到 {maximum}。",
            options=[str(index) for index in range(1, min(maximum, 10) + 1)],
            input_type="string_list",
        )
    selected_ids = [snapshot.draft_ids[position - 1] for position in positions]
    records = _records_by_id(product_store, selected_ids)
    selected: list[DraftSummary] = []
    for position in positions:
        draft_id = snapshot.draft_ids[position - 1]
        item = records.get(draft_id)
        if item is None:
            raise CapabilityInputRequired(
                "DRAFT_QUERY_SNAPSHOT_ITEM_MISSING",
                f"快照中的草稿 {draft_id} 已不存在。",
                key="draft_query",
                label="草稿查询",
                reason="请重新查询草稿后再选择。",
            )
        selected.append(_summary(item, view=snapshot.query.view))
    return selected


def query_drafts(
    request: DraftQueryRequest,
    *,
    product_store: DraftIndexReader,
    snapshot_repository: DraftSnapshotRepository,
) -> DraftQueryResult:
    """执行新查询，或按已有快照稳定重放顺序并解析序号。"""

    if request.positions and not request.snapshot_id:
        raise CapabilityInputRequired(
            "DRAFT_QUERY_SNAPSHOT_REQUIRED",
            "使用草稿序号前必须先有查询快照。",
            key="draft_query",
            label="草稿查询",
            reason="请先查询草稿，再选择第几个草稿。",
        )

    if request.snapshot_id:
        snapshot = _snapshot_or_error(snapshot_repository, request.snapshot_id)
        records = _records_by_id(product_store, snapshot.draft_ids)
        summaries = [
            _summary(records[draft_id], view=snapshot.query.view)
            for draft_id in snapshot.draft_ids
            if draft_id in records
        ]
        selected = resolve_draft_positions(
            snapshot.snapshot_id,
            request.positions,
            product_store=product_store,
            snapshot_repository=snapshot_repository,
        )
        return DraftQueryResult(
            total=snapshot.total,
            items=summaries,
            count_by_platform=snapshot.count_by_platform,
            count_by_status=snapshot.count_by_status,
            snapshot_id=snapshot.snapshot_id,
            selected_items=selected,
        )

    query = DraftQueryCriteria.model_validate(
        request.model_dump(exclude={"snapshot_id", "positions"})
    )
    records: list[dict[str, Any]] = []
    total = 0
    platform_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for item in product_store.iter_drafts_index(query.scope):
        if not isinstance(item, dict) or not _matches(item, query):
            continue
        detail = _summary(item, view="detail")
        total += 1
        _add_counts(detail, platform_counts, status_counts)
        _retain_sorted_item(
            records,
            item,
            sort=query.sort,
            limit=query.limit,
        )
    summaries = [
        _summary(item, view=query.view)
        for item in records
    ]
    serialized_platform_counts = dict(sorted(platform_counts.items()))
    serialized_status_counts = dict(sorted(status_counts.items()))
    snapshot = DraftQuerySnapshot(
        snapshot_id=f"dqs_{uuid4().hex}",
        draft_ids=[item.draft_id for item in summaries],
        total=total,
        count_by_platform=serialized_platform_counts,
        count_by_status=serialized_status_counts,
        query=query,
        created_at=datetime.now(timezone.utc),
    )
    snapshot = DraftQuerySnapshot.model_validate(
        snapshot_repository.save_draft_query_snapshot(snapshot)
    )
    return DraftQueryResult(
        total=total,
        items=summaries,
        count_by_platform=serialized_platform_counts,
        count_by_status=serialized_status_counts,
        snapshot_id=snapshot.snapshot_id,
        selected_items=[],
    )


def resolve_trusted_draft_answer(
    decision: GlobalPlanningDecision,
    *,
    product_store: DraftIndexReader,
    snapshot_repository: DraftSnapshotRepository,
    resolution_scope: AnswerResolutionScope | None = None,
) -> TrustedGlobalAnswer:
    """从查询快照重放当前事实，生成不可由模型覆写的展示答案。"""

    scope = AnswerResolutionScope.model_validate(resolution_scope or {})
    if decision.action != "answer" or decision.answer_kind is None:
        raise BusinessCapabilityError(
            "GLOBAL_ANSWER_DECISION_INVALID",
            "只有结构化只读回答决策可以解析可信答案。",
        )
    snapshot = _snapshot_or_error(
        snapshot_repository,
        decision.query_snapshot_id,
    )
    if decision.answer_kind == "active_draft_count":
        if scope.expected_product_id or scope.expected_target_platform:
            raise BusinessCapabilityError(
                "GLOBAL_ANSWER_COUNT_SCOPE_CONFLICT",
                "活跃草稿总数不能绑定到单个商品或目标平台上下文。",
            )
        if (
            snapshot.query.scope != "active"
            or snapshot.query.target_platform
            or snapshot.query.status
            or snapshot.query.keyword
        ):
            raise BusinessCapabilityError(
                "GLOBAL_ANSWER_ACTIVE_SCOPE_REQUIRED",
                "活跃草稿总数必须引用未附带平台、状态或关键词过滤的 active 查询快照。",
            )
        return resolve_fresh_active_draft_count_answer(
            product_store=product_store,
            snapshot_repository=snapshot_repository,
        )

    position = decision.answer_draft_position
    if position is None:
        if snapshot.total != 1 or len(snapshot.draft_ids) != 1:
            maximum = min(len(snapshot.draft_ids), 10)
            raise CapabilityInputRequired(
                "GLOBAL_ANSWER_DRAFT_AMBIGUOUS",
                "当前查询结果不能唯一指向一个草稿。",
                key="draft_position",
                label="草稿序号",
                reason="请说明要查看第几个草稿的来源与目标市场。",
                options=[str(index) for index in range(1, maximum + 1)],
                input_type="select",
            )
        position = 1
    result = query_drafts(
        DraftQueryRequest(
            snapshot_id=snapshot.snapshot_id,
            positions=[position],
        ),
        product_store=product_store,
        snapshot_repository=snapshot_repository,
    )
    if len(result.selected_items) != 1:
        raise BusinessCapabilityError(
            "GLOBAL_ANSWER_DRAFT_NOT_RESOLVED",
            "查询快照未能解析出唯一草稿。",
        )
    draft = result.selected_items[0]
    if (
        scope.expected_product_id
        and draft.product_id != scope.expected_product_id
    ):
        raise BusinessCapabilityError(
            "GLOBAL_ANSWER_PRODUCT_SCOPE_MISMATCH",
            "查询快照指向的草稿不属于当前任务商品。",
        )
    if (
        scope.expected_target_platform
        and draft.target_platform != scope.expected_target_platform
    ):
        raise BusinessCapabilityError(
            "GLOBAL_ANSWER_PLATFORM_SCOPE_MISMATCH",
            "查询快照指向的草稿目标平台与当前任务上下文不一致。",
        )
    source_text = (
        f"该商品来源于 {draft.source_platform}"
        if draft.source_platform
        else "该商品未记录来源平台"
    )
    target_text = (
        f"当前草稿的目标平台是 {draft.target_platform}"
        if draft.target_platform
        else "当前草稿未记录目标平台"
    )
    site_text = (
        f"目标站点代码为 {draft.target_site}"
        if draft.target_site
        else "未记录目标站点代码"
    )
    evidence_refs = [
        f"draft_query_snapshot:{snapshot.snapshot_id}",
        f"draft:{draft.draft_id}",
    ]
    if draft.product_id:
        evidence_refs.append(f"product:{draft.product_id}")
    return TrustedGlobalAnswer(
        answer_kind="draft_market_context",
        query_snapshot_id=snapshot.snapshot_id,
        message=f"{source_text}；{target_text}，{site_text}。",
        facts={
            "draft_id": draft.draft_id,
            "product_id": draft.product_id,
            "draft_position": position,
            "source_platform": draft.source_platform,
            "target_platform": draft.target_platform,
            "target_platforms": draft.target_platforms,
            "target_site": draft.target_site,
        },
        evidence_refs=evidence_refs,
    )


def resolve_fresh_active_draft_count_answer(
    *,
    product_store: DraftIndexReader,
    snapshot_repository: DraftSnapshotRepository,
) -> TrustedGlobalAnswer:
    """查询此刻的无过滤 active 集合并生成同一新快照上的可信答案。"""

    result = query_drafts(
        DraftQueryRequest(scope="active", view="summary", limit=100),
        product_store=product_store,
        snapshot_repository=snapshot_repository,
    )
    return TrustedGlobalAnswer(
        answer_kind="active_draft_count",
        query_snapshot_id=result.snapshot_id,
        message=f"当前共有 {result.total} 个活跃草稿。",
        facts={
            "scope": "active",
            "active_draft_count": result.total,
        },
        evidence_refs=[f"draft_query_snapshot:{result.snapshot_id}"],
    )


__all__ = [
    "DraftIndexReader",
    "DraftSnapshotRepository",
    "query_drafts",
    "resolve_fresh_active_draft_count_answer",
    "resolve_trusted_draft_answer",
    "resolve_draft_positions",
]
