# -*- coding: utf-8 -*-
"""Agent 工具有界输出验收测试（计划 §9 / §13.4）。"""

from __future__ import annotations

import json

from erp_web.runtime_units.product_write_capabilities import _ai_draft_read_view

TOOL_OUTPUT_LIMIT_BYTES = 262_144


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
