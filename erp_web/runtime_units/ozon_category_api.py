# -*- coding: utf-8 -*-
"""Ozon Seller 类目树与属性 API 适配。

Ozon 创建商品时需要同时使用 ``description_category_id`` 和 ``type_id``。
对外的统一类目接口以 ``type_id`` 作为 ``category_id``，并把配对的描述类目 ID
保留在记录中，避免后续读取属性或发布时丢失关键信息。
"""
from __future__ import annotations

import time
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.schemas.category import CategoryCorpusInfo


OZON_CATEGORY_TREE_URL = "https://api-seller.ozon.ru/v1/description-category/tree"
OZON_CATEGORY_ATTRIBUTES_URL = "https://api-seller.ozon.ru/v1/description-category/attribute"
_TREE_CACHE_TTL_SECONDS = 15 * 60


@dataclass
class TtlCache:
    ttl_seconds: float
    _items: dict[str, tuple[float, datetime, Any]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            cached = self._items.get(key)
            if not cached:
                return None
            if now - cached[0] >= self.ttl_seconds:
                self._items.pop(key, None)
                return None
            return deepcopy(cached[2])

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (
                time.monotonic(),
                datetime.now(timezone.utc),
                deepcopy(value),
            )

    def timestamps(self, key: str) -> tuple[datetime, datetime] | None:
        now = time.monotonic()
        with self._lock:
            cached = self._items.get(key)
            if not cached:
                return None
            if now - cached[0] >= self.ttl_seconds:
                self._items.pop(key, None)
                return None
            retrieved_at = cached[1]
            return (
                retrieved_at,
                retrieved_at + timedelta(seconds=self.ttl_seconds),
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_tree_cache = TtlCache(_TREE_CACHE_TTL_SECONDS)


def _load_store_config() -> dict[str, Any]:
    # 延迟导入，避免分类 provider 初始化阶段形成循环依赖。
    from erp_web.context import get_context

    return get_context().config.load_store_config()


def _ozon_credentials() -> tuple[str, str]:
    config = _load_store_config()
    ozon = config.get("ozon") if isinstance(config.get("ozon"), dict) else {}
    client_id = str(ozon.get("client_id") or "").strip()
    api_key = str(ozon.get("api_key") or "").strip()
    if not client_id or not api_key:
        raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
    return client_id, api_key


def _text(value: Any) -> str:
    return str(value or "").strip()


def _node_title(node: dict[str, Any]) -> str:
    return _text(node.get("type_name") or node.get("category_name") or node.get("title") or node.get("name"))


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in node.get("children", []) if isinstance(item, dict)] if isinstance(node.get("children"), list) else []


def _load_tree(client_id: str, api_key: str) -> list[dict[str, Any]]:
    cached = _tree_cache.get(client_id)
    if isinstance(cached, list):
        return cached
    response = request_ozon_json(
        "POST",
        OZON_CATEGORY_TREE_URL,
        client_id,
        api_key,
        {"language": "DEFAULT"},
    )
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, list):
        raise RuntimeError("Ozon 类目树响应缺少 result 列表。")
    tree = [item for item in result if isinstance(item, dict)]
    _tree_cache.set(client_id, tree)
    return tree


def _flatten_product_types(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], names: list[str], category_ids: list[str], description_category_id: str = "") -> None:
        title = _node_title(node)
        path = [*names, title] if title else list(names)
        node_description_id = _text(node.get("description_category_id")) or description_category_id
        node_category_id = _text(node.get("description_category_id") or node.get("category_id"))
        path_ids = [*category_ids, node_category_id] if node_category_id else list(category_ids)
        type_id = _text(node.get("type_id"))
        if type_id and not bool(node.get("disabled")):
            records.append(
                {
                    "platform": "ozon",
                    "site": "global",
                    # 统一接口的类目 ID 使用 Ozon 可发布商品类型 ID。
                    "category_id": type_id,
                    "description_category_id": node_description_id,
                    "subject_id": node_description_id,
                    "type_id": type_id,
                    "name_original": title or type_id,
                    "name_cn": "",
                    "category_path": " / ".join(path or [title or type_id]),
                    "path_original": path or [title or type_id],
                    "path_ids": path_ids or [node_description_id, type_id],
                    "path_cn": [],
                    "parent_id": node_description_id,
                    "level": len(path or [title or type_id]),
                    "keywords": [item for item in (title, *path, type_id, node_description_id) if item],
                    "attributes": {"required": [], "optional": []},
                    "raw": deepcopy(node),
                }
            )
        for child in _children(node):
            walk(child, path, path_ids, node_description_id)

    for root in tree:
        walk(root, [], [])
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique.setdefault(str(record["type_id"]), record)
    return list(unique.values())


