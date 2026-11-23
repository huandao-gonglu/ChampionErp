"""1688 / 万邦只读缓存：持久化地址簿（24h）、已开通免密签约（6h）与商详短缓存（10m）。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time

ADDRESS_CACHE_TTL_SECONDS = 86400.0
AGREEMENT_CACHE_TTL_SECONDS = 21600.0
PRODUCT_CACHE_TTL_SECONDS = 600.0
_MAX_PRODUCT_CACHE_ENTRIES = 64
_MAX_AGREEMENT_CACHE_ENTRIES = 64


class AlibabaReadCache:
    """按个人工作空间与买家 account_scope 隔离的 1688 只读缓存，不保存任何凭据或密钥。"""

    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        self._agreements: dict[tuple[str, str], tuple[float, dict]] = {}
        self._products: dict[tuple[str, str, str], tuple[float, dict]] = {}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("1688 只读缓存库版本不受支持")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alibaba_address_cache (
                    account_scope TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            conn.execute("PRAGMA user_version=1")
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        with self._lock:
            self._agreements.clear()
            self._products.clear()

    # -- 1. 收货地址簿持久化缓存（24h） ----------------------------------------

    def get_addresses(
        self,
        scope: str,
        *,
        ttl: float = ADDRESS_CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> list[tuple[dict, dict]] | None:
        if not scope:
            return None
        current = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM alibaba_address_cache WHERE account_scope=?",
                (scope,),
            ).fetchone()
        if row is None or current - float(row["updated_at"]) > ttl:
            return None
        try:
            raw = json.loads(row["payload"])
        except (ValueError, TypeError):
            return None
        if not isinstance(raw, list):
            return None
        items: list[tuple[dict, dict]] = []
        for entry in raw:
            if (
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[0], dict)
                and isinstance(entry[1], dict)
            ):
                items.append((entry[0], entry[1]))
            else:
                return None
        return items

    def set_addresses(
        self,
        scope: str,
        items: list[tuple[dict, dict]],
        *,
        now: float | None = None,
    ) -> None:
        if not scope:
            return
        current = time.time() if now is None else now
        encoded = json.dumps([[candidate, fields] for candidate, fields in items], ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO alibaba_address_cache(account_scope, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(account_scope) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (scope, encoded, current),
            )
            conn.commit()

    # -- 2. 免密签约状态缓存（仅缓存 ready=True，6h） ---------------------------

    def get_payment_agreement(
        self,
        scope: str,
        channel: str,
        *,
        ttl: float = AGREEMENT_CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> dict | None:
        if not scope or not channel:
            return None
        current = time.time() if now is None else now
        key = (scope, channel)
        with self._lock:
            entry = self._agreements.get(key)
            if entry is None:
                return None
            saved_at, value = entry
            if current - saved_at > ttl:
                self._agreements.pop(key, None)
                return None
            return deepcopy(value)

    def set_payment_agreement(
        self,
        scope: str,
        channel: str,
        agreement: dict,
        *,
        now: float | None = None,
    ) -> None:
        if not scope or not channel or not isinstance(agreement, dict):
            return
        key = (scope, channel)
        with self._lock:
            if not agreement.get("ready"):
                # 未开通时不缓存，方便用户在 1688 签约后立即重试生效。
                self._agreements.pop(key, None)
                return
            current = time.time() if now is None else now
            if len(self._agreements) >= _MAX_AGREEMENT_CACHE_ENTRIES and key not in self._agreements:
                oldest = min(self._agreements, key=lambda item: self._agreements[item][0])
                self._agreements.pop(oldest, None)
            self._agreements[key] = (current, deepcopy(agreement))

    def invalidate_payment_agreement(self, scope: str, channel: str = "") -> None:
        if not scope:
            return
        with self._lock:
            if channel:
                self._agreements.pop((scope, channel), None)
            else:
                for key in [k for k in self._agreements if k[0] == scope]:
                    self._agreements.pop(key, None)

    # -- 3. 1688 商详只读短缓存（10m，跨采集 / 绑源 / 直购复用） ----------------

    def get_product(
        self,
        provider_type: str,
        scope: str,
        offer_id: str,
        *,
        ttl: float = PRODUCT_CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> dict | None:
        if not provider_type or not scope or not offer_id:
            return None
        current = time.time() if now is None else now
        key = (provider_type, scope, str(offer_id))
        with self._lock:
            entry = self._products.get(key)
            if entry is None:
                return None
            saved_at, payload = entry
            if current - saved_at > ttl:
                self._products.pop(key, None)
                return None
            return deepcopy(payload)

    def set_product(
        self,
        provider_type: str,
        scope: str,
        offer_id: str,
        payload: dict,
        *,
        now: float | None = None,
    ) -> None:
        if not provider_type or not scope or not offer_id or not isinstance(payload, dict):
            return
        current = time.time() if now is None else now
        key = (provider_type, scope, str(offer_id))
        with self._lock:
            if len(self._products) >= _MAX_PRODUCT_CACHE_ENTRIES and key not in self._products:
                oldest = min(self._products, key=lambda item: self._products[item][0])
                self._products.pop(oldest, None)
            self._products[key] = (current, deepcopy(payload))


__all__ = [
    "ADDRESS_CACHE_TTL_SECONDS",
    "AGREEMENT_CACHE_TTL_SECONDS",
    "AlibabaReadCache",
    "PRODUCT_CACHE_TTL_SECONDS",
]
