from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from erp_web.schemas.draft_capabilities import (
    DraftQueryRequest,
    DraftQuerySnapshot,
)
from erp_web.schemas.global_tasks import (
    AnswerResolutionScope,
    GlobalPlanningDecision,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.services.draft_query_service import (
    query_drafts,
    resolve_trusted_draft_answer,
)


class _Drafts:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def load_drafts_index(self, scope: str = "active") -> list[dict]:
        records = deepcopy(self.records)
        if scope == "published":
            return [item for item in records if item["status"] == "published"]
        if scope == "active":
            return [item for item in records if item["status"] != "published"]
        return records

    def iter_drafts_index(self, scope: str = "active") -> Iterator[dict]:
        yield from self.load_drafts_index(scope)


class _Snapshots:
    def __init__(self) -> None:
        self.items: dict[str, DraftQuerySnapshot] = {}

    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        self.items[snapshot.snapshot_id] = snapshot
        return snapshot

    def load_draft_query_snapshot(
        self,
        snapshot_id: str,
    ) -> DraftQuerySnapshot | None:
        return self.items.get(snapshot_id)


def _record(
    draft_id: str,
    *,
    created_at: str,
    platform: str = "ozon",
    status: str = "claimed",
    title: str = "",
) -> dict:
    return {
        "draft_id": draft_id,
        "product_id": f"product-{draft_id}",
        "source_platform": "1688",
        "platform": platform,
        "platforms": [platform],
        "site": "global" if platform == "ozon" else "MLM",
        "status": status,
        "publish_status": "ready" if status == "ready_to_publish" else "",
        "title": title or f"Title {draft_id}",
        "product_title": f"Product {draft_id}",
        "created_at": created_at,
        "updated_at": created_at,
        "raw": {
            "description": f"Description {draft_id}",
            "attributes": {"BRAND": "Generic"},
            "images": [{"asset_id": f"asset-{draft_id}"}],
            "last_precheck": {
                "ok": status == "ready_to_publish",
                "errors": [],
                "warnings": [],
            },
        },
    }


def test_query_drafts_filters_sorts_counts_and_saves_snapshot() -> None:
    drafts = _Drafts(
        [
            _record("d1", created_at="2026-08-01T00:00:00Z"),
            _record(
                "d2",
                created_at="2026-08-03T00:00:00Z",
                status="ready_to_publish",
            ),
            _record(
                "d3",
                created_at="2026-08-02T00:00:00Z",
                platform="mercadolibre",
            ),
            _record(
                "d4",
                created_at="2026-08-04T00:00:00Z",
                status="published",
            ),
        ]
    )
    snapshots = _Snapshots()

    result = query_drafts(
        DraftQueryRequest(scope="active", target_platform="ozon"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    assert result.total == 2
    assert [item.draft_id for item in result.items] == ["d2", "d1"]
    assert result.count_by_platform == {"ozon": 2}
    assert result.count_by_status == {"claimed": 1, "ready_to_publish": 1}
    saved = snapshots.items[result.snapshot_id]
    assert saved.draft_ids == ["d2", "d1"]
    assert saved.query.target_platform == "ozon"
    assert result.items[0].source_platform == "1688"
    assert result.items[0].target_platform == "ozon"
    assert result.items[0].target_platforms == ["ozon"]
    assert result.items[0].target_site == "global"


def test_query_drafts_streams_past_legacy_index_limit_with_complete_counts() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = [
        _record(
            f"d{index:03d}",
            created_at=(start + timedelta(seconds=index)).isoformat(),
            platform="mercadolibre" if index == 5 else "ozon",
            status="ready_to_publish" if index == 5 else "claimed",
            title="Tail Needle" if index == 5 else f"Title {index:03d}",
        )
        for index in reversed(range(520))
    ]
    drafts = _Drafts(records)

    result = query_drafts(
        DraftQueryRequest(scope="all", sort="created_asc", limit=3),
        product_store=drafts,
        snapshot_repository=_Snapshots(),
    )

    assert result.total == 520
    assert [item.draft_id for item in result.items] == ["d000", "d001", "d002"]
    assert result.count_by_platform == {"mercadolibre": 1, "ozon": 519}
    assert result.count_by_status == {"claimed": 519, "ready_to_publish": 1}

    latest = query_drafts(
        DraftQueryRequest(scope="all", sort="created_desc", limit=2),
        product_store=drafts,
        snapshot_repository=_Snapshots(),
    )
    assert [item.draft_id for item in latest.items] == ["d519", "d518"]

    by_title = query_drafts(
        DraftQueryRequest(scope="all", sort="title_asc", limit=2),
        product_store=drafts,
        snapshot_repository=_Snapshots(),
    )
    assert [item.draft_id for item in by_title.items] == ["d005", "d000"]

    filtered = query_drafts(
        DraftQueryRequest(scope="all", keyword="tail needle", limit=1),
        product_store=drafts,
        snapshot_repository=_Snapshots(),
    )

    assert filtered.total == 1
    assert [item.draft_id for item in filtered.items] == ["d005"]


def test_legacy_snapshot_query_platform_reads_as_target_platform() -> None:
    criteria = DraftQuerySnapshot.model_validate(
        {
            "snapshot_id": "legacy-snapshot",
            "draft_ids": [],
            "total": 0,
            "count_by_platform": {},
            "count_by_status": {},
            "query": {"scope": "active", "platform": "ozon"},
            "created_at": "2026-08-13T00:00:00Z",
        }
    ).query

    assert criteria.target_platform == "ozon"
    assert "platform" not in criteria.model_dump()


def test_query_drafts_counts_every_platform_on_multi_market_draft() -> None:
    record = _record("d1", created_at="2026-08-01T00:00:00Z")
    record["platforms"] = ["ozon", "mercadolibre"]

    result = query_drafts(
        DraftQueryRequest(scope="all"),
        product_store=_Drafts([record]),
        snapshot_repository=_Snapshots(),
    )

    assert result.total == 1
    assert result.count_by_platform == {"mercadolibre": 1, "ozon": 1}


def test_query_drafts_applies_view_projection_and_replays_it_from_snapshot() -> None:
    record = _record(
        "d1",
        created_at="2026-08-01T00:00:00Z",
        status="ready_to_publish",
    )
    record.update(
        {
            "language": "ru",
            "category_id": "cat-1",
            "category_path": "Root > Leaf",
        }
    )
    snapshots = _Snapshots()

    summary = query_drafts(
        DraftQueryRequest(scope="all", view="summary"),
        product_store=_Drafts([record]),
        snapshot_repository=snapshots,
    )
    item = summary.items[0]
    assert item.title == "Title d1"
    assert item.category_id == ""
    assert item.readiness.workflow_status == "ready_to_publish"
    assert item.readiness.image_count == 0

    replay = query_drafts(
        DraftQueryRequest(snapshot_id=summary.snapshot_id),
        product_store=_Drafts([record]),
        snapshot_repository=snapshots,
    )
    assert replay.items[0] == item

    readiness = query_drafts(
        DraftQueryRequest(scope="all", view="publish_readiness"),
        product_store=_Drafts([record]),
        snapshot_repository=_Snapshots(),
    ).items[0]
    assert readiness.category_id == "cat-1"
    assert readiness.category_path == ""
    assert readiness.has_description is True
    assert readiness.readiness.image_count == 1

    detail = query_drafts(
        DraftQueryRequest(scope="all", view="detail"),
        product_store=_Drafts([record]),
        snapshot_repository=_Snapshots(),
    ).items[0]
    assert detail.category_path == "Root > Leaf"
    assert detail.product_title == "Product d1"


def test_snapshot_positions_keep_original_identity_after_index_reorders() -> None:
    drafts = _Drafts(
        [
            _record("d1", created_at="2026-08-01T00:00:00Z"),
            _record("d2", created_at="2026-08-02T00:00:00Z"),
        ]
    )
    snapshots = _Snapshots()
    initial = query_drafts(
        DraftQueryRequest(scope="all"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    assert [item.draft_id for item in initial.items] == ["d2", "d1"]

    drafts.records.insert(
        0,
        _record("d3", created_at="2026-08-03T00:00:00Z"),
    )
    selected = query_drafts(
        DraftQueryRequest(snapshot_id=initial.snapshot_id, positions=[2]),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    assert [item.draft_id for item in selected.items] == ["d2", "d1"]
    assert [item.draft_id for item in selected.selected_items] == ["d1"]


def test_snapshot_keeps_total_and_aggregates_beyond_item_limit() -> None:
    drafts = _Drafts(
        [
            _record("d1", created_at="2026-08-01T00:00:00Z"),
            _record("d2", created_at="2026-08-02T00:00:00Z"),
        ]
    )
    snapshots = _Snapshots()
    initial = query_drafts(
        DraftQueryRequest(scope="all", limit=1),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    assert initial.total == 2
    assert len(initial.items) == 1

    drafts.records = []
    replay = query_drafts(
        DraftQueryRequest(snapshot_id=initial.snapshot_id),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    assert replay.total == 2
    assert replay.count_by_platform == {"ozon": 2}
    assert replay.count_by_status == {"claimed": 2}


def test_positions_without_snapshot_request_a_new_query() -> None:
    with pytest.raises(CapabilityInputRequired) as exc_info:
        query_drafts(
            DraftQueryRequest(positions=[1]),
            product_store=_Drafts([]),
            snapshot_repository=_Snapshots(),
        )

    assert exc_info.value.code == "DRAFT_QUERY_SNAPSHOT_REQUIRED"
    assert exc_info.value.key == "draft_query"


def test_snapshot_out_of_range_returns_explicit_position_requirement() -> None:
    drafts = _Drafts([_record("d1", created_at="2026-08-01T00:00:00Z")])
    snapshots = _Snapshots()
    initial = query_drafts(
        DraftQueryRequest(scope="all"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    with pytest.raises(CapabilityInputRequired) as exc_info:
        query_drafts(
            DraftQueryRequest(snapshot_id=initial.snapshot_id, positions=[2]),
            product_store=drafts,
            snapshot_repository=snapshots,
        )

    assert exc_info.value.code == "DRAFT_QUERY_POSITION_OUT_OF_RANGE"
    assert exc_info.value.options == ("1",)
    assert exc_info.value.input_type == "string_list"


def test_trusted_count_answer_uses_a_fresh_unfiltered_active_snapshot() -> None:
    drafts = _Drafts([_record("d1", created_at="2026-08-01T00:00:00Z")])
    snapshots = _Snapshots()
    old_query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )
    drafts.records.append(
        _record("d2", created_at="2026-08-02T00:00:00Z")
    )

    answer = resolve_trusted_draft_answer(
        GlobalPlanningDecision(
            action="answer",
            answer_kind="active_draft_count",
            query_snapshot_id=old_query.snapshot_id,
        ),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    assert answer.message == "当前共有 2 个活跃草稿。"
    assert answer.facts == {"scope": "active", "active_draft_count": 2}
    assert old_query.total == 1
    assert answer.query_snapshot_id != old_query.snapshot_id
    assert snapshots.items[answer.query_snapshot_id].total == 2
    assert answer.evidence_refs == [
        f"draft_query_snapshot:{answer.query_snapshot_id}"
    ]


def test_trusted_count_answer_rejects_filtered_active_snapshot() -> None:
    drafts = _Drafts(
        [
            _record(
                "d1",
                platform="ozon",
                created_at="2026-08-01T00:00:00Z",
            )
        ]
    )
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active", target_platform="ozon"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    with pytest.raises(BusinessCapabilityError) as exc_info:
        resolve_trusted_draft_answer(
            GlobalPlanningDecision(
                action="answer",
                answer_kind="active_draft_count",
                query_snapshot_id=query.snapshot_id,
            ),
            product_store=drafts,
            snapshot_repository=snapshots,
        )

    assert exc_info.value.code == "GLOBAL_ANSWER_ACTIVE_SCOPE_REQUIRED"


def test_trusted_market_answer_separates_source_target_and_keeps_site_as_code() -> None:
    record = _record(
        "d1",
        created_at="2026-08-01T00:00:00Z",
        platform="mercadolibre",
    )
    record["site"] = "CBT"
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=_Drafts([record]),
        snapshot_repository=snapshots,
    )

    answer = resolve_trusted_draft_answer(
        GlobalPlanningDecision(
            action="answer",
            answer_kind="draft_market_context",
            query_snapshot_id=query.snapshot_id,
        ),
        product_store=_Drafts([record]),
        snapshot_repository=snapshots,
    )

    assert answer.message == (
        "该商品来源于 1688；当前草稿的目标平台是 mercadolibre，"
        "目标站点代码为 CBT。"
    )
    assert answer.facts["source_platform"] == "1688"
    assert answer.facts["target_platform"] == "mercadolibre"
    assert answer.facts["target_site"] == "CBT"
    assert answer.query_snapshot_id == query.snapshot_id
    assert "Cross-Border" not in answer.message


@pytest.mark.parametrize(
    ("scope", "expected_code"),
    [
        (
            AnswerResolutionScope(expected_product_id="product-other"),
            "GLOBAL_ANSWER_PRODUCT_SCOPE_MISMATCH",
        ),
        (
            AnswerResolutionScope(expected_target_platform="ozon"),
            "GLOBAL_ANSWER_PLATFORM_SCOPE_MISMATCH",
        ),
    ],
)
def test_trusted_market_answer_rejects_task_scope_mismatch(
    scope: AnswerResolutionScope,
    expected_code: str,
) -> None:
    record = _record(
        "d1",
        created_at="2026-08-01T00:00:00Z",
        platform="mercadolibre",
    )
    drafts = _Drafts([record])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    with pytest.raises(BusinessCapabilityError) as exc_info:
        resolve_trusted_draft_answer(
            GlobalPlanningDecision(
                action="answer",
                answer_kind="draft_market_context",
                query_snapshot_id=query.snapshot_id,
            ),
            product_store=drafts,
            snapshot_repository=snapshots,
            resolution_scope=scope,
        )

    assert exc_info.value.code == expected_code


def test_trusted_count_answer_rejects_product_or_platform_task_scope() -> None:
    drafts = _Drafts([_record("d1", created_at="2026-08-01T00:00:00Z")])
    snapshots = _Snapshots()
    query = query_drafts(
        DraftQueryRequest(scope="active"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    with pytest.raises(BusinessCapabilityError) as exc_info:
        resolve_trusted_draft_answer(
            GlobalPlanningDecision(
                action="answer",
                answer_kind="active_draft_count",
                query_snapshot_id=query.snapshot_id,
            ),
            product_store=drafts,
            snapshot_repository=snapshots,
            resolution_scope=AnswerResolutionScope(
                expected_product_id="product-d1",
            ),
        )

    assert exc_info.value.code == "GLOBAL_ANSWER_COUNT_SCOPE_CONFLICT"
