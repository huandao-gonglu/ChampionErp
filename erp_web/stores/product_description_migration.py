"""读取历史商品时移除来源文案，仅保留各平台草稿的描述。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import html
import re


def _take_details(record: dict[str, Any]) -> list[str]:
    details: list[str] = []
    for field in ("selling_points", "bullets", "bullet_points"):
        value = record.pop(field, None)
        if isinstance(value, str):
            details.extend(value.splitlines())
        elif isinstance(value, list):
            details.extend(str(item) for item in value if item is not None)
    return details


def migrate_draft_description(draft: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(draft)
    details = _take_details(migrated)
    description = str(migrated.get("description") or "").strip()
    for detail in details:
        text = detail.strip()
        plain = html.unescape(re.sub(r"<[^>]+>", " ", description))
        pattern = r"(?<![A-Za-z0-9])" + re.escape(text) + r"(?![A-Za-z0-9])"
        if text and not re.search(pattern, plain, re.IGNORECASE):
            description = f"{description}\n\n{text}" if description else text
    if details:
        migrated["description"] = description
    return migrated


def migrate_product_description(product: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(product)
    _take_details(migrated)
    migrated.pop("description", None)
    source = migrated.get("source")
    if isinstance(source, dict):
        _take_details(source)
        source.pop("description", None)
        diagnostics = source.get("collect_diagnostics")
        if isinstance(diagnostics, dict):
            diagnostics.pop("bullets_found_count", None)
            diagnostics.pop("description_found", None)
            for field in ("collected_fields", "missing_fields"):
                if isinstance(diagnostics.get(field), list):
                    diagnostics[field] = [
                        item for item in diagnostics[field] if item not in {"bullets", "description"}
                    ]
    for field in ("drafts", "copy_results", "listing_overrides"):
        records = migrated.get(field)
        if isinstance(records, dict):
            migrated[field] = {
                key: migrate_draft_description(value) if isinstance(value, dict) else value
                for key, value in records.items()
            }
    return migrated
