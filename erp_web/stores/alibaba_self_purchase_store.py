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
            if conn.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4):
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
            if "payment" not in columns:
                conn.execute("ALTER TABLE alibaba_self_purchases ADD COLUMN payment TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS alibaba_self_purchase_target ON alibaba_self_purchases(target_key,account,state)")
            conn.execute('CREATE TABLE IF NOT EXISTS alibaba_purchase_accounts (alias TEXT PRIMARY KEY, account TEXT NOT NULL, buyer_id TEXT NOT NULL)')
            conn.execute("PRAGMA user_version=4")
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
        return {**dict(row), "payload": json.loads(row["payload"]), "result": json.loads(row["result"]),
                "payment": json.loads(row["payment"])}

    def get(self, ident, account):
        with self._preview_lock, self.connect() as conn:
            account = self._account(conn, account)
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
            account = self._account(conn, account)
            rows = conn.execute("SELECT * FROM alibaba_self_purchases WHERE target_key=? AND account=? AND state NOT IN ('preview','expired') ORDER BY created_at DESC LIMIT 30", (target_key, account)).fetchall()
        return [self.decode(row) for row in rows]

    def batch_records(self, order_id, account):
        with self.connect() as conn:
            account = self._account(conn, account)
            rows = conn.execute("""SELECT * FROM alibaba_self_purchases
                WHERE account=? AND json_extract(payload,'$.binding.order_id')=?
                AND json_type(payload,'$.items')='array' AND state NOT IN ('preview','expired')
                ORDER BY created_at DESC""", (account, order_id)).fetchall()
        return [self.decode(row) for row in rows]

    def preview(self, target_key, account, payload):
        ident, now = uuid4().hex, utc_iso()
        with self._preview_lock, self.connect() as conn:
            account = self._account(conn, account)
            self._check_pending(conn, target_key, account, targets=payload.get('targets'))
            self._prune_previews()
            # 新预览使所有交叠订单行的旧预览失效，防止单条与批量入口交叉提交。
            targets = set(payload.get('targets') or [target_key])
            self._previews = {key: row for key, row in self._previews.items()
                              if row['account'] != account or not targets.intersection(row['payload'].get('targets') or [row['target_key']])}
            if len(self._previews) >= 256:
                del self._previews[next(iter(self._previews))]
            row = dict(id=ident, account=account, target_key=target_key, state="preview",
                       payload=deepcopy(payload), result={}, created_at=now, updated_at=now)
            self._previews[ident] = row
            return deepcopy(row)

    @staticmethod
    def _check_pending(conn, target_key, account, exclude="", targets=None):
        # 更换令牌可能仍是同一买家；旧授权的未知订单也不能靠换凭据绕过防重。
        row = conn.execute("""SELECT id FROM alibaba_self_purchases WHERE id!=?
            AND (target_key IN (SELECT value FROM json_each(?)) OR EXISTS (
                SELECT 1 FROM json_each(payload,'$.targets') WHERE value IN (SELECT value FROM json_each(?))))
            AND (state IN ('submitting','unknown') OR (state='created' AND COALESCE(json_extract(result,'$.purchase_record_id'),'')=''))""",
            (exclude, json.dumps(targets or [target_key]), json.dumps(targets or [target_key]))).fetchone()
        if row:
            raise ValueError("该订单商品已有待处理采购请求，请先核验原订单")

    def claim(self, ident, account, channel):
        with self._preview_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = self._account(conn, account)
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
            self._check_pending(conn, row["target_key"], account, ident, row['payload'].get('targets'))
            now = utc_iso()
            # 用户提交时才落库；持久化成功后服务才允许发出创建订单请求。
            conn.execute("INSERT INTO alibaba_self_purchases (id,account,target_key,state,payload,result,created_at,updated_at) VALUES (?,?,?,'submitting',?,?,?,?)",
                         (ident, account, row["target_key"], json.dumps(row["payload"], ensure_ascii=False),
                          json.dumps({"pay_channel": channel}), now, now))
            conn.commit()
            del self._previews[ident]
        return self.get(ident, account), True

    def finish(self, ident, account, state, result):
        with self.connect() as conn:
            account = self._account(conn, account)
            conn.execute("UPDATE alibaba_self_purchases SET state=?,result=?,updated_at=? WHERE id=? AND account=?", (state, json.dumps(result, ensure_ascii=False), utc_iso(), ident, account))
            conn.commit()
        return self.get(ident, account)

    def claim_payment(self, ident, account, amount_fen, channel):
        """支付意图先落库；创建回执与支付回执独立更新，查询不得覆盖支付占位。"""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = self._account(conn, account)
            row = self.decode(conn.execute("SELECT * FROM alibaba_self_purchases WHERE id=? AND account=?", (ident, account)).fetchone())
            if row['payment']:
                return row, False
            if (row['state'] != 'created' or not row['result'].get('purchase_record_id')
                    or channel not in {'alipay', 'shegou'} or row['result'].get('pay_channel') != channel
                    or len(row['result'].get('order_numbers', [])) != 1
                    or row['payload']['view']['total_fen'] != amount_fen):
                raise ValueError('采购订单或确认金额已变化，不能支付')
            payment = dict(state='submitting', channel=channel, amount_fen=amount_fen,
                           request_id=uuid4().hex, message='支付正在提交；请查询原订单，勿重复支付')
            conn.execute("UPDATE alibaba_self_purchases SET payment=? WHERE id=?", (json.dumps(payment, ensure_ascii=False), ident))
            conn.commit()
        return self.get(ident, account), True

    def finish_payment(self, ident, account, request_id, state, message):
        with self.connect() as conn:
            account = self._account(conn, account)
            # 只更新仍待确认的支付；晚到的异常不能覆盖查询已确认的结果。
            conn.execute("""UPDATE alibaba_self_purchases
                SET payment=json_set(payment,'$.state',?,'$.message',?)
                WHERE id=? AND account=? AND json_extract(payment,'$.request_id')=?
                AND json_extract(payment,'$.state') IN ('submitting','unknown')""",
                (state, message, ident, account, request_id))
            conn.commit()
        return self.get(ident, account)

    @staticmethod
    def _account(conn, account):
        row = conn.execute('SELECT account FROM alibaba_purchase_accounts WHERE alias=?', (account,)).fetchone()
        return row['account'] if row else account

    def merge_account_scopes(self, account, aliases, buyer_id):
        """只合并上层已核验同一买家的归属；别名让网络中旧请求的回执仍可保存。"""
        if not buyer_id or not account:
            raise ValueError('采购归属迁移缺少已核验买家')
        aliases = sorted({account, *(value for value in aliases if value)})
        with self._preview_lock, self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('SELECT alias,account,buyer_id FROM alibaba_purchase_accounts WHERE alias IN (SELECT value FROM json_each(?))',
                                (json.dumps(aliases),)).fetchall()
            if any(row['buyer_id'] != buyer_id for row in rows):
                raise ValueError('采购归属不能合并到其他 1688 买家')
            previous = sorted({*aliases, *(row['account'] for row in rows)})
            if any(row['account'] not in aliases for row in rows):
                raise ValueError('采购归属存在未核验关联，请重新连接买家账号')
            conn.execute('UPDATE alibaba_self_purchases SET account=? WHERE account IN (SELECT value FROM json_each(?)) AND account!=?',
                         (account, json.dumps(previous), account))
            conn.executemany('INSERT INTO alibaba_purchase_accounts(alias,account,buyer_id) VALUES (?,?,?) '
                             'ON CONFLICT(alias) DO UPDATE SET account=excluded.account',
                             [(alias, account, buyer_id) for alias in aliases])
            conn.commit()
            for row in self._previews.values():
                if row['account'] in previous:
                    row['account'] = account
