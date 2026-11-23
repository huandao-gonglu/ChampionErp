"""1688 万邦个人直购独立存储；与 ERP 商品、草稿、订单与采购库完全隔离。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

from erp_web.schemas.orders import utc_iso

_RESOLVE_TTL_SECONDS = 1800
_MAX_RESOLVED_CACHE = 128
_MAX_PREVIEW_CACHE = 128


class AlibabaDirectPurchaseStore:
    def __init__(self, path: str | Path) -> None:
        self._resolved: dict[str, dict] = {}
        self._previews: dict[str, dict] = {}
        self._lock = RLock()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("直购记录库版本不受支持")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alibaba_direct_purchases (
                    id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    result TEXT NOT NULL,
                    payment TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_direct_purchases_account_created "
                "ON alibaba_direct_purchases(account, created_at DESC)"
            )
            conn.execute("PRAGMA user_version=1")
            conn.commit()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def decode(row: sqlite3.Row | None) -> dict:
        if row is None:
            raise ValueError("直购记录不存在或授权账号已切换")
        return {
            **dict(row),
            "payload": json.loads(row["payload"]),
            "result": json.loads(row["result"]),
            "payment": json.loads(row["payment"] or "{}"),
        }

    def _prune_resolved(self) -> None:
        now = time.time()
        self._resolved = {
            key: value for key, value in self._resolved.items() if value["expires_at"] > now
        }
        while len(self._resolved) > _MAX_RESOLVED_CACHE:
            oldest = min(self._resolved, key=lambda key: self._resolved[key]["expires_at"])
            self._resolved.pop(oldest, None)

    def _prune_previews(self) -> None:
        now = time.time()
        self._previews = {
            key: row
            for key, row in self._previews.items()
            if row["payload"]["view"]["expires_at"] > now
        }
        while len(self._previews) > _MAX_PREVIEW_CACHE:
            oldest = min(
                self._previews,
                key=lambda key: self._previews[key]["payload"]["view"]["expires_at"],
            )
            self._previews.pop(oldest, None)

    def close(self) -> None:
        with self._lock:
            self._resolved.clear()
            self._previews.clear()

    def save_resolved(self, account: str, product: dict, sku_map: dict[str, dict]) -> str:
        token = uuid4().hex
        with self._lock:
            self._prune_resolved()
            self._resolved[token] = {
                "account": account,
                "expires_at": time.time() + _RESOLVE_TTL_SECONDS,
                "product": deepcopy(product),
                "sku_map": deepcopy(sku_map),
            }
        return token

    def get_resolved(self, token: str, account: str) -> dict:
        with self._lock:
            self._prune_resolved()
            entry = self._resolved.get(token)
            if entry is None or entry["account"] != account:
                raise ValueError("商品解析已过期或万邦授权账号已切换，请重新解析链接")
            return deepcopy(entry)

    def save_preview(self, account: str, payload: dict) -> dict:
        ident, now = uuid4().hex, utc_iso()
        row = {
            "id": ident,
            "account": account,
            "state": "preview",
            "payload": deepcopy(payload),
            "result": {},
            "payment": {},
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._prune_previews()
            self._previews[ident] = row
            return deepcopy(row)

    def get(self, ident: str, account: str) -> dict:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM alibaba_direct_purchases WHERE id=? AND account=?",
                (ident, account),
            ).fetchone()
            if row is not None:
                return self.decode(row)
            self._prune_previews()
            preview = self._previews.get(ident)
            if preview is None or preview["account"] != account:
                raise ValueError("预览已过期或授权账号已切换，请重新预览")
            return deepcopy(preview)

    def list_records(self, account: str, *, limit: int = 50) -> list[dict]:
        if not account:
            return []
        capped = max(1, min(200, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alibaba_direct_purchases WHERE account=? "
                "ORDER BY created_at DESC LIMIT ?",
                (account, capped),
            ).fetchall()
        return [self.decode(row) for row in rows]

    def claim(self, ident: str, account: str, channel: str) -> tuple[dict, bool]:
        """下单意图先落库，事务提交成功后才允许外发建单请求。"""
        with self._lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = conn.execute(
                "SELECT * FROM alibaba_direct_purchases WHERE id=? AND account=?",
                (ident, account),
            ).fetchone()
            if saved is not None:
                row = self.decode(saved)
                if row["result"].get("pay_channel", channel) != channel:
                    raise ValueError("该预览已提交，不能更改原请求的支付渠道")
                return row, False
            self._prune_previews()
            preview = self._previews.get(ident)
            if preview is None or preview["account"] != account:
                raise ValueError("预览已过期或授权账号已切换，请重新预览")
            if channel not in preview["payload"]["view"]["pay_channels"]:
                raise ValueError("该订单预览不支持所选支付渠道")
            now = utc_iso()
            conn.execute(
                "INSERT INTO alibaba_direct_purchases "
                "(id, account, state, payload, result, payment, created_at, updated_at) "
                "VALUES (?, ?, 'submitting', ?, ?, '{}', ?, ?)",
                (
                    ident,
                    account,
                    json.dumps(preview["payload"], ensure_ascii=False),
                    json.dumps({"pay_channel": channel}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            self._previews.pop(ident, None)
        return self.get(ident, account), True

    def finish(self, ident: str, account: str, state: str, result: dict) -> dict:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alibaba_direct_purchases SET state=?, result=?, updated_at=? "
                "WHERE id=? AND account=?",
                (state, json.dumps(result, ensure_ascii=False), utc_iso(), ident, account),
            )
            conn.commit()
        return self.get(ident, account)

    def claim_payment(
        self, ident: str, account: str, amount_fen: int, channel: str
    ) -> tuple[dict, bool]:
        """免密支付意图先落库，避免超时重试导致重复扣款。"""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.decode(
                conn.execute(
                    "SELECT * FROM alibaba_direct_purchases WHERE id=? AND account=?",
                    (ident, account),
                ).fetchone()
            )
            if row["payment"]:
                return row, False
            if (
                row["state"] != "created"
                or channel not in {"alipay", "shegou"}
                or row["result"].get("pay_channel") != channel
                or len(row["result"].get("order_numbers", [])) != 1
                or row["payload"]["view"]["total_fen"] != amount_fen
            ):
                raise ValueError("直购订单或确认金额已变化，不能发起免密支付")
            payment = {
                "state": "submitting",
                "channel": channel,
                "amount_fen": amount_fen,
                "request_id": uuid4().hex,
                "message": "免密支付正在提交；请刷新订单状态，勿重复支付",
            }
            conn.execute(
                "UPDATE alibaba_direct_purchases SET payment=?, updated_at=? "
                "WHERE id=? AND account=?",
                (json.dumps(payment, ensure_ascii=False), utc_iso(), ident, account),
            )
            conn.commit()
        return self.get(ident, account), True

    def finish_payment(
        self, ident: str, account: str, request_id: str, state: str, message: str
    ) -> dict:
        with self.connect() as conn:
            conn.execute(
                """UPDATE alibaba_direct_purchases
                SET payment=json_set(payment,'$.state',?,'$.message',?), updated_at=?
                WHERE id=? AND account=? AND json_extract(payment,'$.request_id')=?
                AND json_extract(payment,'$.state') IN ('submitting','unknown')""",
                (state, message, utc_iso(), ident, account, request_id),
            )
            conn.commit()
        return self.get(ident, account)

    def mark_quota_credited(self, ident: str, account: str) -> dict:
        with self.connect() as conn:
            conn.execute(
                """UPDATE alibaba_direct_purchases
                SET result=json_set(result,'$.quota_credited',json('true')), updated_at=?
                WHERE id=? AND account=?""",
                (utc_iso(), ident, account),
            )
            conn.commit()
        return self.get(ident, account)


__all__ = ["AlibabaDirectPurchaseStore"]