def _stable_corpus_hash(records: list[dict[str, Any]]) -> str:
    stable_records = sorted(
        (
            {
                "category_id": _text(record.get("category_id")),
                "description_category_id": _text(
                    record.get("description_category_id")
                ),
                "name": _text(record.get("name_original")),
                "path": [
                    _text(item)
                    for item in (
                        record.get("path_original")
                        if isinstance(record.get("path_original"), list)
                        else []
                    )
                    if _text(item)
                ],
            }
            for record in records
        ),
        key=lambda item: (
            item["category_id"],
            item["description_category_id"],
            item["name"],
        ),
    )
    encoded = json.dumps(
        stable_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_ozon_category_corpus() -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """返回缓存树展平后的可发布商品类型及可复盘的语料身份。"""

    client_id, api_key = _ozon_credentials()
    records = _flatten_product_types(_load_tree(client_id, api_key))
    if not records:
        raise RuntimeError("Ozon 类目树未返回可发布的商品类型。")
    timestamps = _tree_cache.timestamps(client_id)
    if timestamps is None:
        raise RuntimeError("Ozon 类目树缓存状态不可用。")
    retrieved_at, expires_at = timestamps
    credential_scope = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
    return records, {
        "corpus_hash": _stable_corpus_hash(records),
        "taxonomy_version": None,
        "locale": "ru-RU",
        "retrieved_at": retrieved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "credential_scope_hash": f"sha256:{credential_scope}",
    }


def _normalize_query(value: str) -> list[str]:
    return [part for part in " ".join(value.casefold().split()).split(" ") if part]


def _search_score(record: dict[str, Any], query: str, terms: list[str]) -> int:
    name = _text(record.get("name_original")).casefold()
    path = _text(record.get("category_path")).casefold()
    if query in name:
        return 100
    if query in path:
        return 90
    if terms and all(term in path for term in terms):
        return 80
    return 0


def search_ozon_categories(query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = _text(query)
    if not query:
        return []
    records, _ = load_ozon_category_corpus()
    normalized_query = " ".join(_normalize_query(query))
    terms = _normalize_query(query)
    matches: list[dict[str, Any]] = []
    for record in records:
        score = _search_score(record, normalized_query, terms)
        if not score:
            continue
        result = deepcopy(record)
        result.update(
            {
                "id": result["category_id"],
                "name": result["name_original"],
                "path": result["category_path"],
                "score": score,
                "matched_terms": terms,
                "source": "ozon_category_tree",
            }
        )
        matches.append(result)
    matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("category_path") or "")))
    return matches[: max(1, min(50, int(limit or 20)))]


def fetch_ozon_category_tree_summary() -> dict[str, Any]:
    """读取类目树并返回适合授权设置页展示的摘要。"""

    product_types, _ = load_ozon_category_corpus()
    if not product_types:
        raise RuntimeError("Ozon 类目树未返回可发布的商品类型。")
    sample = product_types[0]
    return {
        "product_type_count": len(product_types),
        "sample": {
            "type_id": sample.get("type_id"),
            "description_category_id": sample.get("description_category_id"),
            "path": sample.get("category_path"),
        },
    }


def _record_for_type_id(type_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    exact = next((item for item in records if _text(item.get("type_id")) == type_id), None)
    if exact:
        return deepcopy(exact)
    category_matches = [item for item in records if _text(item.get("description_category_id")) == type_id]
    if len(category_matches) == 1:
        return deepcopy(category_matches[0])
    raise RuntimeError("未找到 Ozon 商品类型。请从类目检索结果中选择可发布的商品类型。")


def _normalize_attribute(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(item.get("id")),
        "name": _text(item.get("name") or item.get("id")),
        "required": bool(item.get("is_required")),
        "value_type": _text(item.get("type")) or "string",
        "unit": _text(item.get("unit")),
        # Ozon 将枚举值放在单独的 attribute/values 接口；这里不额外逐属性请求。
        "options": [],
        "description": _text(item.get("description")),
        "raw": deepcopy(item),
    }


def fetch_ozon_category_record(category_id: str, include_attributes: bool = False) -> dict[str, Any]:
    type_id = _text(category_id)
    if not type_id:
        raise RuntimeError("缺少 Ozon 商品类型 ID。")
    client_id, api_key = _ozon_credentials()
    record = _record_for_type_id(type_id, _flatten_product_types(_load_tree(client_id, api_key)))
    if not include_attributes:
        return record
    description_category_id = _text(record.get("description_category_id"))
    try:
        request_category_id: int | str = int(description_category_id)
        request_type_id: int | str = int(_text(record.get("type_id")))
    except ValueError as exc:
        raise RuntimeError("Ozon 类目 ID 格式无效。") from exc
    response = request_ozon_json(
        "POST",
        OZON_CATEGORY_ATTRIBUTES_URL,
        client_id,
        api_key,
        {
            "description_category_id": request_category_id,
            "type_id": request_type_id,
            "language": "DEFAULT",
        },
    )
    raw_attributes = response.get("result") if isinstance(response, dict) else None
    if not isinstance(raw_attributes, list):
        raise RuntimeError("Ozon 类目属性响应缺少 result 列表。")
    attributes = [_normalize_attribute(item) for item in raw_attributes if isinstance(item, dict)]
    record["attributes"] = {
        "required": [item for item in attributes if item.get("required")],
        "optional": [item for item in attributes if not item.get("required")],
    }
    record["raw"] = {
        "category_tree": record.get("raw") if isinstance(record.get("raw"), dict) else {},
        "attributes": deepcopy(raw_attributes),
    }
    return record


def clear_ozon_category_tree_cache() -> None:
    """供测试和凭据切换后的显式刷新使用。"""

    _tree_cache.clear()


__all__ = [
    "OZON_CATEGORY_ATTRIBUTES_URL",
    "OZON_CATEGORY_TREE_URL",
    "clear_ozon_category_tree_cache",
    "fetch_ozon_category_tree_summary",
    "fetch_ozon_category_record",
    "load_ozon_category_corpus",
    "search_ozon_categories",
]
