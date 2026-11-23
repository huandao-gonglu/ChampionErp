# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Any

from erp_web.schemas.category_brand import (
    is_brand_attribute_id,
    is_official_no_brand_value,
    no_brand_query_term,
    normalize_attribute_value_query,
)

from .category_catalog import get_category_catalog
from .category_definition_support import definition_to_legacy_attribute
from .category_localization import enrich_category_candidates_l2
from .category_providers import require_category_provider
from .category_searchers import create_category_searcher


logger = logging.getLogger(__name__)


# 所有平台类目事实统一经 CategoryCatalog 读取；返回的 legacy record 不再携带
# 平台 raw 报文或完整枚举 values。展示层中文字段（name_zh / path_zh / label_zh）
# 严格与发布值分离，仅通过 L1 官方双语与 L2 服务端词典零耗时回填。


def fetch_category_record(
    platform: str,
    category_id: str,
    site: str = "",
    include_attributes: bool = False,
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """经 Catalog 读取类目记录（属性只含规范化有界字段；include_attributes 不重复查详情）。"""

    platform = str(platform or "").strip().lower()
    provider = require_category_provider(platform)
    catalog = get_category_catalog()

    if include_attributes:
        definition = catalog.attribute_definitions(
            platform,
            category_id,
            site=site,
            timeout_seconds=timeout_seconds,
            force_refresh=force_refresh,
        )
        path_segments = [
            segment.strip()
            for segment in str(definition.category_path or "").split("/")
            if segment.strip()
        ]
        resolved_id = definition.category_id or str(category_id or "").strip()
        name_original = path_segments[-1] if path_segments else resolved_id
        ancestors = (
            list(catalog.cached_category_ancestor_ids(platform, resolved_id))
            if platform == "yandex"
            else []
        )
        return {
            "platform": platform,
            "site": definition.site or provider.resolve_site(site),
            "category_id": resolved_id,
            "name_original": name_original,
            "category_path": definition.category_path,
            "path_zh": definition.category_path_zh,
            "path_original": path_segments or ([name_original] if name_original else []),
            "parent_id": ancestors[-1] if ancestors else "",
            "ancestor_ids": ancestors,
            "description_category_id": definition.description_category_id,
            "type_id": resolved_id if platform == "ozon" else "",
            "is_leaf": True,
            "source": f"{platform}_live",
            "attributes": {
                "required": [
                    definition_to_legacy_attribute(attribute)
                    for attribute in definition.required
                ],
                "optional": [
                    definition_to_legacy_attribute(attribute)
                    for attribute in definition.optional
                ],
            },
        }

    detail = catalog.category_detail(
        platform,
        category_id,
        site=site,
        timeout_seconds=timeout_seconds,
    )
    resolved_id = detail.category_id or str(category_id or "").strip()
    return {
        "platform": platform,
        "site": detail.site or provider.resolve_site(site),
        "category_id": resolved_id,
        "name_original": detail.name,
        "name_zh": detail.name_zh,
        "category_path": detail.path,
        "path_zh": detail.path_zh,
        "path_original": [
            segment.strip() for segment in detail.path.split("/") if segment.strip()
        ]
        or ([detail.name] if detail.name else []),
        "parent_id": detail.parent_id,
        "ancestor_ids": list(detail.ancestor_ids),
        "description_category_id": detail.description_category_id,
        "type_id": resolved_id if platform == "ozon" else "",
        "is_leaf": detail.is_leaf,
        "source": f"{platform}_live",
        "attributes": {"required": [], "optional": []},
    }


def fetch_category_attributes(
    platform: str,
    category_id: str,
    site: str = "",
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    record = fetch_category_record(
        platform,
        category_id,
        site=site,
        include_attributes=True,
        timeout_seconds=timeout_seconds,
        force_refresh=force_refresh,
    )
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    required = list(attrs.get("required") or [])
    optional = list(attrs.get("optional") or [])
    return {
        "ok": True,
        "platform": platform,
        "site": record.get("site") or "",
        "source": f"{platform}_live",
        "category": record,
        "required": required,
        "optional": optional,
        "attributes": required + optional,
        "category_id": record.get("category_id") or category_id,
        "category_path": str(record.get("category_path") or ""),
        "path": str(record.get("category_path") or ""),
    }


def fetch_category_attribute_page(
    platform: str,
    category_id: str,
    site: str = "",
    *,
    cursor: str = "",
    limit: int = 50,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """前端/Agent 的有界属性页（不含 platform_binding/raw/完整枚举，带 L2 中文回填）。"""

    catalog = get_category_catalog()
    page = catalog.public_attribute_page(
        platform,
        category_id,
        site=site,
        cursor=cursor,
        limit=limit,
        timeout_seconds=timeout_seconds,
        force_refresh=force_refresh,
    )
    payload = page.model_dump(mode="json")
    payload["ok"] = True
    return payload


def fetch_category_attribute_values(
    platform: str,
    category_id: str,
    attribute_id: str,
    site: str = "",
    *,
    query: str = "",
    cursor: str = "",
    limit: int = 50,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    category_id = str(category_id or "").strip()
    attribute_id = str(attribute_id or "").strip()
    site = str(site or "").strip()
    raw_query = str(query or "").strip()
    raw_cursor = str(cursor or "").strip()
    effective_query = normalize_attribute_value_query(
        platform,
        attribute_id,
        raw_query,
    )
    catalog = get_category_catalog()
    page = catalog.attribute_values(
        platform,
        category_id,
        attribute_id,
        site=site,
        query=effective_query,
        cursor=raw_cursor,
        limit=limit,
        timeout_seconds=timeout_seconds,
    )
    values = [
        {
            "id": value.dictionary_value_id,
            "value": value.value,
            **({"label_zh": value.label_zh} if value.label_zh else {}),
            "info": "",
            "picture": "",
        }
        for value in page.values
    ]
    next_cursor = page.next_cursor
    has_more = page.has_more

    # 品牌大字典的官方“无品牌”值常不在第一页。空查询首屏额外执行一次
    # 平台原文检索并置顶真实候选；ID 始终来自当前类目的 Provider 结果，
    # 不使用跨类目硬编码兜底。后续页仍沿用原字典游标。
    if (
        not raw_query
        and not raw_cursor
        and is_brand_attribute_id(platform, attribute_id)
        and not any(
            is_official_no_brand_value(platform, item.get("value"))
            for item in values
        )
    ):
        term = no_brand_query_term(platform)
        pinned: dict[str, str] | None = None
        if term:
            try:
                pinned_page = catalog.attribute_values(
                    platform,
                    category_id,
                    attribute_id,
                    site=site,
                    query=term,
                    cursor="",
                    limit=20,
                    timeout_seconds=(
                        min(float(timeout_seconds), 15.0)
                        if timeout_seconds is not None
                        else 15.0
                    ),
                )
                for candidate in pinned_page.values:
                    if is_official_no_brand_value(platform, candidate.value):
                        pinned = {
                            "id": candidate.dictionary_value_id,
                            "value": candidate.value,
                            **({"label_zh": candidate.label_zh} if candidate.label_zh else {}),
                            "info": "",
                            "picture": "",
                        }
                        break
            except Exception:
                logger.warning(
                    "读取 %s 类目 %s 品牌的官方无品牌候选失败",
                    platform,
                    category_id,
                    exc_info=True,
                )
        if pinned is not None:
            safe_limit = max(1, int(page.limit or limit or 50))
            if len(values) >= safe_limit:
                values = [pinned, *values[: safe_limit - 1]]
                next_cursor = values[-1]["id"] if len(values) > 1 else "0"
                has_more = True
            else:
                values = [pinned, *values]

    return {
        "ok": True,
        "platform": page.platform,
        "category_id": page.category_id,
        "attribute_id": page.attribute_id,
        "query": raw_query,
        "cursor": page.cursor,
        "values": values,
        "complete": not has_more,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def search_categories_live(
    platform: str,
    query: str,
    site: str = "",
    limit: int | None = 5,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    result = create_category_searcher(
        platform,
        site=site,
        limit=limit,
        timeout_seconds=timeout_seconds,
    ).search_categories(query)
    source = str(result.get("source") or f"{platform}_live")
    rows: list[dict[str, Any]] = []
    for candidate in result["candidates"]:
        path_text = " / ".join(candidate.get("path_segments") or [])
        row: dict[str, Any] = {
            "id": str(candidate.get("category_id") or ""),
            "category_id": str(candidate.get("category_id") or ""),
            "name": str(candidate.get("name") or ""),
            "path": path_text,
            "category_path": path_text,
            "site": str(candidate.get("site") or site),
            "source": source,
        }
        if candidate.get("name_zh"):
            row["name_zh"] = str(candidate["name_zh"])
        if candidate.get("path_zh"):
            row["path_zh"] = str(candidate["path_zh"])
        elif candidate.get("path_segments_zh"):
            row["path_zh"] = " / ".join(candidate["path_segments_zh"])
        if candidate.get("description_category_id"):
            row["description_category_id"] = str(candidate["description_category_id"])
        if candidate.get("type_id"):
            row["type_id"] = str(candidate["type_id"])
        if candidate.get("forbidden"):
            row["forbidden"] = True
            if candidate.get("forbidden_reason"):
                row["forbidden_reason"] = str(candidate["forbidden_reason"])
        rows.append(row)
    return enrich_category_candidates_l2(platform, rows)


__all__ = [
    "fetch_category_attribute_page",
    "fetch_category_attribute_values",
    "fetch_category_attributes",
    "fetch_category_record",
    "search_categories_live",
]
