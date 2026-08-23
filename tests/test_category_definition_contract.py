# -*- coding: utf-8 -*-
"""统一类目定义契约测试（类目 Schema 分离计划 Phase 0）。

覆盖：
- CategoryDefinition 及其属性模型拒绝未知字段（raw/values 不得混入）；
- 公共有界视图不含 platform_binding/raw；
- 稳定指纹：相同语义定义跨进程产生相同指纹，缓存时间戳与 options 预览
  变化不影响指纹，语义变化使指纹变化。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from erp_web.schemas.category_definition import (
    ATTRIBUTE_OPTIONS_PREVIEW_LIMIT,
    CategoryAttributeDefinition,
    CategoryAttributePage,
    CategoryAttributePlatformBinding,
    CategoryAttributeSummary,
    CategoryAttributeValuePage,
    CategoryCacheState,
    CategoryDefinition,
    CategoryUnitOption,
    definition_fingerprint,
)

APP_DIR = Path(__file__).resolve().parents[1]


def _attribute(
    attribute_id: str = "1001",
    **overrides: object,
) -> CategoryAttributeDefinition:
    payload: dict[str, object] = {
        "id": attribute_id,
        "name": "颜色",
        "required": True,
        "value_type": "enum",
        "value_mode": "multiple",
        "allow_custom_values": False,
        "dictionary_id": "888",
        "is_dictionary": True,
        "is_collection": True,
        "max_value_count": 5,
        "platform_binding": CategoryAttributePlatformBinding(
            complex_id="0",
            aspect="required",
            platform_type="String",
        ),
    }
    payload.update(overrides)
    return CategoryAttributeDefinition(**payload)  # type: ignore[arg-type]


def _definition(**overrides: object) -> CategoryDefinition:
    payload: dict[str, object] = {
        "platform": "ozon",
        "site": "global",
        "category_id": "17028922_971082156",
        "category_path": "电子产品 > 配件",
        "description_category_id": "17028922",
        "required": (_attribute("1001"),),
        "optional": (_attribute("2002", name="尺寸", required=False),),
    }
    payload.update(overrides)
    return CategoryDefinition(**payload)  # type: ignore[arg-type]


# -- 契约形状 ----------------------------------------------------------------


def test_definition_rejects_raw_and_full_values_fields() -> None:
    with pytest.raises(ValidationError):
        CategoryDefinition(  # type: ignore[call-arg]
            platform="ozon",
            category_id="1",
            raw={"anything": True},
        )
    with pytest.raises(ValidationError):
        CategoryAttributeDefinition(  # type: ignore[call-arg]
            id="1",
            values=[{"value": "蓝色"}],
        )
    with pytest.raises(ValidationError):
        CategoryAttributeDefinition(  # type: ignore[call-arg]
            id="1",
            raw_values=["蓝色"],
        )


def test_cache_state_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CategoryCacheState(source="live", fetched_at="2026-08-22")  # type: ignore[call-arg]


def test_public_summary_excludes_platform_binding_and_raw() -> None:
    fields = set(CategoryAttributeSummary.model_fields)
    assert "platform_binding" not in fields
    assert "raw" not in fields
    assert "constraints" not in fields
    page_fields = set(CategoryAttributePage.model_fields)
    assert {"limit", "cursor", "next_cursor", "has_more"} <= page_fields
    value_page_fields = set(CategoryAttributeValuePage.model_fields)
    assert {"limit", "cursor", "next_cursor", "has_more"} <= value_page_fields


def test_options_preview_is_bounded_documented() -> None:
    assert ATTRIBUTE_OPTIONS_PREVIEW_LIMIT >= 1


# -- 指纹稳定性 ----------------------------------------------------------------


def test_fingerprint_ignores_cache_and_preview_noise() -> None:
    base = _definition()
    noisy = _definition(
        cache=CategoryCacheState(
            source="stale",
            stale=True,
            retrieved_at="2026-08-22T00:00:00Z",
            expires_at="2026-08-23T00:00:00Z",
            stale_until="2026-08-29T00:00:00Z",
        ),
        fingerprint="ignored-input",
    )
    assert definition_fingerprint(base) == definition_fingerprint(noisy)


def test_fingerprint_ignores_attribute_order_and_preview_options() -> None:
    base = _definition()
    reordered = _definition(
        required=(_attribute("2002", name="尺寸", required=False),),
        optional=(_attribute("1001"),),
    )
    # 排序/元组位置噪声不得影响指纹；语义由属性自身字段表达。
    assert definition_fingerprint(base) == definition_fingerprint(reordered)

    required_flag_changed = _definition(
        required=(_attribute("1001", required=False),),
    )
    assert definition_fingerprint(base) != definition_fingerprint(
        required_flag_changed
    )

    preview_only = _definition(
        required=(
            _attribute(
                "1001",
                options=(
                    {"value": "蓝色", "dictionary_value_id": "1"},
                ),
                has_more_values=True,
            ),
        ),
    )
    assert definition_fingerprint(base) == definition_fingerprint(preview_only)


def test_fingerprint_changes_with_semantic_definition() -> None:
    base = _definition()
    changed_attribute = _definition(
        required=(_attribute("1001", allow_custom_values=True),),
    )
    assert definition_fingerprint(base) != definition_fingerprint(changed_attribute)

    changed_binding = _definition(
        required=(
            _attribute(
                "1001",
                platform_binding=CategoryAttributePlatformBinding(
                    complex_id="9090",
                    aspect="required",
                    platform_type="String",
                ),
            ),
        ),
    )
    assert definition_fingerprint(base) != definition_fingerprint(changed_binding)

    changed_identity = _definition(category_id="other")
    assert definition_fingerprint(base) != definition_fingerprint(changed_identity)


def test_fingerprint_stable_across_processes() -> None:
    snippet = (
        "from erp_web.schemas.category_definition import ("
        "CategoryAttributeDefinition, CategoryDefinition, "
        "definition_fingerprint);"
        "definition = CategoryDefinition(platform='yandex', site='global', "
        "category_id='16088928', "
        "required=(CategoryAttributeDefinition(id='14871214', name='颜色', "
        "required=True, value_type='enum', value_mode='single', "
        "dictionary_id='124413209', is_dictionary=True, "
        "unit_options=(), unit_ids=()),));"
        "print(definition_fingerprint(definition))"
    )
    outputs = set()
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=str(APP_DIR),
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, f"跨进程指纹不一致：{sorted(outputs)}"
    (fingerprint,) = outputs
    assert len(fingerprint) == 64


def test_fingerprint_serialization_is_deterministic() -> None:
    definition = _definition()
    first = json.dumps(definition.model_dump(mode="json"), sort_keys=True)
    second = json.dumps(definition.model_dump(mode="json"), sort_keys=True)
    assert first == second
