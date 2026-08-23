# -*- coding: utf-8 -*-
"""旧草稿超大输出回归测试（类目 Schema 分离计划 Phase 0）。

事故草稿 d5c8f09c565e1 的双份 Schema 使 draft_read 输出 344,808 字节，
超过 262,144 字节上限。本测试用同构 fixture 复现该体积，只作为
draft_read 有界化与退役字段拒绝的负向边界基线，不代表受支持的持久格式。
"""

from __future__ import annotations

import json

from tests.category_schema_regression import (
    TOOL_OUTPUT_LIMIT_BYTES,
    build_legacy_draft_with_schema,
    serialized_size,
)


def test_legacy_draft_with_dual_schema_exceeds_tool_output_limit() -> None:
    draft = build_legacy_draft_with_schema(enum_option_count=600)

    first = draft["category_attribute_schema"]
    second = draft["target_sites"][0]["category_attribute_schema"]
    assert first == second, "事故草稿的两份 Schema 完全相同"

    draft_bytes = serialized_size(draft)
    assert draft_bytes > TOOL_OUTPUT_LIMIT_BYTES, (
        f"legacy fixture 必须复现超限体积，实际 {draft_bytes} 字节"
    )

    schema_bytes = serialized_size(first)
    # 事故实测单份约 157,013 字节；fixture 只需同量级即可。
    assert schema_bytes > 100_000


def test_draft_business_payload_without_schema_is_small() -> None:
    draft = build_legacy_draft_with_schema(enum_option_count=600)

    business_only = {
        key: value
        for key, value in draft.items()
        if key != "category_attribute_schema"
    }
    business_only["target_sites"] = [
        {
            key: value
            for key, value in site.items()
            if key != "category_attribute_schema"
        }
        for site in draft["target_sites"]
    ]

    assert serialized_size(business_only) < 4_096, (
        "删除平台规则副本后，草稿应回到业务数据量级"
    )


def test_legacy_schema_json_roundtrip_stable() -> None:
    draft = build_legacy_draft_with_schema(enum_option_count=10)
    text = json.dumps(draft, ensure_ascii=False)
    assert json.loads(text) == draft
