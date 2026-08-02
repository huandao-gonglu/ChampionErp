# -*- coding: utf-8 -*-
"""Ozon Seller 类目树与属性 API 适配。

Ozon 创建商品时需要同时使用 ``description_category_id`` 和 ``type_id``。
对外的统一类目接口以 ``type_id`` 作为 ``category_id``，并把配对的描述类目 ID
保留在记录中，避免后续读取属性或发布时丢失关键信息。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import ssl
import threading
import time
from typing import Any
import urllib.error

from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.schemas.category import CategoryCorpusInfo

from .ozon_category_cache import (
    OZON_CATEGORY_CACHE_FRESH_TTL,
    OzonCategoryCacheEntry,
    clear_ozon_category_cache,
    read_ozon_category_cache,
    write_ozon_category_cache,
)


logger = logging.getLogger(__name__)


OZON_CATEGORY_TREE_URL = "https://api-seller.ozon.ru/v1/description-category/tree"
OZON_CATEGORY_ATTRIBUTES_URL = "https://api-seller.ozon.ru/v1/description-category/attribute"
_STALE_RETRY_COOLDOWN_SECONDS = 60


@dataclass
class _MemoryCorpusCache:
    _items: dict[str, tuple[float, Any]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            cached = self._items.get(key)
            if not cached:
                return None
            if now >= cached[0]:
                self._items.pop(key, None)
                return None
            return deepcopy(cached[1])

    def set(self, key: str, value: Any, *, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._items[key] = (
                time.monotonic() + ttl_seconds,
                deepcopy(value),
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_corpus_cache = _MemoryCorpusCache()
_corpus_load_lock = threading.RLock()


def _load_store_config() -> dict[str, Any]:
    # 延迟导入，避免分类 provider 初始化阶段形成循环依赖。
    from erp_web.context import get_context

    return get_context().config.load_store_config()


def _category_cache_root() -> Path:
    from erp_web.context import get_context

    return get_context().paths.cache_dir


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


def _credential_scope_hash(client_id: str) -> str:
    digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _is_auth_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            " 401",
            " 403",
            "unauthorized",
            "forbidden",
            "请先填写",
            "credentials missing",
            "missing credential",
        )
    )


def _is_transient_provider_error(exc: Exception) -> bool:
    if _is_auth_error(exc):
        return False
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            urllib.error.URLError,
            ssl.SSLError,
        ),
    ):
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            " 408",
            " 425",
            " 429",
            " 500",
            " 502",
            " 503",
            " 504",
            "timeout",
            "timed out",
            "rate limit",
            "temporarily unavailable",
            "connection",
            "network",
            "ssl",
            "unexpected_eof",
            "unexpected eof",
            "connection reset",
        )
    )


def _load_tree_from_remote(
    client_id: str,
    api_key: str,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """从 Ozon 读取类目树；瞬时网络错误在同一 deadline 内只重试一次。"""

    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )
    response: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            if deadline_at is None:
                response = request_ozon_json(
                    "POST",
                    OZON_CATEGORY_TREE_URL,
                    client_id,
                    api_key,
                    {"language": "DEFAULT"},
                )
            else:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Ozon 类目树 deadline 已耗尽")
                response = request_ozon_json(
                    "POST",
                    OZON_CATEGORY_TREE_URL,
                    client_id,
                    api_key,
                    {"language": "DEFAULT"},
                    timeout_seconds=remaining,
                )
            break
        except Exception as exc:
            if attempt or not _is_transient_provider_error(exc):
                raise
    if response is None:
        raise RuntimeError("Ozon 类目树请求未返回结果。")
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, list):
        raise RuntimeError("Ozon 类目树响应缺少 result 列表。")
    return [item for item in result if isinstance(item, dict)]


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _entry_payload(
    entry: OzonCategoryCacheEntry,
    *,
    cache_source: str,
    stale: bool,
) -> dict[str, Any]:
    return {
        "entry": entry,
        "cache_source": cache_source,
        "stale": stale,
    }


def _corpus_result(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    entry = payload.get("entry")
    if not isinstance(entry, OzonCategoryCacheEntry):
        raise RuntimeError("Ozon 类目语料缓存状态不可用。")
    source = str(payload.get("cache_source") or "persistent_cache")
    stale = bool(payload.get("stale"))
    return deepcopy(entry.records), {
        "corpus_hash": entry.corpus_hash,
        "taxonomy_version": entry.taxonomy_version,
        "locale": entry.locale,
        "retrieved_at": entry.retrieved_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "stale_until": entry.stale_until.isoformat(),
        "credential_scope_hash": entry.credential_scope_hash,
        "cache_source": source,
        "stale": stale,
    }


def _read_valid_persistent_entry(
    credential_scope_hash: str,
    *,
    now: datetime,
) -> OzonCategoryCacheEntry | None:
    entry = read_ozon_category_cache(
        _category_cache_root(),
        credential_scope_hash,
        now=now,
    )
    if entry is None:
        return None
    if _stable_corpus_hash(entry.records) != entry.corpus_hash:
        return None
    return entry


def load_ozon_category_corpus(
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """返回可发布商品类型，并按 24 小时新鲜期管理持久化缓存。

    新鲜缓存直接复用；缓存过期后刷新远端。远端仅在瞬时网络错误时允许使用
    7 天内的旧缓存，认证错误、无效响应和空语料都不会被旧数据掩盖。
    """

    client_id, api_key = _ozon_credentials()
    credential_scope_hash = _credential_scope_hash(client_id)
    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )

    def remaining_timeout() -> float | None:
        if deadline_at is None:
            return None
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Ozon 类目语料加载超过 deadline")
        return remaining

    with _corpus_load_lock:
        now = _utc_now()
        if not force_refresh:
            cached = _corpus_cache.get(credential_scope_hash)
            if isinstance(cached, dict):
                return _corpus_result(cached)

        persistent = _read_valid_persistent_entry(
            credential_scope_hash,
            now=now,
        )
        if not force_refresh and persistent is not None and persistent.is_fresh(now):
            payload = _entry_payload(
                persistent,
                cache_source="persistent_cache",
                stale=False,
            )
            _corpus_cache.set(
                credential_scope_hash,
                payload,
                ttl_seconds=(persistent.expires_at - now).total_seconds(),
            )
            return _corpus_result(payload)

        try:
            records = _flatten_product_types(
                _load_tree_from_remote(
                    client_id,
                    api_key,
                    timeout_seconds=remaining_timeout(),
                )
            )
            if not records:
                raise RuntimeError("Ozon 类目树未返回可发布的商品类型。")
            refreshed_at = _utc_now()
            entry = OzonCategoryCacheEntry(
                credential_scope_hash=credential_scope_hash,
                corpus_hash=_stable_corpus_hash(records),
                taxonomy_version=None,
                locale="ru-RU",
                retrieved_at=refreshed_at,
                records=records,
            )
        except Exception as exc:
            fallback_now = _utc_now()
            if (
                persistent is None
                or not persistent.can_serve_stale(fallback_now)
                or not _is_transient_provider_error(exc)
            ):
                raise
            fallback_is_stale = not persistent.is_fresh(fallback_now)
            payload = _entry_payload(
                persistent,
                cache_source=(
                    "stale_cache" if fallback_is_stale else "persistent_cache"
                ),
                stale=fallback_is_stale,
            )
            _corpus_cache.set(
                credential_scope_hash,
                payload,
                ttl_seconds=(
                    min(
                        _STALE_RETRY_COOLDOWN_SECONDS,
                        (
                            persistent.stale_until - fallback_now
                        ).total_seconds(),
                    )
                    if fallback_is_stale
                    else (
                        persistent.expires_at - fallback_now
                    ).total_seconds()
                ),
            )
            return _corpus_result(payload)

        try:
            write_ozon_category_cache(_category_cache_root(), entry)
        except OSError:
            logger.warning("写入 Ozon 类目持久化缓存失败", exc_info=True)

        payload = _entry_payload(
            entry,
            cache_source="remote_cache",
            stale=False,
        )
        _corpus_cache.set(
            credential_scope_hash,
            payload,
            ttl_seconds=OZON_CATEGORY_CACHE_FRESH_TTL.total_seconds(),
        )
        return _corpus_result(payload)


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


def search_ozon_categories(
    query: str,
    limit: int = 20,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    query = _text(query)
    if not query:
        return []
    records, corpus_info = load_ozon_category_corpus(
        timeout_seconds=timeout_seconds
    )
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
                "cache_source": corpus_info.get("cache_source"),
            }
        )
        matches.append(result)
    matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("category_path") or "")))
    return matches[: max(1, min(50, int(limit or 20)))]


def fetch_ozon_category_tree_summary(
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """读取类目树并返回适合授权设置页展示的摘要。"""

    product_types, corpus_info = load_ozon_category_corpus(
        force_refresh=force_refresh
    )
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
        "cache": {
            "source": corpus_info.get("cache_source"),
            "stale": bool(corpus_info.get("stale")),
            "retrieved_at": corpus_info.get("retrieved_at"),
            "expires_at": corpus_info.get("expires_at"),
            "stale_until": corpus_info.get("stale_until"),
        },
    }


def refresh_ozon_category_corpus(
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """显式跳过新鲜缓存并刷新 Ozon 类目语料。"""

    return load_ozon_category_corpus(
        timeout_seconds=timeout_seconds,
        force_refresh=True,
    )


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


def fetch_ozon_category_record(
    category_id: str,
    include_attributes: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    type_id = _text(category_id)
    if not type_id:
        raise RuntimeError("缺少 Ozon 商品类型 ID。")
    deadline_at = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )

    def remaining_timeout() -> float:
        if deadline_at is None:
            return 30
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Ozon 类目详情 deadline 已耗尽")
        return remaining

    records, _ = load_ozon_category_corpus(
        timeout_seconds=(
            None if deadline_at is None else remaining_timeout()
        )
    )
    record = _record_for_type_id(
        type_id,
        records,
    )
    if not include_attributes:
        return record
    client_id, api_key = _ozon_credentials()
    description_category_id = _text(record.get("description_category_id"))
    try:
        request_category_id: int | str = int(description_category_id)
        request_type_id: int | str = int(_text(record.get("type_id")))
    except ValueError as exc:
        raise RuntimeError("Ozon 类目 ID 格式无效。") from exc
    request_payload = {
        "description_category_id": request_category_id,
        "type_id": request_type_id,
        "language": "DEFAULT",
    }
    if deadline_at is None:
        response = request_ozon_json(
            "POST",
            OZON_CATEGORY_ATTRIBUTES_URL,
            client_id,
            api_key,
            request_payload,
        )
    else:
        response = request_ozon_json(
            "POST",
            OZON_CATEGORY_ATTRIBUTES_URL,
            client_id,
            api_key,
            request_payload,
            timeout_seconds=remaining_timeout(),
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


def clear_ozon_category_tree_cache(
    *,
    include_persistent: bool = True,
    credential_scope_hash: str | None = None,
) -> int:
    """清空内存缓存，并可删除一个作用域或全部持久化类目缓存。"""

    with _corpus_load_lock:
        _corpus_cache.clear()
        if not include_persistent:
            return 0
        return clear_ozon_category_cache(
            _category_cache_root(),
            credential_scope_hash=credential_scope_hash,
        )


__all__ = [
    "OZON_CATEGORY_ATTRIBUTES_URL",
    "OZON_CATEGORY_TREE_URL",
    "clear_ozon_category_tree_cache",
    "fetch_ozon_category_tree_summary",
    "fetch_ozon_category_record",
    "load_ozon_category_corpus",
    "refresh_ozon_category_corpus",
    "search_ozon_categories",
]
