# -*- coding: utf-8 -*-
"""Yandex Market 类目树与类目属性 API 适配。

类目树按语言和凭据作用域缓存（内存 + 持久化 gzip JSON），避免触发 Yandex
每小时限额；授权测试和用户主动刷新通过 ``force_refresh`` 绕过缓存。
类目参数按类目 ID 做短 TTL 内存缓存。

本模块只保留平台 shape 与缓存；平台 shape → 通用 CategoryProvider shape 的
机械转换发生在 ``category_providers.py`` 的平台边界。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
import uuid

from erp_web.marketplaces.yandex_http import (
    fetch_yandex_category_parameters,
    fetch_yandex_category_tree,
)
from erp_web.schemas.category import CategoryCorpusInfo

logger = logging.getLogger(__name__)


YANDEX_CATEGORY_CACHE_SCHEMA = "yandex.category-corpus.v1"
# 类目树变化频率低；新鲜窗口设长一些，避免触发每小时限额。
YANDEX_CATEGORY_CACHE_FRESH_TTL = timedelta(hours=6)
YANDEX_CATEGORY_CACHE_MAX_AGE = timedelta(days=7)
YANDEX_CATEGORY_PARAMETERS_TTL_SECONDS = 15 * 60

_CACHE_DIRECTORY_NAME = "yandex_categories"
_CACHE_FILE_PATTERN = re.compile(
    r"^corpus-v1-([0-9a-f]{64})-([A-Z]{2}(?:-[A-Za-z0-9]{2})?)\.json\.gz$"
)


@dataclass(frozen=True)
class YandexCategoryCacheEntry:
    credential_scope_hash: str
    corpus_hash: str
    language: str
    retrieved_at: datetime
    records: list[dict[str, Any]]

    @property
    def expires_at(self) -> datetime:
        return self.retrieved_at + YANDEX_CATEGORY_CACHE_FRESH_TTL

    @property
    def stale_until(self) -> datetime:
        return self.retrieved_at + YANDEX_CATEGORY_CACHE_MAX_AGE

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at

    def can_serve_stale(self, now: datetime) -> bool:
        return now < self.stale_until


@dataclass
class _MemoryCache:
    """带 TTL 的内存缓存；测试可直接 clear。"""

    def __post_init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

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
            self._items[key] = (time.monotonic() + ttl_seconds, deepcopy(value))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_corpus_cache = _MemoryCache()
_parameters_cache = _MemoryCache()
_corpus_load_lock = threading.RLock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_store_config() -> dict[str, Any]:
    # 延迟导入，避免 provider 初始化阶段形成循环依赖。
    from erp_web.context import get_context

    return get_context().config.load_store_config()


def _category_cache_root() -> Path:
    from erp_web.context import get_context

    return get_context().paths.cache_dir


def _yandex_credentials() -> tuple[str, str]:
    """返回 ``(api_token, business_id)``；business_id 来自已验证授权元数据。"""

    config = _load_store_config()
    yandex = config.get("yandex") if isinstance(config.get("yandex"), dict) else {}
    api_token = _text(yandex.get("api_token"))
    business_id = _text(yandex.get("business_id"))
    if not api_token:
        raise RuntimeError("请先填写 Yandex API-Key Token。")
    if not business_id:
        raise RuntimeError("Yandex business_id 尚未通过在线授权校验，请先测试授权。")
    return api_token, business_id


def _credential_scope_hash(api_token: str) -> str:
    digest = hashlib.sha256(str(api_token or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _language_key(language: str) -> str:
    return _text(language).upper() or "RU"


def _scope_digest(credential_scope_hash: str) -> str:
    value = _text(credential_scope_hash).lower()
    digest = value.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Yandex 类目缓存的凭据作用域摘要无效。")
    return digest


def _cache_path(
    cache_root: Path,
    credential_scope_hash: str,
    language: str,
) -> Path:
    digest = _scope_digest(credential_scope_hash)
    return (
        Path(cache_root)
        / _CACHE_DIRECTORY_NAME
        / f"corpus-v1-{digest}-{_language_key(language)}.json.gz"
    )


def _parse_utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [item for item in children if isinstance(item, dict)]


def _node_is_leaf(node: dict[str, Any]) -> bool:
    if isinstance(node.get("is_leaf"), bool):
        return bool(node["is_leaf"])
    if isinstance(node.get("isLeaf"), bool):
        return bool(node["isLeaf"])
    return not _node_children(node)


def _flatten_leaf_categories(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展平类目树，只保留叶子类目（发布只能选择叶子）。"""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: dict[str, Any], names: list[str], parent_id: str) -> None:
        name = _text(
            node.get("name") or node.get("title") or node.get("category_name")
        )
        # 官方 CategoryDTO 使用 id；保留旧键作为非官方 shape 的兜底。
        category_id = _text(
            node.get("id") or node.get("category_id") or node.get("categoryId")
        )
        path = [*names, name] if name else list(names)
        if not category_id:
            for child in _node_children(node):
                walk(child, path, parent_id)
            return
        if _node_is_leaf(node):
            if category_id not in seen:
                seen.add(category_id)
                slim_raw = {
                    key: value
                    for key, value in node.items()
                    if key != "children"
                }
                records.append(
                    {
                        "platform": "yandex",
                        "site": "global",
                        "category_id": category_id,
                        "name_original": name or category_id,
                        "category_path": " / ".join(path or [name or category_id]),
                        "path_original": path or [name or category_id],
                        "parent_id": parent_id,
                        "level": len(path or [name or category_id]),
                        "is_leaf": True,
                        "keywords": [item for item in (*path, category_id) if item],
                        "raw": slim_raw,
                    }
                )
            return
        for child in _node_children(node):
            walk(child, path, category_id)

    for root in tree:
        walk(root, [], "")
    return records


