from __future__ import annotations

"""受限草稿查询与基于快照的一基序号解析。"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from erp_web.schemas.draft_capabilities import (
    DraftPublishReadiness,
    DraftQueryCriteria,
    DraftQueryRequest,
    DraftQueryResult,
    DraftQuerySnapshot,
    DraftSummary,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


class DraftIndexReader(Protocol):
    def load_drafts_index(self, scope: str = "active") -> list[dict[str, Any]]:
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
        platform=_text(item.get("platform")).lower(),
        platforms=_platforms(item),
        site=_text(item.get("site")),
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
        "platform": summary.platform,
        "platforms": summary.platforms,
        "site": summary.site,
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
    platform = query.platform.lower()
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


def _counts(items: list[DraftSummary]) -> tuple[dict[str, int], dict[str, int]]:
    platform_counts: Counter[str] = Counter()
    for item in items:
        platforms = list(dict.fromkeys(item.platforms or [item.platform]))
        if not platforms:
            platforms = ["unknown"]
        platform_counts.update(platform or "unknown" for platform in platforms)
    status_counts = Counter(
        item.readiness.workflow_status or item.readiness.publish_status or "unknown"
        for item in items
    )
    return dict(sorted(platform_counts.items())), dict(sorted(status_counts.items()))


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
    records = {
        _text(item.get("draft_id")): item
        for item in product_store.load_drafts_index("all")
        if _text(item.get("draft_id"))
    }
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
        records = {
            _text(item.get("draft_id")): item
            for item in product_store.load_drafts_index("all")
            if _text(item.get("draft_id"))
        }
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
    records = [
        item
        for item in product_store.load_drafts_index(query.scope)
        if isinstance(item, dict) and _matches(item, query)
    ]
    records = _sort_items(records, query.sort)
    total = len(records)
    summaries = [
        _summary(item, view=query.view)
        for item in records[: query.limit]
    ]
    platform_counts, status_counts = _counts(
        [_summary(item, view="detail") for item in records]
    )
    snapshot = DraftQuerySnapshot(
        snapshot_id=f"dqs_{uuid4().hex}",
        draft_ids=[item.draft_id for item in summaries],
        total=total,
        count_by_platform=platform_counts,
        count_by_status=status_counts,
        query=query,
        created_at=datetime.now(timezone.utc),
    )
    snapshot = DraftQuerySnapshot.model_validate(
        snapshot_repository.save_draft_query_snapshot(snapshot)
    )
    return DraftQueryResult(
        total=total,
        items=summaries,
        count_by_platform=platform_counts,
        count_by_status=status_counts,
        snapshot_id=snapshot.snapshot_id,
        selected_items=[],
    )


__all__ = [
    "DraftIndexReader",
    "DraftSnapshotRepository",
    "query_drafts",
    "resolve_draft_positions",
]
