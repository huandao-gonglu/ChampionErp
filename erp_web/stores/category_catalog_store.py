# -*- coding: utf-8 -*-
"""统一类目元数据与服务端翻译词典引擎（SQLite WAL + FTS5）。

本模块取代历史 4 套散装文件/内存缓存（Ozon 语料 .json.gz、Yandex 语料/参数缓存、
CategoryDefinition .json.gz 缓存、Mercado HTTP 内存字典），以单库承载：

1. ``taxonomy_snapshots`` + ``taxonomy_nodes`` + ``taxonomy_fts``：Ozon / Yandex 完整类目树
   （含分支节点、Ozon 复合键 ``(description_category_id, type_id)``、Ozon 官方中文、
   Yandex 祖先链与 FTS5 词干/中文倒排索引）；
2. ``category_definitions``：三平台属性定义持久缓存（含 LRU 热缓存、per-key singleflight、
   fresh/stale 窗口、60s 瞬时失败冷却与自动过期清理）；
3. ``category_value_cache``：三平台属性枚举页与只读 HTTP 响应有界缓存；
4. ``translation_glossary``：全平台共享服务端词条级翻译词典（L2 0ms 回填）。

本模块是纯存储基础设施，不导入任何平台 Provider 或业务 runtime unit。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any

from erp_web.russian_text import (
    cjk_index_tokens,
    has_cjk,
    normalize_search_terms,
    score_cjk_search_match,
    score_russian_search_match,
    stemmed_search_tokens,
)
from erp_web.schemas.category_definition import (
    DEFINITION_FORMAT_VERSION,
    CategoryCacheState,
    CategoryDefinition,
    definition_fingerprint,
)

logger = logging.getLogger(__name__)

STORE_SCHEMA_VERSION = 2
DEFINITION_CACHE_FRESH_TTL = timedelta(hours=24)
DEFINITION_CACHE_MAX_AGE = timedelta(days=7)
STALE_RETRY_COOLDOWN_SECONDS = 60.0

_DEFINITION_LRU_MAXSIZE = 128
_VALUE_LRU_MAXSIZE = 256
_GLOSSARY_LRU_MAXSIZE = 4096
_PRUNE_INTERVAL_SECONDS = 300.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _fts_quote(token: str) -> str:
    cleaned = token.replace('"', "").strip()
    return f'"{cleaned}"' if cleaned else ""


@dataclass(frozen=True)
class TaxonomySnapshot:
    platform: str
    scope_key: str
    locale: str
    corpus_hash: str
    taxonomy_version: str | None
    node_count: int
    leaf_count: int
    retrieved_at: datetime
    expires_at: datetime
    stale_until: datetime
    source: str

    def is_fresh(self, now: datetime) -> bool:
        return self.retrieved_at <= now < self.expires_at

    def can_serve_stale(self, now: datetime) -> bool:
        return self.retrieved_at <= now < self.stale_until

    def to_corpus_info(self, *, cache_source: str, stale: bool) -> dict[str, Any]:
        return {
            "corpus_hash": self.corpus_hash,
            "taxonomy_version": self.taxonomy_version,
            "locale": self.locale,
            "retrieved_at": self.retrieved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "stale_until": self.stale_until.isoformat(),
            "credential_scope_hash": self.scope_key,
            "cache_source": cache_source,
            "stale": stale,
        }


@dataclass(frozen=True)
class CategoryDefinitionCacheEntry:
    platform: str
    credential_scope_hash: str
    site: str
    category_id: str
    format_version: int
    definition: CategoryDefinition
    fingerprint: str
    retrieved_at: datetime
    expires_at: datetime
    stale_until: datetime
    source: str

    def is_fresh(self, now: datetime) -> bool:
        return self.retrieved_at <= now < self.expires_at

    def can_serve_stale(self, now: datetime) -> bool:
        return self.retrieved_at <= now < self.stale_until

    def cache_state(self, *, stale: bool, source: str) -> CategoryCacheState:
        return CategoryCacheState(
            source=source,
            stale=stale,
            retrieved_at=self.retrieved_at.isoformat(),
            expires_at=self.expires_at.isoformat(),
            stale_until=self.stale_until.isoformat(),
        )


class CategoryCatalogStore:
    """线程安全的类目树、属性定义、枚举缓存与翻译词典 SQLite 引擎。"""

    _shared_registry_lock = threading.RLock()
    _shared_instances: dict[Path, tuple["CategoryCatalogStore", int]] = {}

    @classmethod
    def acquire_shared(cls, path: str | Path) -> "CategoryCatalogStore":
        """按规范化库路径返回进程内共享实例，使多账号共用单飞锁、冷却态与热 LRU。"""

        resolved = Path(path).resolve()
        with cls._shared_registry_lock:
            existing = cls._shared_instances.get(resolved)
            if existing is not None:
                store, count = existing
                cls._shared_instances[resolved] = (store, count + 1)
                return store
            store = cls(resolved)
            cls._shared_instances[resolved] = (store, 1)
            return store

    @classmethod
    def release_shared(cls, store: "CategoryCatalogStore") -> None:
        """释放共享实例引用；仅在最后一个持有者退出时清理内存并移出注册表。"""

        resolved = store.path.resolve()
        should_close = False
        with cls._shared_registry_lock:
            existing = cls._shared_instances.get(resolved)
            if existing is None or existing[0] is not store:
                should_close = True
            else:
                remaining = existing[1] - 1
                if remaining <= 0:
                    cls._shared_instances.pop(resolved, None)
                    should_close = True
                else:
                    cls._shared_instances[resolved] = (store, remaining)
        if should_close:
            store.close()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

        self._meta_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._singleflight_locks: dict[str, threading.Lock] = {}
        self._cooldowns: dict[str, float] = {}
        self._definition_lru: OrderedDict[
            tuple[str, str, str, str, int], CategoryDefinitionCacheEntry
        ] = OrderedDict()
        self._value_lru: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._glossary_lru: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._last_pruned_at = 0.0
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, STORE_SCHEMA_VERSION):
                # 仅对未知/不兼容版本重建；1 -> 2 走原地迁移，完整保留 translation_glossary。
                for table in (
                    "taxonomy_fts",
                    "taxonomy_nodes",
                    "taxonomy_snapshots",
                    "category_definitions",
                    "category_value_cache",
                    "translation_glossary",
                ):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS taxonomy_snapshots (
                    platform TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    corpus_hash TEXT NOT NULL,
                    taxonomy_version TEXT,
                    node_count INTEGER NOT NULL,
                    leaf_count INTEGER NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    stale_until TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'remote_cache',
                    PRIMARY KEY (platform, scope_key, locale)
                );

                CREATE TABLE IF NOT EXISTS taxonomy_nodes (
                    platform TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    category_id TEXT NOT NULL,
                    description_category_id TEXT NOT NULL DEFAULT '',
                    parent_key TEXT NOT NULL DEFAULT '',
                    level TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    name_zh TEXT NOT NULL DEFAULT '',
                    path_text TEXT NOT NULL,
                    path_zh TEXT NOT NULL DEFAULT '',
                    path_json TEXT NOT NULL,
                    path_zh_json TEXT NOT NULL DEFAULT '[]',
                    ancestor_ids_json TEXT NOT NULL DEFAULT '[]',
                    child_count INTEGER NOT NULL DEFAULT 0,
                    publishable INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (platform, scope_key, locale, node_key)
                );
                CREATE INDEX IF NOT EXISTS idx_taxonomy_parent
                    ON taxonomy_nodes(platform, scope_key, locale, parent_key);
                CREATE INDEX IF NOT EXISTS idx_taxonomy_cat_id
                    ON taxonomy_nodes(platform, scope_key, locale, category_id);
                CREATE INDEX IF NOT EXISTS idx_taxonomy_desc_id
                    ON taxonomy_nodes(platform, scope_key, locale, description_category_id);

                CREATE VIRTUAL TABLE IF NOT EXISTS taxonomy_fts USING fts5(
                    platform UNINDEXED,
                    scope_key UNINDEXED,
                    locale UNINDEXED,
                    node_key UNINDEXED,
                    category_id,
                    description_category_id,
                    name_stemmed,
                    path_stemmed,
                    zh_tokens,
                    tokenize='unicode61'
                );

                CREATE TABLE IF NOT EXISTS category_definitions (
                    platform TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    site TEXT NOT NULL,
                    category_id TEXT NOT NULL,
                    format_version INTEGER NOT NULL,
                    description_category_id TEXT NOT NULL DEFAULT '',
                    category_path TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    stale_until TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'live',
                    PRIMARY KEY (platform, scope_key, site, category_id, format_version)
                );
                CREATE INDEX IF NOT EXISTS idx_definitions_stale
                    ON category_definitions(stale_until);

                CREATE TABLE IF NOT EXISTS category_value_cache (
                    cache_key TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_value_cache_scope
                    ON category_value_cache(platform, scope_key);
                CREATE INDEX IF NOT EXISTS idx_value_cache_exp
                    ON category_value_cache(expires_at);

                CREATE TABLE IF NOT EXISTS translation_glossary (
                    domain TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (domain, target_language, source_hash)
                );
                """
            )
            migrated_legacy_rows = 0
            if version == 1:
                # v1 -> v2 原地清理历史按凭据隔离的重复 Ozon/Yandex 公共类目树与旧作用域缓存，
                # 严格保留 translation_glossary 全部已翻译词条。
                for table in ("taxonomy_snapshots", "taxonomy_nodes", "taxonomy_fts"):
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE platform IN ('ozon', 'yandex') AND scope_key != 'global'"
                    )
                    migrated_legacy_rows += max(0, int(cur.rowcount))
                for table in ("category_definitions", "category_value_cache"):
                    cur_ozon = conn.execute(
                        f"DELETE FROM {table} WHERE platform = 'ozon' AND scope_key != 'global'"
                    )
                    cur_yandex = conn.execute(
                        f"DELETE FROM {table} WHERE platform = 'yandex' AND scope_key NOT LIKE 'business:%'"
                    )
                    migrated_legacy_rows += max(0, int(cur_ozon.rowcount)) + max(0, int(cur_yandex.rowcount))
            conn.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
            conn.commit()
            if migrated_legacy_rows > 0:
                try:
                    conn.execute("VACUUM")
                except sqlite3.Error:
                    logger.debug("v1 -> v2 清理旧类目缓存后 VACUUM 跳过", exc_info=True)

    def close(self) -> None:
        with self._meta_lock:
            self._definition_lru.clear()
            self._value_lru.clear()
            self._glossary_lru.clear()
            self._cooldowns.clear()
            self._singleflight_locks.clear()

    def _flight_lock(self, key: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._singleflight_locks.get(key)
            if lock is None:
                if len(self._singleflight_locks) > 512:
                    # 回收未占用的旧锁，防止长期运行字典膨胀。
                    idle = [k for k, v in self._singleflight_locks.items() if not v.locked()]
                    for k in idle[:256]:
                        self._singleflight_locks.pop(k, None)
                lock = threading.Lock()
                self._singleflight_locks[key] = lock
            return lock

    def _in_cooldown(self, key: str) -> bool:
        with self._meta_lock:
            until = self._cooldowns.get(key)
            if until is None:
                return False
            if time.monotonic() < until:
                return True
            self._cooldowns.pop(key, None)
            return False

    def _set_cooldown(self, key: str, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._meta_lock:
            self._cooldowns[key] = time.monotonic() + seconds

    def _clear_cooldown(self, key: str) -> None:
        with self._meta_lock:
            self._cooldowns.pop(key, None)

    def _maybe_prune_expired(self, *, now_dt: datetime, now_ts: float) -> None:
        with self._meta_lock:
            if now_ts - self._last_pruned_at < _PRUNE_INTERVAL_SECONDS:
                return
            self._last_pruned_at = now_ts
        try:
            with self._write_lock, self._connect() as conn:
                conn.execute(
                    "DELETE FROM category_value_cache WHERE expires_at <= ?",
                    (now_ts,),
                )
                conn.execute(
                    "DELETE FROM category_definitions WHERE stale_until <= ?",
                    (now_dt.isoformat(),),
                )
                conn.commit()
        except sqlite3.Error:
            logger.debug("清理过期类目缓存失败", exc_info=True)

    # -----------------------------------------------------------------------
    # 1. Taxonomy（Ozon / Yandex 完整树、FTS5 检索、树导航、兄弟节点与祖先链）
    # -----------------------------------------------------------------------

    def read_taxonomy_snapshot(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        *,
        now: datetime | None = None,
    ) -> TaxonomySnapshot | None:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT platform, scope_key, locale, corpus_hash, taxonomy_version,
                       node_count, leaf_count, retrieved_at, expires_at, stale_until, source
                FROM taxonomy_snapshots
                WHERE platform = ? AND scope_key = ? AND locale = ?
                """,
                (plat, scope, loc),
            ).fetchone()
        if row is None:
            return None
        retrieved_at = _parse_utc(row["retrieved_at"])
        expires_at = _parse_utc(row["expires_at"])
        stale_until = _parse_utc(row["stale_until"])
        if retrieved_at is None or expires_at is None or stale_until is None:
            return None
        snapshot = TaxonomySnapshot(
            platform=row["platform"],
            scope_key=row["scope_key"],
            locale=row["locale"],
            corpus_hash=row["corpus_hash"],
            taxonomy_version=row["taxonomy_version"],
            node_count=int(row["node_count"]),
            leaf_count=int(row["leaf_count"]),
            retrieved_at=retrieved_at,
            expires_at=expires_at,
            stale_until=stale_until,
            source=row["source"],
        )
        current = now or _utc_now()
        if not snapshot.can_serve_stale(current):
            return None
        return snapshot

    def save_taxonomy(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        nodes: list[dict[str, Any]],
        *,
        corpus_hash: str,
        taxonomy_version: str | None = None,
        retrieved_at: datetime | None = None,
        fresh_ttl: timedelta,
        max_age: timedelta,
        source: str = "remote_cache",
    ) -> TaxonomySnapshot:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        ts = (retrieved_at or _utc_now()).astimezone(timezone.utc)
        expires_at = ts + fresh_ttl
        stale_until = ts + max_age

        node_rows: list[tuple[Any, ...]] = []
        fts_rows: list[tuple[Any, ...]] = []
        leaf_count = 0

        for item in nodes:
            node_key = _text(item.get("node_key") or item.get("node_id") or item.get("category_id"))
            if not node_key:
                continue
            level = _text(item.get("level")) or ("product_type" if item.get("publishable") else "branch")
            publishable = 1 if (level == "product_type" and item.get("publishable", True)) else 0
            category_id = _text(item.get("category_id") or item.get("type_id") or node_key)
            description_category_id = _text(item.get("description_category_id"))
            parent_key = _text(item.get("parent_key") if "parent_key" in item else item.get("parent_id"))
            depth = max(1, int(item.get("depth") or 1))
            name = _text(item.get("name") or item.get("name_original") or category_id)
            name_zh = _text(item.get("name_zh") or item.get("name_cn"))
            path_segments = [
                _text(seg)
                for seg in (item.get("path_segments") or item.get("path_original") or [])
                if _text(seg)
            ] or [name]
            path_text = _text(item.get("path_text") or item.get("category_path")) or " / ".join(path_segments)
            path_zh_segments = [
                _text(seg)
                for seg in (item.get("path_segments_zh") or item.get("path_cn") or [])
                if _text(seg)
            ]
            path_zh = _text(item.get("path_zh")) or (" / ".join(path_zh_segments) if path_zh_segments else "")
            ancestors = [
                _text(aid)
                for aid in (item.get("ancestor_ids") or [])
                if _text(aid)
            ]
            child_count = max(0, int(item.get("child_count") or 0))
            raw_obj = item.get("raw") if isinstance(item.get("raw"), dict) else {}

            node_rows.append(
                (
                    plat,
                    scope,
                    loc,
                    node_key,
                    category_id,
                    description_category_id,
                    parent_key,
                    level,
                    depth,
                    name,
                    name_zh,
                    path_text,
                    path_zh,
                    json.dumps(path_segments, ensure_ascii=False),
                    json.dumps(path_zh_segments, ensure_ascii=False),
                    json.dumps(ancestors, ensure_ascii=False),
                    child_count,
                    publishable,
                    json.dumps(raw_obj, ensure_ascii=False),
                )
            )
            if publishable:
                leaf_count += 1
                name_stemmed = " ".join(stemmed_search_tokens(name))
                path_stemmed = " ".join(stemmed_search_tokens(path_text))
                zh_tokens = cjk_index_tokens(f"{name_zh} {path_zh}") if (name_zh or path_zh) else ""
                fts_rows.append(
                    (
                        plat,
                        scope,
                        loc,
                        node_key,
                        category_id,
                        description_category_id,
                        name_stemmed,
                        path_stemmed,
                        zh_tokens,
                    )
                )

        with self._write_lock, self._connect() as conn:
            if scope == "global":
                # 公共全局类目树落库时顺带清除同平台同语种残留的非 global 历史副本。
                conn.execute(
                    "DELETE FROM taxonomy_snapshots WHERE platform = ? AND locale = ? AND scope_key != 'global'",
                    (plat, loc),
                )
                conn.execute(
                    "DELETE FROM taxonomy_nodes WHERE platform = ? AND locale = ? AND scope_key != 'global'",
                    (plat, loc),
                )
                conn.execute(
                    "DELETE FROM taxonomy_fts WHERE platform = ? AND locale = ? AND scope_key != 'global'",
                    (plat, loc),
                )
            conn.execute(
                "DELETE FROM taxonomy_nodes WHERE platform = ? AND scope_key = ? AND locale = ?",
                (plat, scope, loc),
            )
            conn.execute(
                "DELETE FROM taxonomy_fts WHERE platform = ? AND scope_key = ? AND locale = ?",
                (plat, scope, loc),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO taxonomy_nodes (
                    platform, scope_key, locale, node_key, category_id, description_category_id,
                    parent_key, level, depth, name, name_zh, path_text, path_zh,
                    path_json, path_zh_json, ancestor_ids_json, child_count, publishable, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                node_rows,
            )
            if fts_rows:
                conn.executemany(
                    """
                    INSERT INTO taxonomy_fts (
                        platform, scope_key, locale, node_key, category_id,
                        description_category_id, name_stemmed, path_stemmed, zh_tokens
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    fts_rows,
                )
            conn.execute(
                """
                INSERT INTO taxonomy_snapshots (
                    platform, scope_key, locale, corpus_hash, taxonomy_version,
                    node_count, leaf_count, retrieved_at, expires_at, stale_until, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, scope_key, locale) DO UPDATE SET
                    corpus_hash = excluded.corpus_hash,
                    taxonomy_version = excluded.taxonomy_version,
                    node_count = excluded.node_count,
                    leaf_count = excluded.leaf_count,
                    retrieved_at = excluded.retrieved_at,
                    expires_at = excluded.expires_at,
                    stale_until = excluded.stale_until,
                    source = excluded.source
                """,
                (
                    plat,
                    scope,
                    loc,
                    corpus_hash,
                    taxonomy_version,
                    len(node_rows),
                    leaf_count,
                    ts.isoformat(),
                    expires_at.isoformat(),
                    stale_until.isoformat(),
                    source,
                ),
            )
            conn.commit()

        self._clear_cooldown(f"taxonomy:{plat}:{scope}:{loc}")
        return TaxonomySnapshot(
            platform=plat,
            scope_key=scope,
            locale=loc,
            corpus_hash=corpus_hash,
            taxonomy_version=taxonomy_version,
            node_count=len(node_rows),
            leaf_count=leaf_count,
            retrieved_at=ts,
            expires_at=expires_at,
            stale_until=stale_until,
            source=source,
        )

    def ensure_taxonomy(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        *,
        live_loader: Callable[[], tuple[list[dict[str, Any]], str, str | None]],
        force_refresh: bool = False,
        fresh_ttl: timedelta,
        max_age: timedelta,
        is_transient_error: Callable[[Exception], bool],
        cooldown_seconds: float = STALE_RETRY_COOLDOWN_SECONDS,
        now: datetime | None = None,
    ) -> tuple[TaxonomySnapshot, str, bool]:
        """单飞确保类目树在 SQLite 中就绪；返回 ``(snapshot, cache_source, stale)``。"""

        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        flight_key = f"taxonomy:{plat}:{scope}:{loc}"

        if not force_refresh:
            current = now or _utc_now()
            existing = self.read_taxonomy_snapshot(plat, scope, loc, now=current)
            if existing is not None and existing.is_fresh(current):
                return existing, "persistent_cache", False
            if existing is not None and existing.can_serve_stale(current) and self._in_cooldown(flight_key):
                return existing, "stale_cache", True

        with self._flight_lock(flight_key):
            current = now or _utc_now()
            existing = self.read_taxonomy_snapshot(plat, scope, loc, now=current)
            if not force_refresh:
                if existing is not None and existing.is_fresh(current):
                    return existing, "persistent_cache", False
                if existing is not None and existing.can_serve_stale(current) and self._in_cooldown(flight_key):
                    return existing, "stale_cache", True

            try:
                nodes, corpus_hash, taxonomy_version = live_loader()
                snapshot = self.save_taxonomy(
                    plat,
                    scope,
                    loc,
                    nodes,
                    corpus_hash=corpus_hash,
                    taxonomy_version=taxonomy_version,
                    retrieved_at=now or _utc_now(),
                    fresh_ttl=fresh_ttl,
                    max_age=max_age,
                    source="remote_cache",
                )
                return snapshot, "remote_cache", False
            except Exception as exc:
                fallback_now = now or _utc_now()
                if (
                    existing is not None
                    and existing.can_serve_stale(fallback_now)
                    and is_transient_error(exc)
                ):
                    stale = not existing.is_fresh(fallback_now)
                    if stale:
                        self._set_cooldown(
                            flight_key,
                            min(
                                cooldown_seconds,
                                max(0.0, (existing.stale_until - fallback_now).total_seconds()),
                            ),
                        )
                    return existing, ("stale_cache" if stale else "persistent_cache"), stale
                raise

    @staticmethod
    def _row_to_leaf_record(row: sqlite3.Row) -> dict[str, Any]:
        platform = row["platform"]
        path_segments = json.loads(row["path_json"]) if row["path_json"] else []
        path_zh_segments = json.loads(row["path_zh_json"]) if row["path_zh_json"] else []
        ancestors = json.loads(row["ancestor_ids_json"]) if row["ancestor_ids_json"] else []
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        category_id = row["category_id"]
        desc_id = row["description_category_id"]
        record: dict[str, Any] = {
            "platform": platform,
            "site": "global",
            "node_key": row["node_key"],
            "category_id": category_id,
            "name_original": row["name"],
            "name_zh": row["name_zh"],
            "name_cn": row["name_zh"],
            "category_path": row["path_text"],
            "path_zh": row["path_zh"],
            "path_original": path_segments,
            "path_cn": path_zh_segments,
            "parent_id": desc_id if platform == "ozon" else (ancestors[-1] if ancestors else ""),
            "parent_key": row["parent_key"],
            "ancestor_ids": ancestors,
            "level": int(row["depth"]),
            "is_leaf": True,
            "raw": raw,
        }
        if platform == "ozon":
            record["type_id"] = category_id
            record["description_category_id"] = desc_id
            record["subject_id"] = desc_id
            record["path_ids"] = [*ancestors, desc_id] if desc_id and (not ancestors or ancestors[-1] != desc_id) else list(ancestors)
        return record

    @staticmethod
    def _row_to_tree_node(row: sqlite3.Row) -> dict[str, Any]:
        platform = row["platform"]
        level = row["level"]
        path_segments = json.loads(row["path_json"]) if row["path_json"] else []
        path_zh_segments = json.loads(row["path_zh_json"]) if row["path_zh_json"] else []
        node: dict[str, Any] = {
            "node_id": row["node_key"],
            "name": row["name"],
            "level": level,
            "depth": int(row["depth"]),
            "parent_id": row["parent_key"],
            "path_segments": path_segments,
            "child_count": int(row["child_count"]),
            "publishable": bool(row["publishable"]),
            "platform": platform,
            "site": "global",
        }
        if row["name_zh"]:
            node["name_zh"] = row["name_zh"]
        if path_zh_segments:
            node["path_segments_zh"] = path_zh_segments
        if level == "product_type":
            node["category_id"] = row["category_id"]
            if platform == "ozon":
                node["type_id"] = row["category_id"]
                node["description_category_id"] = row["description_category_id"]
        return node

    def list_leaf_records(
        self,
        platform: str,
        scope_key: str,
        locale: str,
    ) -> list[dict[str, Any]]:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ? AND publishable = 1
                ORDER BY category_id, description_category_id
                """,
                (plat, scope, loc),
            ).fetchall()
        return [self._row_to_leaf_record(row) for row in rows]

    def find_leaf_by_category_id(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        category_id: str,
        *,
        description_category_id: str = "",
    ) -> dict[str, Any] | None:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        cid = _text(category_id)
        desc_id = _text(description_category_id)
        if not cid:
            return None
        with self._connect() as conn:
            if desc_id:
                row = conn.execute(
                    """
                    SELECT * FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND category_id = ? AND description_category_id = ? AND publishable = 1
                    LIMIT 1
                    """,
                    (plat, scope, loc, cid, desc_id),
                ).fetchone()
                if row is not None:
                    return self._row_to_leaf_record(row)
            row = conn.execute(
                """
                SELECT * FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ?
                  AND category_id = ? AND publishable = 1
                ORDER BY description_category_id
                LIMIT 1
                """,
                (plat, scope, loc, cid),
            ).fetchone()
            if row is not None:
                return self._row_to_leaf_record(row)
            if plat == "ozon":
                # 兜底：若用户误填了唯一子类型的 description_category_id，仅在唯一匹配时返回。
                rows = conn.execute(
                    """
                    SELECT * FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND description_category_id = ? AND publishable = 1
                    LIMIT 2
                    """,
                    (plat, scope, loc, cid),
                ).fetchall()
                if len(rows) == 1:
                    return self._row_to_leaf_record(rows[0])
        return None

    def list_children(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        parent_keys: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        normalized_parents = list(dict.fromkeys(_text(p) for p in parent_keys if _text(p)))
        with self._connect() as conn:
            unknown: list[str] = []
            if normalized_parents:
                placeholders = ",".join("?" for _ in normalized_parents)
                found_rows = conn.execute(
                    f"""
                    SELECT node_key FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND node_key IN ({placeholders})
                    """,
                    (plat, scope, loc, *normalized_parents),
                ).fetchall()
                found = {row["node_key"] for row in found_rows}
                unknown = [p for p in normalized_parents if p not in found]
                if unknown:
                    return [], unknown
                child_rows = conn.execute(
                    f"""
                    SELECT * FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND parent_key IN ({placeholders})
                    ORDER BY LOWER(name), node_key
                    """,
                    (plat, scope, loc, *normalized_parents),
                ).fetchall()
            else:
                child_rows = conn.execute(
                    """
                    SELECT * FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND parent_key = ''
                    ORDER BY LOWER(name), node_key
                    """,
                    (plat, scope, loc),
                ).fetchall()
        seen: set[str] = set()
        nodes: list[dict[str, Any]] = []
        for row in child_rows:
            if row["node_key"] in seen:
                continue
            seen.add(row["node_key"])
            nodes.append(self._row_to_tree_node(row))
        return nodes, []

    def sibling_leaves(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        category_ids: list[str],
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """返回给定叶子类目同父分支下的相邻可发布叶子，供 AI 精排做细粒度对比。"""

        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        seeds = list(dict.fromkeys(_text(cid) for cid in category_ids if _text(cid)))
        if not seeds or limit <= 0:
            return []
        seed_set = set(seeds)
        placeholders = ",".join("?" for _ in seeds)
        with self._connect() as conn:
            parent_rows = conn.execute(
                f"""
                SELECT DISTINCT parent_key FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ?
                  AND category_id IN ({placeholders}) AND publishable = 1
                  AND parent_key != ''
                """,
                (plat, scope, loc, *seeds),
            ).fetchall()
            parent_keys = [row["parent_key"] for row in parent_rows if row["parent_key"]]
            if not parent_keys:
                return []
            p_placeholders = ",".join("?" for _ in parent_keys)
            sibling_rows = conn.execute(
                f"""
                SELECT * FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ?
                  AND parent_key IN ({p_placeholders}) AND publishable = 1
                ORDER BY LOWER(name), category_id
                LIMIT ?
                """,
                (plat, scope, loc, *parent_keys, limit + len(seed_set)),
            ).fetchall()
        results: list[dict[str, Any]] = []
        seen: set[str] = set(seed_set)
        for row in sibling_rows:
            cid = row["category_id"]
            if cid in seen:
                continue
            seen.add(cid)
            results.append(self._row_to_leaf_record(row))
            if len(results) >= limit:
                break
        return results

    def ancestor_ids_for(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        category_id: str,
    ) -> tuple[str, ...]:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        cid = _text(category_id)
        if not cid:
            return ()
        if cid.isascii() and cid.isdigit():
            cid = str(int(cid))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ancestor_ids_json FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ?
                  AND category_id = ?
                LIMIT 1
                """,
                (plat, scope, loc, cid),
            ).fetchone()
        if row is None or not row["ancestor_ids_json"]:
            return ()
        try:
            items = json.loads(row["ancestor_ids_json"])
        except ValueError:
            return ()
        return tuple(_text(item) for item in items if _text(item))

    def ancestor_map(
        self,
        platform: str,
        scope_key: str,
        locale: str,
    ) -> dict[str, tuple[str, ...]]:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT category_id, ancestor_ids_json FROM taxonomy_nodes
                WHERE platform = ? AND scope_key = ? AND locale = ? AND publishable = 1
                """,
                (plat, scope, loc),
            ).fetchall()
        mapping: dict[str, tuple[str, ...]] = {}
        for row in rows:
            cid = _text(row["category_id"])
            if not cid:
                continue
            try:
                items = json.loads(row["ancestor_ids_json"]) if row["ancestor_ids_json"] else []
            except ValueError:
                items = []
            mapping[cid] = tuple(_text(item) for item in items if _text(item))
        return mapping

    def search_taxonomy(
        self,
        platform: str,
        scope_key: str,
        locale: str,
        query: str,
        *,
        limit: int | None = 20,
    ) -> list[dict[str, Any]]:
        """FTS5 + 词干精确打分检索；同时支持纯 ID 直查、俄语词干及 Ozon 官方中文检索。"""

        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        loc = _text(locale) or "DEFAULT"
        raw_query = _text(query)
        if not raw_query:
            return []

        # 1. 纯数字 ID 精确直查（type_id / category_id / description_category_id）
        if raw_query.isascii() and raw_query.isdigit():
            normalized_id = str(int(raw_query))
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM taxonomy_nodes
                    WHERE platform = ? AND scope_key = ? AND locale = ?
                      AND publishable = 1
                      AND (category_id = ? OR category_id = ? OR description_category_id = ? OR description_category_id = ?)
                    ORDER BY (category_id = ? OR category_id = ?) DESC, path_text
                    """,
                    (plat, scope, loc, raw_query, normalized_id, raw_query, normalized_id, raw_query, normalized_id),
                ).fetchall()
            matches: list[dict[str, Any]] = []
            for row in rows:
                record = self._row_to_leaf_record(row)
                record["score"] = 100 if row["category_id"] in {raw_query, normalized_id} else 88
                record["matched_terms"] = [raw_query]
                matches.append(record)
            return matches if limit is None else matches[: max(1, min(50, int(limit or 20)))]

        # 2. 中文关键词检索（支持 Ozon 官方中文合表）
        if has_cjk(raw_query):
            zh_tokens = [tok for tok in cjk_index_tokens(raw_query).split() if tok]
            rows: list[sqlite3.Row] = []
            with self._connect() as conn:
                if zh_tokens:
                    fts_query = " OR ".join(
                        f"zh_tokens:{_fts_quote(tok)}" for tok in zh_tokens[:16] if _fts_quote(tok)
                    )
                    if fts_query:
                        rows = conn.execute(
                            """
                            SELECT n.*
                            FROM taxonomy_fts f
                            JOIN taxonomy_nodes n
                              ON n.platform = f.platform
                             AND n.scope_key = f.scope_key
                             AND n.locale = f.locale
                             AND n.node_key = f.node_key
                            WHERE f.platform = ? AND f.scope_key = ? AND f.locale = ?
                              AND taxonomy_fts MATCH ?
                              AND n.publishable = 1
                            ORDER BY bm25(taxonomy_fts)
                            LIMIT 400
                            """,
                            (plat, scope, loc, fts_query),
                        ).fetchall()
                if not rows:
                    like_pattern = f"%{raw_query}%"
                    rows = conn.execute(
                        """
                        SELECT * FROM taxonomy_nodes
                        WHERE platform = ? AND scope_key = ? AND locale = ?
                          AND publishable = 1
                          AND (name_zh LIKE ? OR path_zh LIKE ?)
                        LIMIT 400
                        """,
                        (plat, scope, loc, like_pattern, like_pattern),
                    ).fetchall()
            scored: list[dict[str, Any]] = []
            for row in rows:
                score, matched = score_cjk_search_match(raw_query, row["name_zh"], row["path_zh"])
                if not score:
                    continue
                record = self._row_to_leaf_record(row)
                record["score"] = score
                record["matched_terms"] = matched
                scored.append(record)
            scored.sort(key=lambda item: (-int(item["score"]), str(item.get("category_path") or "")))
            return scored if limit is None else scored[: max(1, min(50, int(limit or 20)))]

        # 3. 俄语 / 拉丁文词干检索（FTS5 召回 + 精确词形打分，保持非前缀约束）
        terms = normalize_search_terms(raw_query)
        if not terms:
            return []
        query_stems = list(dict.fromkeys(stemmed_search_tokens(raw_query)))
        if not query_stems:
            return []

        fts_clauses = [
            f"(name_stemmed:{_fts_quote(stem)} OR path_stemmed:{_fts_quote(stem)})"
            for stem in query_stems[:12]
            if _fts_quote(stem)
        ]
        if not fts_clauses:
            return []
        fts_query = " OR ".join(fts_clauses)
        # 无条数上限（AI 全量检索）时不裁剪 FTS 召回集，保证完整覆盖。
        sql_limit = 5000 if limit is None else max(300, int(limit) * 15)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT n.*, f.name_stemmed AS fts_name_stemmed, f.path_stemmed AS fts_path_stemmed
                FROM taxonomy_fts f
                JOIN taxonomy_nodes n
                  ON n.platform = f.platform
                 AND n.scope_key = f.scope_key
                 AND n.locale = f.locale
                 AND n.node_key = f.node_key
                WHERE f.platform = ? AND f.scope_key = ? AND f.locale = ?
                  AND taxonomy_fts MATCH ?
                  AND n.publishable = 1
                ORDER BY bm25(taxonomy_fts, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 1.0, 0.0)
                LIMIT ?
                """,
                (plat, scope, loc, fts_query, sql_limit),
            ).fetchall()

        scored_results: list[dict[str, Any]] = []
        for row in rows:
            name_stems = set(str(row["fts_name_stemmed"] or "").split())
            path_stems = name_stems | set(str(row["fts_path_stemmed"] or "").split())
            score, matched_terms = score_russian_search_match(terms, name_stems, path_stems)
            if not score:
                continue
            record = self._row_to_leaf_record(row)
            record["score"] = score
            record["matched_terms"] = matched_terms
            scored_results.append(record)
        scored_results.sort(
            key=lambda item: (-int(item["score"]), str(item.get("category_path") or ""))
        )
        return scored_results if limit is None else scored_results[: max(1, min(50, int(limit or 20)))]

    def clear_taxonomy(
        self,
        platform: str | None = None,
        *,
        scope_key: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if platform:
            clauses.append("platform = ?")
            params.append(_text(platform).lower())
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(_text(scope_key))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM taxonomy_snapshots {where}", params)
            conn.execute(f"DELETE FROM taxonomy_nodes {where}", params)
            conn.execute(f"DELETE FROM taxonomy_fts {where}", params)
            conn.commit()
            removed = cur.rowcount
        with self._meta_lock:
            prefix = f"taxonomy:{_text(platform).lower()}:" if platform else "taxonomy:"
            for key in [k for k in self._cooldowns if k.startswith(prefix)]:
                if not scope_key or f":{_text(scope_key)}:" in key:
                    self._cooldowns.pop(key, None)
        return max(0, int(removed))

    # -----------------------------------------------------------------------
    # 2. CategoryDefinition 持久缓存（含内存 LRU + Singleflight + 冷却）
    # -----------------------------------------------------------------------

    def read_definition(
        self,
        platform: str,
        scope_key: str,
        site: str,
        category_id: str,
        *,
        format_version: int = DEFINITION_FORMAT_VERSION,
        now: datetime | None = None,
    ) -> CategoryDefinitionCacheEntry | None:
        plat = _text(platform).lower()
        scope = _text(scope_key) or "public"
        site_key = _text(site)
        cid = _text(category_id)
        fv = int(format_version)
        lru_key = (plat, scope, site_key.lower(), cid, fv)
        current = now or _utc_now()

        with self._meta_lock:
            cached = self._definition_lru.get(lru_key)
            if cached is not None:
                if cached.can_serve_stale(current):
                    self._definition_lru.move_to_end(lru_key)
                    return cached
                self._definition_lru.pop(lru_key, None)

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT platform, scope_key, site, category_id, format_version,
                       fingerprint, definition_json, retrieved_at, expires_at, stale_until, source
                FROM category_definitions
                WHERE platform = ? AND scope_key = ? AND LOWER(site) = LOWER(?)
                  AND category_id = ? AND format_version = ?
                LIMIT 1
                """,
                (plat, scope, site_key, cid, fv),
            ).fetchone()
        if row is None:
            return None
        retrieved_at = _parse_utc(row["retrieved_at"])
        expires_at = _parse_utc(row["expires_at"])
        stale_until = _parse_utc(row["stale_until"])
        if retrieved_at is None or expires_at is None or stale_until is None:
            return None
        if current >= stale_until:
            return None
        try:
            definition_payload = json.loads(row["definition_json"])
            definition = CategoryDefinition.model_validate(definition_payload)
        except (ValueError, TypeError):
            logger.warning("SQLite 类目定义反序列化失败，忽略：%s/%s", plat, cid, exc_info=True)
            return None
        fingerprint = _text(row["fingerprint"])
        computed = definition_fingerprint(definition)
        if fingerprint and fingerprint != computed:
            logger.warning("SQLite 类目定义指纹不一致，忽略：%s/%s", plat, cid)
            return None
        entry = CategoryDefinitionCacheEntry(
            platform=plat,
            credential_scope_hash=scope,
            site=row["site"],
            category_id=cid,
            format_version=fv,
            definition=definition,
            fingerprint=fingerprint or computed,
            retrieved_at=retrieved_at,
            expires_at=expires_at,
            stale_until=stale_until,
            source=row["source"] or "live",
        )
        with self._meta_lock:
            self._definition_lru[lru_key] = entry
            self._definition_lru.move_to_end(lru_key)
            while len(self._definition_lru) > _DEFINITION_LRU_MAXSIZE:
                self._definition_lru.popitem(last=False)
        return entry

    def write_definition(self, entry: CategoryDefinitionCacheEntry) -> None:
        lru_key = (
            entry.platform,
            entry.credential_scope_hash,
            entry.site.lower(),
            entry.category_id,
            entry.format_version,
        )
        encoded = json.dumps(entry.definition.model_dump(mode="json"), ensure_ascii=False)
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO category_definitions (
                    platform, scope_key, site, category_id, format_version,
                    description_category_id, category_path, fingerprint,
                    definition_json, retrieved_at, expires_at, stale_until, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, scope_key, site, category_id, format_version) DO UPDATE SET
                    description_category_id = excluded.description_category_id,
                    category_path = excluded.category_path,
                    fingerprint = excluded.fingerprint,
                    definition_json = excluded.definition_json,
                    retrieved_at = excluded.retrieved_at,
                    expires_at = excluded.expires_at,
                    stale_until = excluded.stale_until,
                    source = excluded.source
                """,
                (
                    entry.platform,
                    entry.credential_scope_hash,
                    entry.site,
                    entry.category_id,
                    entry.format_version,
                    entry.definition.description_category_id,
                    entry.definition.category_path,
                    entry.fingerprint,
                    encoded,
                    entry.retrieved_at.isoformat(),
                    entry.expires_at.isoformat(),
                    entry.stale_until.isoformat(),
                    entry.source,
                ),
            )
            conn.commit()
        with self._meta_lock:
            self._definition_lru[lru_key] = entry
            self._definition_lru.move_to_end(lru_key)
            while len(self._definition_lru) > _DEFINITION_LRU_MAXSIZE:
                self._definition_lru.popitem(last=False)
        self._maybe_prune_expired(now_dt=entry.retrieved_at, now_ts=time.time())

    def load_definition_through_cache(
        self,
        *,
        platform: str,
        credential_scope_hash: str,
        site: str,
        category_id: str,
        live_loader: Callable[[], CategoryDefinition],
        is_transient_error: Callable[[BaseException], bool],
        unavailable_error_factory: Callable[[str], BaseException],
        force_refresh: bool = False,
        fresh_ttl: timedelta = DEFINITION_CACHE_FRESH_TTL,
        max_age: timedelta = DEFINITION_CACHE_MAX_AGE,
        cooldown_seconds: float = STALE_RETRY_COOLDOWN_SECONDS,
        now: datetime | None = None,
    ) -> CategoryDefinition:
        plat = _text(platform).lower()
        scope = _text(credential_scope_hash) or "public"
        site_key = _text(site)
        cid = _text(category_id)
        flight_key = f"def:{plat}:{scope}:{site_key.lower()}:{cid}"

        if not force_refresh:
            current = now or _utc_now()
            cached = self.read_definition(plat, scope, site_key, cid, now=current)
            if cached is not None and cached.is_fresh(current):
                return cached.definition.model_copy(
                    update={
                        "fingerprint": cached.fingerprint,
                        "cache": cached.cache_state(stale=False, source="cache"),
                    }
                )
            if cached is not None and cached.can_serve_stale(current) and self._in_cooldown(flight_key):
                return cached.definition.model_copy(
                    update={
                        "fingerprint": cached.fingerprint,
                        "cache": cached.cache_state(stale=True, source="stale"),
                    }
                )

        with self._flight_lock(flight_key):
            current = now or _utc_now()
            cached = None if force_refresh else self.read_definition(
                plat, scope, site_key, cid, now=current
            )
            if cached is not None and cached.is_fresh(current):
                return cached.definition.model_copy(
                    update={
                        "fingerprint": cached.fingerprint,
                        "cache": cached.cache_state(stale=False, source="cache"),
                    }
                )
            if (
                not force_refresh
                and cached is not None
                and cached.can_serve_stale(current)
                and self._in_cooldown(flight_key)
            ):
                return cached.definition.model_copy(
                    update={
                        "fingerprint": cached.fingerprint,
                        "cache": cached.cache_state(stale=True, source="stale"),
                    }
                )

            try:
                live = live_loader()
            except Exception as exc:
                if (
                    cached is not None
                    and cached.can_serve_stale(current)
                    and is_transient_error(exc)
                ):
                    self._set_cooldown(
                        flight_key,
                        min(
                            cooldown_seconds,
                            max(0.0, (cached.stale_until - current).total_seconds()),
                        ),
                    )
                    logger.warning(
                        "类目定义 live 读取失败，返回 stale 缓存：%s/%s (%s)",
                        plat,
                        cid,
                        exc,
                    )
                    return cached.definition.model_copy(
                        update={
                            "fingerprint": cached.fingerprint,
                            "cache": cached.cache_state(stale=True, source="stale"),
                        }
                    )
                if cached is None and is_transient_error(exc):
                    raise unavailable_error_factory(f"类目属性定义暂时不可用：{exc}") from exc
                raise

            retrieved_at = current
            fingerprint = definition_fingerprint(live)
            entry = CategoryDefinitionCacheEntry(
                platform=plat,
                credential_scope_hash=scope,
                site=site_key,
                category_id=cid,
                format_version=DEFINITION_FORMAT_VERSION,
                definition=live,
                fingerprint=fingerprint,
                retrieved_at=retrieved_at,
                expires_at=retrieved_at + fresh_ttl,
                stale_until=retrieved_at + max_age,
                source="live",
            )
            try:
                self.write_definition(entry)
            except sqlite3.Error:
                logger.warning("写入 SQLite 类目定义缓存失败", exc_info=True)
            self._clear_cooldown(flight_key)
            return live.model_copy(
                update={
                    "fingerprint": fingerprint,
                    "cache": entry.cache_state(stale=False, source="live"),
                }
            )

    def clear_definitions(
        self,
        platform: str | None = None,
        *,
        scope_key: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if platform:
            clauses.append("platform = ?")
            params.append(_text(platform).lower())
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(_text(scope_key))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM category_definitions {where}", params)
            conn.commit()
            removed = cur.rowcount
        with self._meta_lock:
            plat_filter = _text(platform).lower() if platform else ""
            scope_filter = _text(scope_key) if scope_key else ""
            for key in list(self._definition_lru):
                if (not plat_filter or key[0] == plat_filter) and (not scope_filter or key[1] == scope_filter):
                    self._definition_lru.pop(key, None)
            prefix = f"def:{plat_filter}:" if plat_filter else "def:"
            for key in [k for k in self._cooldowns if k.startswith(prefix)]:
                if not scope_filter or f":{scope_filter}:" in key:
                    self._cooldowns.pop(key, None)
        return max(0, int(removed))

    # -----------------------------------------------------------------------
    # 3. 枚举页与只读平台 HTTP 缓存（锁外执行网络 IO + Singleflight）
    # -----------------------------------------------------------------------

    def get_cached_value(self, cache_key: str, *, now: float | None = None) -> Any | None:
        ts = time.time() if now is None else float(now)
        with self._meta_lock:
            mem = self._value_lru.get(cache_key)
            if mem is not None:
                if mem[0] > ts:
                    self._value_lru.move_to_end(cache_key)
                    return deepcopy(mem[1])
                self._value_lru.pop(cache_key, None)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM category_value_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None or float(row["expires_at"]) <= ts:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError):
            return None
        with self._meta_lock:
            self._value_lru[cache_key] = (float(row["expires_at"]), deepcopy(payload))
            self._value_lru.move_to_end(cache_key)
            while len(self._value_lru) > _VALUE_LRU_MAXSIZE:
                self._value_lru.popitem(last=False)
        return payload

    def set_cached_value(
        self,
        cache_key: str,
        platform: str,
        scope_key: str,
        value: Any,
        *,
        ttl_seconds: float,
        now: float | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            return
        ts = time.time() if now is None else float(now)
        expires_at = ts + float(ttl_seconds)
        encoded = json.dumps(value, ensure_ascii=False)
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO category_value_cache (
                    cache_key, platform, scope_key, payload_json, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    platform = excluded.platform,
                    scope_key = excluded.scope_key,
                    payload_json = excluded.payload_json,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (cache_key, _text(platform).lower(), _text(scope_key) or "public", encoded, expires_at, ts),
            )
            conn.commit()
        with self._meta_lock:
            self._value_lru[cache_key] = (expires_at, deepcopy(value))
            self._value_lru.move_to_end(cache_key)
            while len(self._value_lru) > _VALUE_LRU_MAXSIZE:
                self._value_lru.popitem(last=False)
        self._maybe_prune_expired(now_dt=_utc_now(), now_ts=ts)

    def load_cached_value(
        self,
        cache_key: str,
        *,
        platform: str,
        scope_key: str,
        loader: Callable[[], Any],
        ttl_seconds: float,
        force_refresh: bool = False,
        now: float | None = None,
    ) -> Any:
        if not force_refresh:
            hit = self.get_cached_value(cache_key, now=now)
            if hit is not None:
                return hit
        with self._flight_lock(f"val:{cache_key}"):
            if not force_refresh:
                hit = self.get_cached_value(cache_key, now=now)
                if hit is not None:
                    return hit
            result = loader()
            try:
                self.set_cached_value(
                    cache_key,
                    platform,
                    scope_key,
                    result,
                    ttl_seconds=ttl_seconds,
                    now=now,
                )
            except sqlite3.Error:
                logger.warning("写入 SQLite 枚举/HTTP 缓存失败", exc_info=True)
            return deepcopy(result)

    def clear_cached_values(
        self,
        platform: str | None = None,
        *,
        scope_key: str | None = None,
        key_prefix: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if platform:
            clauses.append("platform = ?")
            params.append(_text(platform).lower())
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(_text(scope_key))
        if key_prefix:
            clauses.append("cache_key LIKE ?")
            params.append(f"{key_prefix}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM category_value_cache {where}", params)
            conn.commit()
            removed = cur.rowcount
        with self._meta_lock:
            if not platform and not scope_key and not key_prefix:
                self._value_lru.clear()
            else:
                for key in list(self._value_lru):
                    if key_prefix and not key.startswith(key_prefix):
                        continue
                    if platform and f":{_text(platform).lower()}:" not in f":{key}:":
                        # 兜底全清命中前缀/模糊键，防止内存残留过期值。
                        pass
                    self._value_lru.pop(key, None)
        return max(0, int(removed))

    # -----------------------------------------------------------------------
    # 4. 服务端持久化翻译词典（L2 全局复用）
    # -----------------------------------------------------------------------

    def get_glossary(
        self,
        domain: str,
        target_language: str,
        texts: Iterable[str],
        *,
        fallback_general: bool = True,
    ) -> dict[str, str]:
        """按 (domain, target_language, sha256(text)) 批量查询已缓存译文。"""

        dom = _text(domain).lower() or "general"
        lang = _text(target_language)
        if not lang:
            return {}
        unique_texts = list(dict.fromkeys(_text(t) for t in texts if _text(t)))
        if not unique_texts:
            return {}

        results: dict[str, str] = {}
        misses_by_hash: dict[str, str] = {}

        with self._meta_lock:
            for text in unique_texts:
                digest = _source_hash(text)
                hit = self._glossary_lru.get((dom, lang, digest))
                if hit is None and fallback_general and dom != "general":
                    hit = self._glossary_lru.get(("general", lang, digest))
                if hit is not None:
                    results[text] = hit
                else:
                    misses_by_hash[digest] = text

        if not misses_by_hash:
            return results

        hashes = list(misses_by_hash)
        domains = [dom, "general"] if (fallback_general and dom != "general") else [dom]
        d_placeholders = ",".join("?" for _ in domains)
        found_rows: dict[tuple[str, str], str] = {}
        with self._connect() as conn:
            # SQLite 单次 IN 参数上限保守按 400 分片
            for start in range(0, len(hashes), 400):
                chunk = hashes[start : start + 400]
                h_placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT domain, source_hash, translated_text
                    FROM translation_glossary
                    WHERE domain IN ({d_placeholders})
                      AND target_language = ?
                      AND source_hash IN ({h_placeholders})
                    """,
                    (*domains, lang, *chunk),
                ).fetchall()
                for row in rows:
                    found_rows[(row["domain"], row["source_hash"])] = row["translated_text"]

        with self._meta_lock:
            for digest, text in misses_by_hash.items():
                translated = found_rows.get((dom, digest))
                matched_domain = dom
                if translated is None and fallback_general and dom != "general":
                    translated = found_rows.get(("general", digest))
                    matched_domain = "general"
                if translated:
                    results[text] = translated
                    lru_key = (matched_domain, lang, digest)
                    self._glossary_lru[lru_key] = translated
                    self._glossary_lru.move_to_end(lru_key)
                    while len(self._glossary_lru) > _GLOSSARY_LRU_MAXSIZE:
                        self._glossary_lru.popitem(last=False)
        return results

    def put_glossary(
        self,
        domain: str,
        target_language: str,
        entries: Mapping[str, str],
        *,
        also_seed_general: bool = False,
    ) -> int:
        dom = _text(domain).lower() or "general"
        lang = _text(target_language)
        if not lang or not entries:
            return 0
        now_ts = time.time()
        rows: list[tuple[Any, ...]] = []
        lru_updates: list[tuple[tuple[str, str, str], str]] = []
        for raw_src, raw_dst in entries.items():
            src = _text(raw_src)
            dst = _text(raw_dst)
            if not src or not dst:
                continue
            digest = _source_hash(src)
            rows.append((dom, lang, digest, src, dst, now_ts, now_ts))
            lru_updates.append(((dom, lang, digest), dst))
            if also_seed_general and dom != "general":
                rows.append(("general", lang, digest, src, dst, now_ts, now_ts))
                lru_updates.append((("general", lang, digest), dst))
        if not rows:
            return 0
        with self._write_lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO translation_glossary (
                    domain, target_language, source_hash, source_text,
                    translated_text, hit_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(domain, target_language, source_hash) DO UPDATE SET
                    translated_text = excluded.translated_text,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()
        with self._meta_lock:
            for key, dst in lru_updates:
                self._glossary_lru[key] = dst
                self._glossary_lru.move_to_end(key)
                while len(self._glossary_lru) > _GLOSSARY_LRU_MAXSIZE:
                    self._glossary_lru.popitem(last=False)
        return len(rows)

    def reverse_lookup_glossary(
        self,
        domain: str,
        target_language: str,
        translated_query: str,
        *,
        exact_only: bool = True,
        limit: int = 3,
    ) -> list[str]:
        """按已缓存中文译文反查源语言短词条（排除整串多级路径，0ms 本地命中）。"""

        dom = _text(domain).lower() or "general"
        lang = _text(target_language)
        needle = _text(translated_query)
        if not lang or not needle:
            return []
        safe_limit = max(1, min(10, int(limit or 3)))
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_pattern = f"%{escaped}%"

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_text, translated_text
                FROM translation_glossary
                WHERE domain = ? AND target_language = ?
                  AND source_text NOT LIKE '% / %'
                  AND translated_text LIKE ? ESCAPE '\\'
                ORDER BY length(translated_text) ASC, length(source_text) ASC
                LIMIT 50
                """,
                (dom, lang, like_pattern),
            ).fetchall()

        exact_hits: list[str] = []
        substring_hits: list[str] = []
        seen: set[str] = set()
        needle_cf = needle.casefold()
        for row in rows:
            src = _text(row["source_text"])
            dst = _text(row["translated_text"])
            if not src or not dst or src.casefold() in seen:
                continue
            parts = [
                part.strip().casefold()
                for part in dst.replace("，", ",").replace("、", ",").replace("/", ",").split(",")
                if part.strip()
            ]
            is_exact = dst.casefold() == needle_cf or needle_cf in parts
            if is_exact:
                exact_hits.append(src)
                seen.add(src.casefold())
            elif not exact_only:
                substring_hits.append(src)
                seen.add(src.casefold())
            if len(exact_hits) >= safe_limit:
                break
        if exact_only:
            return exact_hits[:safe_limit]
        return (exact_hits + substring_hits)[:safe_limit]

    def clear_glossary(
        self,
        domain: str | None = None,
        *,
        target_language: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if domain:
            clauses.append("domain = ?")
            params.append(_text(domain).lower())
        if target_language:
            clauses.append("target_language = ?")
            params.append(_text(target_language))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM translation_glossary {where}", params)
            conn.commit()
            removed = cur.rowcount
        with self._meta_lock:
            self._glossary_lru.clear()
        return max(0, int(removed))


__all__ = [
    "CategoryCatalogStore",
    "CategoryDefinitionCacheEntry",
    "DEFINITION_CACHE_FRESH_TTL",
    "DEFINITION_CACHE_MAX_AGE",
    "STALE_RETRY_COOLDOWN_SECONDS",
    "STORE_SCHEMA_VERSION",
    "TaxonomySnapshot",
]
