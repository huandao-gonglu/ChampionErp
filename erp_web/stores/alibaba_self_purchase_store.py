"""采购回执在发出写请求前落库；事务不跨网络，重启后不重放未知写入。"""
import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
import time
from uuid import uuid4
from erp_web.schemas.orders import utc_iso


class AlibabaSelfPurchaseStore:
    def __init__(self, path):
        self._previews = {}
        self._preview_lock = RLock()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
                raise ValueError("采购记录库版本不受支持")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''CREATE TABLE IF NOT EXISTS alibaba_self_purchases (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, target_key TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            columns = {r[1] for r in conn.execute("PRAGMA table_info(alibaba_self_purchases)")}
            if "listing_id" in columns:
                conn.execute("ALTER TABLE alibaba_self_purchases RENAME COLUMN listing_id TO target_key")
                conn.execute("DROP INDEX IF EXISTS alibaba_self_purchase_listing")
            conn.execute("CREATE INDEX IF NOT EXISTS alibaba_self_purchase_target ON alibaba_self_purchases(target_key,account,state)")
            conn.execute("PRAGMA user_version=2")
            conn.commit()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("采购记录不存在或授权账号已切换")
        return {**dict(row), "payload": json.loads(row["payload"]), "result": json.loads(row["result"])}

    def get(self, ident, account):
        with self._preview_lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM alibaba_self_purchases WHERE id=? AND account=?", (ident, account)).fetchone()
            if row is not None:
                return self.decode(row)
            return deepcopy(self._preview(ident, account))

    def _preview(self, ident, account):
        self._prune_previews()
        row = self._previews.get(ident)
        if row is None or row["account"] != account:
            raise ValueError("预览已过期或授权已切换，请重新预览")
        return row

    def _prune_previews(self):
        now = time.time()
        self._previews = {key: row for key, row in self._previews.items()
                          if row["payload"]["view"]["expires_at"] > now}

    def close(self):
        with self._preview_lock:
            self._previews.clear()

    def records(self, target_key, account):
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alibaba_self_purchases WHERE target_key=? AND account=? AND state NOT IN ('preview','expired') ORDER BY created_at DESC LIMIT 30", (target_key, account)).fetchall()
        return [self.decode(row) for row in rows]

    def preview(self, target_key, account, payload):
        ident, now = uuid4().hex, utc_iso()
        with self._preview_lock, self.connect() as conn:
            self._check_pending(conn, target_key, account)
            self._prune_previews()
            # 预览只供本次确认；同一订单行的新预览替换旧预览，不写数据库。
            self._previews = {key: row for key, row in self._previews.items()
                              if (row["target_key"], row["account"]) != (target_key, account)}
            if len(self._previews) >= 256:
                del self._previews[next(iter(self._previews))]
            row = dict(id=ident, account=account, target_key=target_key, state="preview",
                       payload=deepcopy(payload), result={}, created_at=now, updated_at=now)
            self._previews[ident] = row
            return deepcopy(row)

    @staticmethod
    def _check_pending(conn, target_key, account, exclude=""):
        # 更换令牌可能仍是同一买家；旧授权的未知订单也不能靠换凭据绕过防重。
        row = conn.execute("SELECT id FROM alibaba_self_purchases WHERE target_key=? AND id!=? AND (state IN ('submitting','unknown') OR (state='created' AND COALESCE(json_extract(result,'$.purchase_record_id'),'')=''))", (target_key, exclude)).fetchone()
        if row:
            raise ValueError("该订单商品已有待处理采购请求，请先核验原订单")

    def claim(self, ident, account, channel):
        with self._preview_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = conn.execute("SELECT * FROM alibaba_self_purchases WHERE id=? AND account=?", (ident, account)).fetchone()
            if saved is not None:
                row = self.decode(saved)
                if row["state"] in {"preview", "expired"}:
                    raise ValueError("旧预览已失效，请重新预览")
                if row["result"].get("pay_channel", channel) != channel:
                    raise ValueError("该预览已提交，不能更改原请求的支付渠道")
                return row, False
            row = self._preview(ident, account)
            if channel not in row["payload"]["view"]["pay_channels"]:
                raise ValueError("该订单预览不支持所选支付渠道")
            self._check_pending(conn, row["target_key"], account, ident)
            now = utc_iso()
            # 用户提交时才落库；持久化成功后服务才允许发出创建订单请求。
            conn.execute("INSERT INTO alibaba_self_purchases VALUES (?,?,?,'submitting',?,?,?,?)",
                         (ident, account, row["target_key"], json.dumps(row["payload"], ensure_ascii=False),
                          json.dumps({"pay_channel": channel}), now, now))
            conn.commit()
            del self._previews[ident]
        return self.get(ident, account), True

    def finish(self, ident, account, state, result):
        with self.connect() as conn:
            conn.execute("UPDATE alibaba_self_purchases SET state=?,result=?,updated_at=? WHERE id=? AND account=?", (state, json.dumps(result, ensure_ascii=False), utc_iso(), ident, account))
            conn.commit()
        return self.get(ident, account)
