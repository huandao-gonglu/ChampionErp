# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from .category_providers import require_category_provider
from .category_searchers import create_category_searcher


logger = logging.getLogger(__name__)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Do not fail the caller, but a corrupt/unreadable JSON store should never
        # be silently indistinguishable from an empty one.
        logger.warning("read_json fell back to default for %s", path, exc_info=True)
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    """Atomically persist JSON: write a same-directory temp file, then os.replace.

    A crash mid-write must never leave a truncated/corrupt store behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


# SQLite 初始化已并入 ErpDatabase（构造期建 schema）；本模块只保留 JSON 文件
# 读写工具和类目实时检索。


def _path_text(record: dict[str, Any]) -> str:
    path = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    if path:
        return " / ".join(str(item).strip() for item in path if str(item).strip())
    return str(record.get("category_path") or record.get("name_original") or record.get("category_id") or "").strip()


def fetch_category_record(
    platform: str,
    category_id: str,
    site: str = "",
    include_attributes: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    provider = require_category_provider(platform)
    if timeout_seconds is None:
        return provider.detail(
            category_id,
            site=site,
            include_attributes=include_attributes,
        )
    return provider.detail(
        category_id,
        site=site,
        include_attributes=include_attributes,
        timeout_seconds=timeout_seconds,
    )


def fetch_category_attributes(
    platform: str,
    category_id: str,
    site: str = "",
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    provider = require_category_provider(platform)
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
        "site": record.get("site") or provider.resolve_site(site),
        "source": f"{platform}_live",
        "category": record,
        "required": required,
        "optional": optional,
        "attributes": required + optional,
        "category_id": record.get("category_id") or category_id,
        "category_path": _path_text(record),
        "path": _path_text(record),
    }


def fetch_category_attribute_values(
    platform: str,
    category_id: str,
    attribute_id: str,
    site: str = "",
    *,
    query: str = "",
    limit: int = 50,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    provider = require_category_provider(platform)
    return provider.attribute_values(
        category_id,
        attribute_id,
        site=site,
        query=query,
        limit=limit,
        timeout_seconds=timeout_seconds,
    )


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
    "fetch_category_attribute_values",
    "fetch_category_attributes",
    "fetch_category_record",
    "read_json",
    "search_categories_live",
    "write_json",
]
