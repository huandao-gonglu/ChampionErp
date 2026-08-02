# -*- coding: utf-8 -*-
"""Ozon 类目语料的版本化持久化缓存。

缓存只保存展平后的公开类目数据和 Client ID 的单向摘要，不保存 Client ID、
API Key 或其他凭据。文件写入使用同目录临时文件 + ``os.replace``，避免进程中断
留下半截 JSON。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any


OZON_CATEGORY_CACHE_SCHEMA = "ozon.category-corpus.v1"
OZON_CATEGORY_CACHE_FRESH_TTL = timedelta(days=1)
OZON_CATEGORY_CACHE_MAX_AGE = timedelta(days=7)

_CACHE_DIRECTORY_NAME = "ozon_categories"
_CACHE_FILE_PATTERN = re.compile(r"^corpus-v1-([0-9a-f]{64})\.json\.gz$")


@dataclass(frozen=True)
class OzonCategoryCacheEntry:
    credential_scope_hash: str
    corpus_hash: str
    taxonomy_version: str | None
    locale: str
    retrieved_at: datetime
    records: list[dict[str, Any]]

    @property
    def expires_at(self) -> datetime:
        return self.retrieved_at + OZON_CATEGORY_CACHE_FRESH_TTL

    @property
    def stale_until(self) -> datetime:
        return self.retrieved_at + OZON_CATEGORY_CACHE_MAX_AGE

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at

    def can_serve_stale(self, now: datetime) -> bool:
        return now < self.stale_until


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scope_digest(credential_scope_hash: str) -> str:
    value = str(credential_scope_hash or "").strip().lower()
    digest = value.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Ozon 类目缓存的凭据作用域摘要无效。")
    return digest


def _cache_directory(cache_root: Path) -> Path:
    return Path(cache_root) / _CACHE_DIRECTORY_NAME


def _cache_path(cache_root: Path, credential_scope_hash: str) -> Path:
    digest = _scope_digest(credential_scope_hash)
    return _cache_directory(cache_root) / f"corpus-v1-{digest}.json.gz"


def read_ozon_category_cache(
    cache_root: Path,
    credential_scope_hash: str,
    *,
    now: datetime | None = None,
) -> OzonCategoryCacheEntry | None:
    """读取仍在 7 天可用窗口内的缓存；损坏或不匹配的文件视为未命中。"""

    path = _cache_path(cache_root, credential_scope_hash)
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != OZON_CATEGORY_CACHE_SCHEMA:
        return None
    if str(payload.get("credential_scope_hash") or "") != credential_scope_hash:
        return None
    retrieved_at = _parse_utc_datetime(payload.get("retrieved_at"))
    records = payload.get("records")
    corpus_hash = str(payload.get("corpus_hash") or "").strip()
    if (
        retrieved_at is None
        or not isinstance(records, list)
        or not records
        or not all(isinstance(record, dict) for record in records)
        or not corpus_hash.startswith("sha256:")
    ):
        return None
    entry = OzonCategoryCacheEntry(
        credential_scope_hash=credential_scope_hash,
        corpus_hash=corpus_hash,
        taxonomy_version=(
            str(payload["taxonomy_version"])
            if payload.get("taxonomy_version") is not None
            else None
        ),
        locale=str(payload.get("locale") or "ru-RU"),
        retrieved_at=retrieved_at,
        records=[dict(record) for record in records],
    )
    if not entry.can_serve_stale(now or _utc_now()):
        return None
    return entry


def write_ozon_category_cache(
    cache_root: Path,
    entry: OzonCategoryCacheEntry,
) -> Path:
    """原子写入压缩 JSON 缓存，并限制文件权限。"""

    path = _cache_path(cache_root, entry.credential_scope_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    payload = {
        "schema": OZON_CATEGORY_CACHE_SCHEMA,
        "credential_scope_hash": entry.credential_scope_hash,
        "corpus_hash": entry.corpus_hash,
        "taxonomy_version": entry.taxonomy_version,
        "locale": entry.locale,
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


def clear_ozon_category_cache(
    cache_root: Path,
    *,
    credential_scope_hash: str | None = None,
) -> int:
    """删除一个凭据作用域或全部受管 Ozon 类目缓存文件。"""

    directory = _cache_directory(cache_root)
    if credential_scope_hash:
        paths = [_cache_path(cache_root, credential_scope_hash)]
    else:
        try:
            paths = [
                path
                for path in directory.iterdir()
                if path.is_file() and _CACHE_FILE_PATTERN.fullmatch(path.name)
            ]
        except FileNotFoundError:
            return 0
    removed = 0
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed += 1
    return removed


__all__ = [
    "OZON_CATEGORY_CACHE_FRESH_TTL",
    "OZON_CATEGORY_CACHE_MAX_AGE",
    "OZON_CATEGORY_CACHE_SCHEMA",
    "OzonCategoryCacheEntry",
    "clear_ozon_category_cache",
    "read_ozon_category_cache",
    "write_ozon_category_cache",
]