def _stable_corpus_hash(records: list[dict[str, Any]]) -> str:
    stable_records = sorted(
        (
            {
                "category_id": _text(record.get("category_id")),
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
        key=lambda item: item["category_id"],
    )
    encoded = json.dumps(
        stable_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _read_persistent_entry(
    cache_root: Path,
    credential_scope_hash: str,
    language: str,
    *,
    now: datetime,
) -> YandexCategoryCacheEntry | None:
    path = _cache_path(cache_root, credential_scope_hash, language)
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != YANDEX_CATEGORY_CACHE_SCHEMA:
        return None
    if _text(payload.get("credential_scope_hash")) != credential_scope_hash:
        return None
    if _language_key(payload.get("language") or "") != _language_key(language):
        return None
    retrieved_at = _parse_utc_datetime(payload.get("retrieved_at"))
    records = payload.get("records")
    corpus_hash = _text(payload.get("corpus_hash"))
    if (
        retrieved_at is None
        or not isinstance(records, list)
        or not records
        or not all(isinstance(record, dict) for record in records)
        or not corpus_hash.startswith("sha256:")
    ):
        return None
    entry = YandexCategoryCacheEntry(
        credential_scope_hash=credential_scope_hash,
        corpus_hash=corpus_hash,
        language=_language_key(payload.get("language") or language),
        retrieved_at=retrieved_at,
        records=[dict(record) for record in records],
    )
    if not entry.can_serve_stale(now):
        return None
    return entry


def _write_persistent_entry(
    cache_root: Path,
    entry: YandexCategoryCacheEntry,
) -> Path:
    path = _cache_path(cache_root, entry.credential_scope_hash, entry.language)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    payload = {
        "schema": YANDEX_CATEGORY_CACHE_SCHEMA,
        "credential_scope_hash": entry.credential_scope_hash,
        "corpus_hash": entry.corpus_hash,
        "language": entry.language,
        "retrieved_at": entry.retrieved_at.astimezone(timezone.utc).isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "stale_until": entry.stale_until.isoformat(),
        "records": entry.records,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
    tmp_path = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp_path.write_bytes(compressed)
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
    return path


def clear_yandex_category_cache(
    cache_root: Path,
    *,
    credential_scope_hash: str | None = None,
) -> int:
    """删除一个凭据作用域或全部受管 Yandex 类目缓存文件。"""

    _corpus_cache.clear()
    _parameters_cache.clear()
    directory = Path(cache_root) / _CACHE_DIRECTORY_NAME
    try:
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file() and _CACHE_FILE_PATTERN.fullmatch(path.name)
        ]
    except FileNotFoundError:
        return 0
    if credential_scope_hash:
        digest = _scope_digest(credential_scope_hash)
        candidates = [path for path in candidates if f"-{digest}-" in path.name]
    removed = 0
    for path in candidates:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def _is_transient_category_error(exc: Exception) -> bool:
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return retryable
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in ("timeout", "timed out", "限流", "rate limit", "network")
    )


def load_yandex_category_corpus(
    *,
    language: str = "RU",
    force_refresh: bool = False,
    credentials: tuple[str, ...] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """返回 Yandex 叶子类目语料，并按新鲜窗口管理缓存。

    新鲜缓存直接复用；过期后刷新远端；远端仅在可重试错误时允许回落到
    7 天内的旧缓存。认证错误、无效响应和空语料都不会被旧数据掩盖。
    """

    resolved_language = _language_key(language)
    if credentials is not None:
        api_token = _text(credentials[0])
        if not api_token:
            raise RuntimeError("请先填写 Yandex API-Key Token。")
    else:
        api_token, _ = _yandex_credentials()
    scope_hash = _credential_scope_hash(api_token)
    cache_key = f"{scope_hash}:{resolved_language}"

    with _corpus_load_lock:
        now = _utc_now()
        if not force_refresh:
            cached = _corpus_cache.get(cache_key)
            if isinstance(cached, dict):
                entry = cached.get("entry")
                if isinstance(entry, YandexCategoryCacheEntry):
                    return deepcopy(entry.records), _corpus_info(entry, cached)

        persistent = _read_persistent_entry(
            _category_cache_root(),
            scope_hash,
            resolved_language,
            now=now,
        )
        if (
            not force_refresh
            and persistent is not None
            and persistent.is_fresh(now)
        ):
            payload = {
                "entry": persistent,
                "cache_source": "persistent_cache",
                "stale": False,
            }
            _corpus_cache.set(
                cache_key,
                payload,
                ttl_seconds=(persistent.expires_at - now).total_seconds(),
            )
            return deepcopy(persistent.records), _corpus_info(persistent, payload)

        try:
            tree = fetch_yandex_category_tree(
                api_token,
                language=resolved_language,
                timeout_seconds=(
                    float(timeout_seconds)
                    if timeout_seconds is not None
                    else 30.0
                ),
            )
            records = _flatten_leaf_categories(tree)
            if not records:
                raise RuntimeError("Yandex 类目树未返回可发布的叶子类目。")
            entry = YandexCategoryCacheEntry(
                credential_scope_hash=scope_hash,
                corpus_hash=_stable_corpus_hash(records),
                language=resolved_language,
                retrieved_at=_utc_now(),
                records=records,
            )
        except Exception as exc:
            fallback_now = _utc_now()
            if (
                persistent is None
                or not persistent.can_serve_stale(fallback_now)
                or not _is_transient_category_error(exc)
            ):
                raise
            stale = not persistent.is_fresh(fallback_now)
            payload = {
                "entry": persistent,
                "cache_source": "stale_cache" if stale else "persistent_cache",
                "stale": stale,
            }
            _corpus_cache.set(
                cache_key,
                payload,
                ttl_seconds=min(
                    60.0,
                    (persistent.stale_until - fallback_now).total_seconds(),
                ),
            )
            return deepcopy(persistent.records), _corpus_info(persistent, payload)

        try:
            _write_persistent_entry(_category_cache_root(), entry)
        except OSError:
            logger.warning("写入 Yandex 类目持久化缓存失败", exc_info=True)
        payload = {
            "entry": entry,
            "cache_source": "remote_cache",
            "stale": False,
        }
        _corpus_cache.set(
            cache_key,
            payload,
            ttl_seconds=YANDEX_CATEGORY_CACHE_FRESH_TTL.total_seconds(),
        )
        return deepcopy(entry.records), _corpus_info(entry, payload)


def _corpus_info(
    entry: YandexCategoryCacheEntry,
    payload: dict[str, Any],
) -> CategoryCorpusInfo:
    return {
        "corpus_hash": entry.corpus_hash,
        "taxonomy_version": None,
        "locale": entry.language,
        "retrieved_at": entry.retrieved_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "stale_until": entry.stale_until.isoformat(),
        "credential_scope_hash": entry.credential_scope_hash,
        "cache_source": str(payload.get("cache_source") or "persistent_cache"),
        "stale": bool(payload.get("stale")),
    }


def refresh_yandex_category_corpus(
    *,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
    """用户主动刷新：绕过新鲜缓存重新读取类目树。"""

    return load_yandex_category_corpus(
        force_refresh=True,
        timeout_seconds=timeout_seconds,
    )


def fetch_yandex_category_tree_summary(
    *,
    force_refresh: bool = False,
    credentials: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """读取类目树并返回适合授权设置页展示的摘要。"""

    api_token = _text(credentials[0]) if credentials else ""
    records, corpus_info = load_yandex_category_corpus(
        force_refresh=force_refresh,
        credentials=(api_token,) if api_token else None,
    )
    if not records:
        raise RuntimeError("Yandex 类目树未返回可发布的叶子类目。")
    sample = records[0]
    return {
        "product_type_count": len(records),
        "sample": {
            "category_id": sample.get("category_id"),
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


def search_yandex_categories(
    query: str,
    limit: int = 20,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """本地缓存树上的规范化匹配；不为每次输入发远端请求。"""

    query = _text(query)
    if not query:
        return []
    records, corpus_info = load_yandex_category_corpus(
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
                "source": "yandex_category_tree",
                "cache_source": corpus_info.get("cache_source"),
            }
        )
        matches.append(result)
    matches.sort(
        key=lambda item: (-int(item.get("score") or 0), str(item.get("category_path") or ""))
    )
    return matches[: max(1, min(50, int(limit or 20)))]


def fetch_yandex_leaf_record(
    category_id: str,
    *,
    include_attributes: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """返回单个叶子类目的平台 shape 记录（可含类目属性）。"""

    category_id = _text(category_id)
    if not category_id:
        raise RuntimeError("缺少 Yandex 类目 ID。")
    records, _ = load_yandex_category_corpus(timeout_seconds=timeout_seconds)
    record = next(
        (item for item in records if _text(item.get("category_id")) == category_id),
        None,
    )
    if record is None:
        raise RuntimeError("未找到 Yandex 叶子类目。请从类目检索结果中选择可发布的叶子类目。")
    result = deepcopy(record)
    result["attributes"] = {"required": [], "optional": []}
    if include_attributes:
        parameters = fetch_yandex_category_parameter_definitions(
            category_id,
            timeout_seconds=timeout_seconds,
        )
        required = [item for item in parameters if item.get("required")]
        optional = [item for item in parameters if not item.get("required")]
        result["attributes"] = {"required": required, "optional": optional}
    return result


def fetch_yandex_category_parameter_definitions(
    category_id: str,
    *,
    timeout_seconds: float | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """读取类目参数并返回平台 shape 定义列表（短 TTL 内存缓存）。"""

    category_id = _text(category_id)
    api_token, business_id = _yandex_credentials()
    cache_key = f"{_credential_scope_hash(api_token)}:{business_id}:{category_id}"
    if not force_refresh:
        cached = _parameters_cache.get(cache_key)
        if isinstance(cached, list):
            return deepcopy(cached)
    parameters = fetch_yandex_category_parameters(
        api_token,
        category_id,
        business_id,
        timeout_seconds=(
            float(timeout_seconds) if timeout_seconds is not None else 30.0
        ),
    )
    normalized = [_normalize_yandex_parameter(item) for item in parameters]
    normalized = [item for item in normalized if item.get("parameter_id")]
    _parameters_cache.set(
        cache_key,
        normalized,
        ttl_seconds=YANDEX_CATEGORY_PARAMETERS_TTL_SECONDS,
    )
    return deepcopy(normalized)


def _normalize_yandex_parameter(item: dict[str, Any]) -> dict[str, Any]:
    """保留 Yandex wire 字段在 raw 中；对外暴露平台 shape 定义。

    官方 CategoryParameterDTO：``multivalue``（多值）、``allowCustomValues``
    （ENUM 自定义值）、``unit.defaultUnitId`` / ``unit.units[]``（单位 ID
    与名称）、``constraints``（NUMERIC min/max 与 TEXT maxLength）、
    ``values[].id``（枚举值 ID，提交时作为 ``valueId``）。
    """

    parameter_id = _text(item.get("id") or item.get("parameterId") or item.get("parameter_id"))
    parameter_type = _text(
        item.get("type") or item.get("parameter_type") or "TEXT"
    ).upper()
    values_raw = item.get("values") if isinstance(item.get("values"), list) else []
    values: list[dict[str, str]] = []
    for row in values_raw:
        if not isinstance(row, dict):
            text = _text(row)
            if text:
                values.append({"value_id": "", "value": text})
            continue
        value_id = _text(row.get("id") or row.get("valueId") or row.get("value_id"))
        value = _text(row.get("value") or row.get("name"))
        if value_id or value:
            values.append({"value_id": value_id, "value": value or value_id})
    unit_block = item.get("unit") if isinstance(item.get("unit"), dict) else {}
    units_raw = (
        unit_block.get("units") if isinstance(unit_block.get("units"), list) else []
    )
    units: list[dict[str, str]] = []
    for unit_row in units_raw:
        if not isinstance(unit_row, dict):
            continue
        unit_id = _text(unit_row.get("id"))
        unit_name = _text(unit_row.get("name"))
        if not unit_id and not unit_name:
            continue
        units.append(
            {
                "id": unit_id,
                "name": unit_name or unit_id,
                "full_name": _text(unit_row.get("fullName")),
            }
        )
    default_unit_id = _text(
        unit_block.get("defaultUnitId") or item.get("default_unit_id")
    )
    default_unit_name = next(
        (unit["name"] for unit in units if unit["id"] and unit["id"] == default_unit_id),
        "",
    )
    if not default_unit_name and units:
        # 兜底：defaultUnitId 缺失时以第一个允许单位作为默认展示。
        default_unit_name = units[0]["name"]
    constraints_raw = (
        item.get("constraints") if isinstance(item.get("constraints"), dict) else {}
    )
    constraints: dict[str, Any] = {}
    if constraints_raw.get("minValue") is not None:
        constraints["min_value"] = constraints_raw.get("minValue")
    if constraints_raw.get("maxValue") is not None:
        constraints["max_value"] = constraints_raw.get("maxValue")
    if constraints_raw.get("maxLength") is not None:
        constraints["max_length"] = constraints_raw.get("maxLength")
    return {
        "parameter_id": parameter_id,
        "name": _text(item.get("name") or parameter_id),
        "required": bool(item.get("required") or item.get("isRequired")),
        "parameter_type": parameter_type,
        "is_collection": bool(item.get("multivalue") or item.get("is_collection")),
        "allow_custom_values": bool(
            item.get("allowCustomValues") or item.get("allow_custom_values")
        ),
        "max_value_count": int(item.get("max_value_count") or 0),
        "unit": default_unit_name,
        "unit_options": [unit["name"] for unit in units if unit["name"]],
        "units": units,
        "default_unit": default_unit_name,
        "default_unit_id": default_unit_id,
        "constraints": constraints,
        "values": values,
        "raw": deepcopy(item),
    }


__all__ = [
    "YANDEX_CATEGORY_CACHE_FRESH_TTL",
    "YANDEX_CATEGORY_CACHE_MAX_AGE",
    "YANDEX_CATEGORY_CACHE_SCHEMA",
    "YandexCategoryCacheEntry",
    "clear_yandex_category_cache",
    "fetch_yandex_category_parameter_definitions",
    "fetch_yandex_category_tree_summary",
    "fetch_yandex_leaf_record",
    "load_yandex_category_corpus",
    "refresh_yandex_category_corpus",
    "search_yandex_categories",
]
