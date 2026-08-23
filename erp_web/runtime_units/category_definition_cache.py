# -*- coding: utf-8 -*-
"""类目属性定义持久缓存（类目 Schema 分离计划 Phase 1）。

缓存属于 Provider/Catalog 基础设施，不进入商品、草稿、发布任务或 Agent
history。缓存键包含 platform + credential_scope_hash + site + category_id +
definition_format_version；记录携带 fingerprint、retrieved_at、expires_at、
stale_until 与 source。

读取规则：
1. fresh cache：直接返回（source=cache）；
2. 无 fresh cache：请求 live；成功则归一化、计算指纹并原子写回；
3. live 遇到瞬时错误（timeout/连接失败/429/平台 5xx）：允许返回仍在
   stale 窗口内的定义（source=stale, cache.stale=True）；
4. 401/403/凭据缺失/类目禁用/结构错误：不得使用 stale 掩盖；
5. 超过 stale_until：抛出可重试 CATEGORY_ATTRIBUTES_UNAVAILABLE。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from erp_web.schemas.category_definition import (
    DEFINITION_FORMAT_VERSION,
    CategoryCacheState,
    CategoryDefinition,
    definition_fingerprint,
)

from .category_definition_support import (
    CategoryAttributesUnavailableError,
    is_transient_category_api_error,
)

logger = logging.getLogger(__name__)

DEFINITION_CACHE_SCHEMA = "category.definition.v1"
DEFINITION_CACHE_FRESH_TTL = timedelta(hours=24)
DEFINITION_CACHE_MAX_AGE = timedelta(days=7)
#: stale 命中后的重试冷却，避免瞬时故障期间反复打平台接口。
DEFINITION_STALE_RETRY_COOLDOWN_SECONDS = 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class CategoryDefinitionCacheEntry:
    """单条定义缓存记录。"""

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


def _cache_directory(cache_root: Path, platform: str) -> Path:
    safe_platform = "".join(
        char if char.isalnum() or char in "-_" else "-"
        for char in str(platform or "unknown").strip().lower()
    )
    return Path(cache_root) / "category_definitions" / safe_platform


def _cache_path(
    cache_root: Path,
    *,
    platform: str,
    credential_scope_hash: str,
    site: str,
    category_id: str,
    format_version: int,
) -> Path:
    scope_digest = hashlib.sha256(
        str(credential_scope_hash or "public").encode("utf-8")
    ).hexdigest()[:32]
    identity_digest = hashlib.sha256(
        f"{str(site or '').strip().lower()}|{str(category_id or '').strip()}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    return (
        _cache_directory(cache_root, platform)
        / f"definition-v{int(format_version)}-{scope_digest}-{identity_digest}.json.gz"
    )


def read_definition_cache(
    cache_root: Path,
    *,
    platform: str,
    credential_scope_hash: str,
    site: str,
    category_id: str,
    format_version: int = DEFINITION_FORMAT_VERSION,
    now: datetime | None = None,
) -> CategoryDefinitionCacheEntry | None:
    """读取仍在 stale 窗口内的缓存记录；过期/损坏/版本不符返回 None。"""

    path = _cache_path(
        cache_root,
        platform=platform,
        credential_scope_hash=credential_scope_hash,
        site=site,
        category_id=category_id,
        format_version=format_version,
    )
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        logger.warning("定义缓存损坏，忽略：%s", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    if (
        str(payload.get("schema") or "") != DEFINITION_CACHE_SCHEMA
        or int(payload.get("format_version") or 0) != int(format_version)
        or str(payload.get("platform") or "") != str(platform or "").strip().lower()
        or str(payload.get("credential_scope_hash") or "")
        != str(credential_scope_hash or "public")
    ):
        return None
    retrieved_at = _parse_datetime(payload.get("retrieved_at"))
    expires_at = _parse_datetime(payload.get("expires_at"))
    stale_until = _parse_datetime(payload.get("stale_until"))
    if retrieved_at is None or expires_at is None or stale_until is None:
        return None
    current = now or _utc_now()
    if current >= stale_until:
        return None
    definition_payload = payload.get("definition")
    if not isinstance(definition_payload, dict):
        return None
    try:
        definition = CategoryDefinition.model_validate(definition_payload)
    except ValueError:
        logger.warning("定义缓存反序列化失败，忽略：%s", path, exc_info=True)
        return None
    fingerprint = str(payload.get("fingerprint") or "").strip()
    if fingerprint and fingerprint != definition_fingerprint(definition):
        logger.warning("定义缓存指纹不一致，忽略：%s", path)
        return None
    return CategoryDefinitionCacheEntry(
        platform=str(platform or "").strip().lower(),
        credential_scope_hash=str(credential_scope_hash or "public"),
        site=str(site or "").strip(),
        category_id=str(category_id or "").strip(),
        format_version=int(format_version),
        definition=definition,
        fingerprint=fingerprint or definition_fingerprint(definition),
        retrieved_at=retrieved_at,
        expires_at=expires_at,
        stale_until=stale_until,
        source=str(payload.get("source") or "live"),
    )


def write_definition_cache(
    cache_root: Path,
    entry: CategoryDefinitionCacheEntry,
) -> None:
    """原子写入定义缓存（临时文件 + os.replace，gzip，私有权限）。"""

    directory = _cache_directory(cache_root, entry.platform)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    payload = {
        "schema": DEFINITION_CACHE_SCHEMA,
        "format_version": entry.format_version,
        "platform": entry.platform,
        "credential_scope_hash": entry.credential_scope_hash,
        "site": entry.site,
        "category_id": entry.category_id,
        "fingerprint": entry.fingerprint,
        "retrieved_at": entry.retrieved_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "stale_until": entry.stale_until.isoformat(),
        "source": entry.source,
        "definition": entry.definition.model_dump(mode="json"),
    }
    target = _cache_path(
        cache_root,
        platform=entry.platform,
        credential_scope_hash=entry.credential_scope_hash,
        site=entry.site,
        category_id=entry.category_id,
        format_version=entry.format_version,
    )
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory),
        prefix=".definition-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            with gzip.open(handle, "wt", encoding="utf-8") as compressed:
                json.dump(payload, compressed, ensure_ascii=False)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def clear_definition_cache(
    cache_root: Path,
    *,
    platform: str | None = None,
) -> int:
    """删除一个平台或全部定义缓存文件；返回删除数量。"""

    base = Path(cache_root) / "category_definitions"
    if not base.exists():
        return 0
    directories = (
        [_cache_directory(cache_root, platform)]
        if platform
        else [item for item in base.iterdir() if item.is_dir()]
    )
    removed = 0
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob("definition-v*.json.gz"):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed += 1
    return removed


def load_definition_through_cache(
    *,
    cache_root: Path,
    platform: str,
    credential_scope_hash: str,
    site: str,
    category_id: str,
    live_loader: Callable[[], CategoryDefinition],
    fresh_ttl: timedelta = DEFINITION_CACHE_FRESH_TTL,
    max_age: timedelta = DEFINITION_CACHE_MAX_AGE,
    now: datetime | None = None,
) -> CategoryDefinition:
    """按 §5.2 读取规则执行 fresh/live/stale/expired 四条路径。"""

    current = now or _utc_now()
    cached = read_definition_cache(
        cache_root,
        platform=platform,
        credential_scope_hash=credential_scope_hash,
        site=site,
        category_id=category_id,
        now=current,
    )
    if cached is not None and cached.is_fresh(current):
        return cached.definition.model_copy(
            update={
                "fingerprint": cached.fingerprint,
                "cache": cached.cache_state(stale=False, source="cache"),
            }
        )

    try:
        live = live_loader()
    except Exception as exc:
        if (
            cached is not None
            and cached.can_serve_stale(current)
            and is_transient_category_api_error(exc)
        ):
            logger.warning(
                "类目定义 live 读取失败，返回 stale 缓存：%s/%s (%s)",
                platform,
                category_id,
                exc,
            )
            return cached.definition.model_copy(
                update={
                    "fingerprint": cached.fingerprint,
                    "cache": cached.cache_state(stale=True, source="stale"),
                }
            )
        if cached is None and is_transient_category_api_error(exc):
            raise CategoryAttributesUnavailableError(
                f"类目属性定义暂时不可用：{exc}"
            ) from exc
        raise

    retrieved_at = current
    fingerprint = definition_fingerprint(live)
    entry = CategoryDefinitionCacheEntry(
        platform=str(platform or "").strip().lower(),
        credential_scope_hash=str(credential_scope_hash or "public"),
        site=str(site or "").strip(),
        category_id=str(category_id or "").strip(),
        format_version=DEFINITION_FORMAT_VERSION,
        definition=live,
        fingerprint=fingerprint,
        retrieved_at=retrieved_at,
        expires_at=retrieved_at + fresh_ttl,
        stale_until=retrieved_at + max_age,
        source="live",
    )
    try:
        write_definition_cache(cache_root, entry)
    except OSError:
        logger.warning("写入类目定义持久缓存失败", exc_info=True)
    return live.model_copy(
        update={
            "fingerprint": fingerprint,
            "cache": entry.cache_state(stale=False, source="live"),
        }
    )


__all__ = [
    "DEFINITION_CACHE_FRESH_TTL",
    "DEFINITION_CACHE_MAX_AGE",
    "DEFINITION_CACHE_SCHEMA",
    "DEFINITION_STALE_RETRY_COOLDOWN_SECONDS",
    "CategoryDefinitionCacheEntry",
    "clear_definition_cache",
    "load_definition_through_cache",
    "read_definition_cache",
    "write_definition_cache",
]
