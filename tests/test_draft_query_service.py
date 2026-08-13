from __future__ import annotations

from copy import deepcopy

import pytest

from erp_web.schemas.draft_capabilities import (
    DraftQueryRequest,
    DraftQuerySnapshot,
)
from erp_web.services.capability_errors import CapabilityInputRequired
from erp_web.services.draft_query_service import query_drafts


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
        DraftQueryRequest(scope="active", platform="ozon"),
        product_store=drafts,
        snapshot_repository=snapshots,
    )

    assert result.total == 2
    assert [item.draft_id for item in result.items] == ["d2", "d1"]
    assert result.count_by_platform == {"ozon": 2}
    assert result.count_by_status == {"claimed": 1, "ready_to_publish": 1}
    saved = snapshots.items[result.snapshot_id]
    assert saved.draft_ids == ["d2", "d1"]
    assert saved.query.platform == "ozon"


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
