# -*- coding: utf-8 -*-
"""Agent 工具有界输出验收测试（计划 §9 / §13.4）。

覆盖：
1. ``category_attributes_query`` 输出不含 raw/完整 values，分页字段正确；
2. 构造 600 个枚举值时，``category_attribute_values_query`` 只返回当前页；
3. 构造 600 个枚举值的草稿，``draft_read`` 视图远小于 256 KiB。
"""

from __future__ import annotations

import json

import pytest

from erp_web.runtime_units.category_query_capabilities import (
    CategoryQueryCapabilityScope,
    category_attributes_query,
    category_attribute_values_query,
)
from erp_web.runtime_units.product_write_capabilities import (
    _ai_draft_read_view,
)
from erp_web.schemas.category_query_capabilities import (
    CategoryAttributesQueryRequest,
    CategoryAttributeValuesQueryRequest,
)

from tests.category_schema_regression import TOOL_OUTPUT_LIMIT_BYTES


def _execution(deadline_seconds: float = 60):
    from datetime import datetime, timedelta, timezone

    from erp_web.schemas.ai_trace import AiExecutionContext

    return AiExecutionContext(
        task_run_id="task-bounded",
        attempt_id="attempt-bounded",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
        budget_profile="test",
    )


def test_category_attributes_query_is_paged_and_bounded() -> None:
    many_attributes = [
        {"id": str(1000 + index), "name": f"属性{index}"} for index in range(120)
    ]

    def attributes_loader(
        platform, category_id, site="", *, cursor="", limit=50, timeout_seconds=None
    ):
        offset = int(str(cursor).removeprefix("offset:") or 0) if cursor else 0
        window = many_attributes[offset : offset + limit]
        has_more = offset + limit < len(many_attributes)
        return {
            "ok": True,
            "platform": platform,
            "site": site,
            "category_id": category_id,
            "category_path": "测试 / 类目",
            "attributes": window,
            "next_cursor": f"offset:{offset + limit}" if has_more else "",
            "has_more": has_more,
        }

    scope = CategoryQueryCapabilityScope(
        searcher=lambda *args, **kwargs: [],
        attributes_loader=attributes_loader,
        attribute_values_loader=lambda *args, **kwargs: {"values": []},
        record_loader=lambda *args, **kwargs: {},
        draft_context_loader=lambda body: ({}, {"error": "no"}, 404),
        product_loader=lambda body: ({"product_id": "p"}, None, 200),
    )

    first = category_attributes_query(
        CategoryAttributesQueryRequest(category_id="94765", limit=50),
        scope=scope,
        execution=_execution(),
    )
    assert len(first.attributes) == 50
    assert first.has_more is True
    assert first.next_cursor == "offset:50"

    second = category_attributes_query(
        CategoryAttributesQueryRequest(
            category_id="94765", limit=50, cursor=first.next_cursor
        ),
        scope=scope,
        execution=_execution(),
    )
    assert len(second.attributes) == 50
    assert second.cursor == "offset:50"

    # 输出不得携带 raw / platform_binding / 完整 values。
    serialized = json.dumps(first.model_dump(mode="json"), ensure_ascii=False)
    assert "platform_binding" not in serialized
    assert '"raw"' not in serialized


def test_category_attribute_values_query_returns_single_page_of_600() -> None:
    all_values = [
        {"id": str(30_000_000 + index), "value": f"候选{index}"} for index in range(600)
    ]

    def values_loader(
        platform,
        category_id,
        attribute_id,
        site="",
        query="",
        cursor="",
        limit=50,
        timeout_seconds=None,
    ):
        offset = int(str(cursor).removeprefix("offset:") or 0) if cursor else 0
        window = all_values[offset : offset + limit]
        has_more = offset + limit < len(all_values)
        return {
            "ok": True,
            "category_id": category_id,
            "attribute_id": attribute_id,
            "values": window,
            "next_cursor": f"offset:{offset + limit}" if has_more else "",
            "has_more": has_more,
        }

    scope = CategoryQueryCapabilityScope(
        searcher=lambda *args, **kwargs: [],
        attributes_loader=lambda *args, **kwargs: {"attributes": []},
        attribute_values_loader=values_loader,
        record_loader=lambda *args, **kwargs: {},
        draft_context_loader=lambda body: ({}, {"error": "no"}, 404),
        product_loader=lambda body: ({"product_id": "p"}, None, 200),
    )

    page = category_attribute_values_query(
        CategoryAttributeValuesQueryRequest(
            category_id="94765", attribute_id="10096", limit=50
        ),
        scope=scope,
        execution=_execution(),
    )
    assert len(page.values) == 50
    assert page.has_more is True
    assert page.next_cursor == "offset:50"

    serialized = json.dumps(page.model_dump(mode="json"), ensure_ascii=False)
    assert len(serialized.encode("utf-8")) < 32_768


def test_draft_read_view_stays_small_with_600_value_attribute() -> None:
    draft = {
        "draft_id": "draft-bounded",
        "product_id": "product-bounded",
        "platform": "yandex",
        "site": "global",
        "status": "in_progress",
        "category_id": "16088928",
        "attributes": {
            "10096": {
                "values": [
                    {"dictionary_value_id": str(30_000_000 + i), "value": f"候选{i}"}
                    for i in range(600)
                ]
            },
            **{f"attr-{i}": f"值{i}" for i in range(300)},
        },
        "images": [{"asset_id": f"img-{i}"} for i in range(100)],
    }

    view = _ai_draft_read_view(draft)
    serialized = json.dumps(
        view.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    assert view.draft_id == "draft-bounded"
    assert view.image_count == 100
    # 300 个普通属性被限制到前 200 个；600 值列表被限制到前 20 项。
    assert len(view.attributes) == 200
    assert len(view.attributes["10096"]["values"]) == 20
    assert len(serialized) < TOOL_OUTPUT_LIMIT_BYTES // 4
