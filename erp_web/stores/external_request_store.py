"""请求尝试、共享放行租约与阻断记录。短 SQLite 事务不跨网络等待。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import logging

import json
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from erp_web.schemas.external_requests import RequestContext, RequestFailure


EXTERNAL_REQUEST_SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS external_attempts (
    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, parent_id TEXT NOT NULL,
    attempt INTEGER NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
    credential_id TEXT NOT NULL, interface TEXT NOT NULL, source TEXT NOT NULL,
    trigger TEXT NOT NULL, quota_key TEXT NOT NULL, method TEXT NOT NULL, semantics TEXT NOT NULL,
    created REAL NOT NULL, released REAL, completed REAL, sent INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', lease_until REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS external_attempt_filter ON external_attempts(platform,account_id,created);
CREATE INDEX IF NOT EXISTS external_attempt_operation ON external_attempts(operation_id);
CREATE TABLE IF NOT EXISTS external_blocks (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, scope TEXT NOT NULL, scope_key TEXT NOT NULL,
    failure TEXT NOT NULL, created REAL NOT NULL, blocked_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(platform,account_id,scope,scope_key));
CREATE TABLE IF NOT EXISTS external_recoveries (
    id TEXT PRIMARY KEY, platform TEXT, account_id TEXT, scope TEXT, scope_key TEXT,
    created REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS external_policies (
    platform TEXT NOT NULL, interface TEXT NOT NULL, concurrency INTEGER,
    requests_per_minute INTEGER, consecutive_failure_limit INTEGER, PRIMARY KEY(platform,interface));
'''


class ExternalRequestStore:
    def __init__(self, path, *, retention_days=30, max_records=100_000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = retention_days * 86400
        self.max_records = max_records
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("外部请求审计库版本不受支持，禁止覆盖")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA journal_size_limit=8388608")
            conn.executescript(EXTERNAL_REQUEST_SCHEMA_SQL)

        self.path.chmod(0o600)
        with self._connect() as conn:
            conn.execute("PRAGMA user_version=1")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def begin(self, ctx: RequestContext, method: str, *, parent_id="", attempt=1):
        request_id = uuid4().hex
        with self._connect() as conn:
            conn.execute('''INSERT INTO external_attempts
                (id,operation_id,parent_id,attempt,platform,account_id,credential_id,interface,source,trigger,quota_key,method,semantics,created,decision)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting')''',
                (request_id,ctx.operation_id,parent_id,attempt,ctx.platform,ctx.account_id,ctx.credential_id,
                 ctx.interface,ctx.source,"retry" if attempt > 1 else ctx.trigger,ctx.quota_key,method,ctx.semantics,time.time()))
            # 正在发送的记录不清理；大小由固定字段与 result 白名单限制。
            # 超过保留期仍无结果的尝试只能标为中断，不能冒充成功或永久逃避清理。
            conn.execute("""UPDATE external_attempts SET completed=?,
                result=json_object('outcome',CASE WHEN sent=1 AND semantics='write' THEN 'outcome_unknown' ELSE 'interrupted' END),
                lease_until=0 WHERE completed IS NULL AND created<?""", (time.time(),time.time()-self.retention_seconds))
            conn.execute("DELETE FROM external_attempts WHERE completed IS NOT NULL AND created<?", (time.time()-self.retention_seconds,))
            conn.execute('''DELETE FROM external_attempts WHERE id IN
                (SELECT id FROM external_attempts WHERE completed IS NOT NULL ORDER BY created DESC LIMIT -1 OFFSET ?)''', (self.max_records,))
            conn.execute("DELETE FROM external_recoveries WHERE created<?", (time.time()-self.retention_seconds,))
            conn.execute("DELETE FROM external_recoveries WHERE id IN (SELECT id FROM external_recoveries ORDER BY created DESC LIMIT -1 OFFSET ?)", (self.max_records,))
            conn.execute("DELETE FROM external_blocks WHERE scope IN ('request','operation') AND created<?", (time.time()-86400,))
            conn.commit()
        return request_id

    @staticmethod
    def scope_key(ctx, scope):
        return {"account": "", "quota":ctx.quota_key,"credential": ctx.credential_id, "interface": ctx.interface,
                "operation": ctx.operation_id,"request": ctx.operation_id+":"+ctx.interface+":"+ctx.fingerprint}.get(scope, "")

    def acquire(self, request_id, ctx, deadline):
        """检查与租约分配在同一写事务中完成；返回失败或下次检查的等待秒数。"""
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            blocks = conn.execute("SELECT * FROM external_blocks WHERE platform=? AND account_id=?", (ctx.platform,ctx.account_id)).fetchall()
            for block in blocks:
                if block["scope_key"] != self.scope_key(ctx, block["scope"]):
                    continue
                failure = RequestFailure(**json.loads(block["failure"]))
                if failure.resume_at is not None and failure.resume_at <= now:
                    continue
                if failure.resume_at is not None and failure.resume_at < deadline:
                    conn.execute("UPDATE external_attempts SET result=? WHERE id=?", (json.dumps({"wait_rule":failure.code,"resume_at":failure.resume_at}),request_id))
                    conn.commit()
                    return None, min(.1, failure.resume_at-now)
                conn.execute("UPDATE external_blocks SET blocked_count=blocked_count+1 WHERE platform=? AND account_id=? AND scope=? AND scope_key=?",
                             (ctx.platform,ctx.account_id,block["scope"],block["scope_key"]))
                conn.commit()
                return failure, 0
            # Yandex 官方全局并发上限按 campaign/business 实体分别计算。
            if ctx.platform == "yandex":
                count = conn.execute("SELECT COUNT(*) FROM external_attempts WHERE platform=? AND account_id=? AND quota_key=? AND decision='allowed' AND completed IS NULL AND lease_until>?",(ctx.platform,ctx.account_id,ctx.quota_key,now)).fetchone()[0]
                if count >= 4:
                    conn.execute("UPDATE external_attempts SET result=? WHERE id=?",(json.dumps({"wait_rule":"yandex_parallel_4"}),request_id))
                    conn.commit()
                    return None,.05
            policies = conn.execute("SELECT * FROM external_policies WHERE platform=? AND interface IN ('*',?)", (ctx.platform,ctx.interface)).fetchall()
            for policy in policies:
                extra = "" if policy["interface"] == "*" else " AND interface=?"
                args = [ctx.platform,ctx.account_id] + ([] if not extra else [ctx.interface])
                if policy["concurrency"]:
                    count = conn.execute("SELECT COUNT(*) FROM external_attempts WHERE platform=? AND account_id=?"+extra+" AND decision='allowed' AND completed IS NULL AND lease_until>?", (*args,now)).fetchone()[0]
                    if count >= policy["concurrency"]:
                        conn.execute("UPDATE external_attempts SET result=? WHERE id=?", (json.dumps({"wait_rule":"concurrency"}),request_id))
                        conn.commit()
                        return None, .05
                if policy["requests_per_minute"]:
                    count = conn.execute("SELECT COUNT(*) FROM external_attempts WHERE platform=? AND account_id=?"+extra+" AND sent=1 AND released>?", (*args,now-60)).fetchone()[0]
                    if count >= policy["requests_per_minute"]:
                        conn.execute("UPDATE external_attempts SET result=? WHERE id=?", (json.dumps({"wait_rule":"requests_per_minute"}),request_id))
                        conn.commit()
                        return None, .1
            conn.execute("UPDATE external_attempts SET decision='allowed',sent=1,released=?,lease_until=? WHERE id=?", (now,deadline,request_id))
            conn.commit()
        return None, 0

    def finish(self, request_id, *, decision=None, result=None, not_sent=False):
        # result 仅接收管理器构造的白名单摘要，禁止传平台响应对象。
        with self._connect() as conn:
            conn.execute("UPDATE external_attempts SET completed=?,decision=COALESCE(?,decision),result=json_patch(result,?),lease_until=0,"
                         "sent=CASE WHEN ? THEN 0 ELSE sent END,released=CASE WHEN ? THEN NULL ELSE released END WHERE id=?",
                         (time.time(),decision,json.dumps(result or {},ensure_ascii=False),not_sent,not_sent,request_id))
            conn.commit()

    def block(self, ctx, failure):
        if not failure.scope:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existed = conn.execute("SELECT 1 FROM external_blocks WHERE platform=? AND account_id=? AND scope=? AND scope_key=?", (ctx.platform,ctx.account_id,failure.scope,self.scope_key(ctx,failure.scope))).fetchone()
            conn.execute('''INSERT INTO external_blocks(platform,account_id,scope,scope_key,failure,created)
                VALUES(?,?,?,?,?,?) ON CONFLICT(platform,account_id,scope,scope_key) DO UPDATE SET failure=excluded.failure''',
                (ctx.platform,ctx.account_id,failure.scope,self.scope_key(ctx,failure.scope),json.dumps(asdict(failure),ensure_ascii=False),time.time()))
            conn.commit()
        return not bool(existed)

    def recover(self, platform, account_id, scope, scope_key, *, reason):
        if not reason.strip():
            raise ValueError("恢复必须填写已满足平台恢复条件的说明")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM external_blocks WHERE platform=? AND account_id=? AND scope=? AND scope_key=?", (platform,account_id,scope,scope_key))
            conn.execute("INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)", (uuid4().hex,platform,account_id,scope,scope_key,time.time(),reason[:500]))
            conn.commit()

    def configure(self, platform, interface="*", *, concurrency=None, requests_per_minute=None, consecutive_failure_limit=None):
        for value in (concurrency,requests_per_minute,consecutive_failure_limit):
            if value is not None and (isinstance(value,bool) or not isinstance(value,int) or value < 1):
                raise ValueError("平台控制上限必须是正整数")
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO external_policies VALUES(?,?,?,?,?)", (platform,interface,concurrency,requests_per_minute,consecutive_failure_limit))
            conn.commit()

    def record_failure_streak(self, ctx):
        """相同接口连续失败到达阈值时停止试错，成功响应会自然打断序列。"""
        with self._connect() as conn:
            policies = conn.execute("SELECT consecutive_failure_limit FROM external_policies WHERE platform=? AND interface IN ('*',?) AND consecutive_failure_limit IS NOT NULL",(ctx.platform,ctx.interface)).fetchall()
            limit = min((r[0] for r in policies),default=5)
            rows = conn.execute("SELECT result FROM external_attempts WHERE platform=? AND account_id=? AND interface=? AND sent=1 AND completed IS NOT NULL AND created>? ORDER BY created DESC LIMIT ?",(ctx.platform,ctx.account_id,ctx.interface,time.time()-600,limit)).fetchall()
        outcomes = [json.loads(row[0]) for row in rows]
        if len(rows) == limit and all(row.get("outcome") in {"failed","network_error","outcome_unknown"} and not row.get("cancelled") for row in outcomes):
            failure = RequestFailure("EXTERNAL_REPEATED_FAILURE", "该接口连续失败，已停止新请求；请查明原因后明确恢复", "interface")
            if self.block(ctx,failure):
                logging.getLogger(__name__).warning("外部接口连续失败，已阻断：平台=%s，账号=%s，接口=%s",ctx.platform,ctx.account_id,ctx.interface)

    def query(self, *, limit=100, since=0, until=None, **filters):
        allowed = {key:key for key in ("platform","account_id","interface","operation_id","decision")}
        allowed.update(outcome="json_extract(result,'$.outcome')",http_status="json_extract(result,'$.http_status')")
        if filters.keys()-allowed.keys():
            raise ValueError("不支持的审计查询条件")
        where = "created>=? AND created<=?" + "".join(f" AND {allowed[k]}=?" for k in filters)
        args = [since, until or time.time(), *filters.values()]
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM external_attempts WHERE "+where+" ORDER BY created DESC LIMIT ?", (*args,max(1,min(int(limit),1000)))).fetchall()
            stats = conn.execute("SELECT COUNT(*) AS attempts,COUNT(DISTINCT operation_id) AS operations,COALESCE(SUM(sent),0) AS network_attempts,COALESCE(SUM(decision='rejected'),0) AS local_rejections FROM external_attempts WHERE "+where,args).fetchone()
        return {"items": [{**dict(r),"result":json.loads(r["result"]), "queue_seconds": (r["released"] or r["completed"] or time.time())-r["created"], "network_seconds": (r["completed"]-r["released"]) if r["completed"] and r["released"] else None} for r in rows],"stats":dict(stats)}

    def blocks(self):
        with self._connect() as conn:
            return [{**dict(r),"failure":json.loads(r["failure"])} for r in conn.execute("SELECT * FROM external_blocks")]

    def recoveries(self, *, limit=100):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM external_recoveries ORDER BY created DESC LIMIT ?", (max(1,min(int(limit),1000)),))]
