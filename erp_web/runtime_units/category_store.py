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
from .category_providers import require_category_provider
from .category_searchers import create_category_searcher


logger = logging.getLogger(__name__)


# SQLite 初始化已并入 ErpDatabase（构造期建 schema）；本模块只保留 JSON 文件
# 读写工具和类目实时检索。所有平台类目事实统一经 CategoryCatalog 读取；
# 返回的 legacy record 不再携带平台 raw 报文或完整枚举 values。


def fetch_category_record(
    platform: str,
    category_id: str,
    site: str = "",
    include_attributes: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """经 Catalog 读取类目记录（过渡 shape；属性只含规范化有界字段）。"""

    platform = str(platform or "").strip().lower()
    provider = require_category_provider(platform)
    catalog = get_category_catalog()
    detail = catalog.category_detail(
        platform,
        category_id,
        site=site,
        timeout_seconds=timeout_seconds,
    )
    record: dict[str, Any] = {
        "platform": platform,
        "site": detail.site or provider.resolve_site(site),
        "category_id": detail.category_id or str(category_id or "").strip(),
        "name_original": detail.name,
        "category_path": detail.path,
        "path_original": [
            segment.strip() for segment in detail.path.split("/") if segment.strip()
        ]
        or ([detail.name] if detail.name else []),
        "parent_id": detail.parent_id,
        "description_category_id": "",
        "is_leaf": detail.is_leaf,
        "source": f"{platform}_live",
        "attributes": {"required": [], "optional": []},
    }
    if not include_attributes:
        return record
    definition = catalog.attribute_definitions(
        platform,
        category_id,
        site=site,
        timeout_seconds=timeout_seconds,
    )
    record["description_category_id"] = definition.description_category_id
    record["category_path"] = definition.category_path or record["category_path"]
    record["attributes"] = {
        "required": [
            definition_to_legacy_attribute(attribute)
            for attribute in definition.required
        ],
        "optional": [
            definition_to_legacy_attribute(attribute)
            for attribute in definition.optional
        ],
    }
    return record


def fetch_category_attributes(
    platform: str,
    category_id: str,
    site: str = "",
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    record = fetch_category_record(
        platform,
        category_id,
        site=site,
        include_attributes=True,
        timeout_seconds=timeout_seconds,
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
) -> dict[str, Any]:
    """前端/Agent 的有界属性页（不含 platform_binding/raw/完整枚举）。"""

    catalog = get_category_catalog()
    page = catalog.public_attribute_page(
        platform,
        category_id,
        site=site,
        cursor=cursor,
        limit=limit,
        timeout_seconds=timeout_seconds,
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
                # limit=1 时没有可作为续读位置的普通候选；Ozon 的显式
                # 起始游标 "0" 可让下一页跳过置顶逻辑并重新读取原首项。
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
    limit: int = 5,
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
    return [
        {
            "id": str(candidate.get("category_id") or ""),
            "category_id": str(candidate.get("category_id") or ""),
            "name": str(candidate.get("name") or ""),
            "path": " / ".join(candidate.get("path_segments") or []),
            "category_path": " / ".join(candidate.get("path_segments") or []),
            **(
                {"description_category_id": candidate["description_category_id"]}
                if candidate.get("description_category_id")
                else {}
            ),
            **(
                {"type_id": candidate["type_id"]}
                if candidate.get("type_id")
                else {}
            ),
        }
        for candidate in result["candidates"]
    ]


__all__ = [
    "fetch_category_attribute_page",
    "fetch_category_attribute_values",
    "fetch_category_attributes",
    "fetch_category_record",
    "search_categories_live",
]
