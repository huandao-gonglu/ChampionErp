"""请求尝试、共享放行租约与阻断记录。短 SQLite 事务不跨网络等待。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import logging

import json
import time
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from erp_web.schemas.external_requests import RequestContext, RequestFailure, is_definite_request_rejection


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
        self._migrate_transient_blocks()

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
            probe = None
            for block in sorted(blocks, key=lambda row: json.loads(row["failure"]).get("code") == "EXTERNAL_TRANSIENT_FAILURE"):
                if block["scope_key"] != self.scope_key(ctx, block["scope"]):
                    continue
                failure = RequestFailure(**json.loads(block["failure"]))
                if failure.code == "EXTERNAL_TRANSIENT_FAILURE":
                    if failure.probe_id and failure.probe_until <= now:
                        # 进程退出或超时的探测不能永久占用名额，也不能被迟到回执恢复。
                        failure = replace(failure, probe_id="", probe_until=0,
                                          cooldown_seconds=min(1800, failure.cooldown_seconds * 2),
                                          resume_at=now + min(1800, failure.cooldown_seconds * 2))
                        self._write_circuit(conn, block, failure)
                    if ctx.semantics == "read" and not failure.probe_id and (failure.resume_at or 0) <= now:
                        probe = (block, failure)
                        continue
                    conn.execute("UPDATE external_blocks SET blocked_count=blocked_count+1 WHERE platform=? AND account_id=? AND scope=? AND scope_key=?",
                                 (ctx.platform,ctx.account_id,block["scope"],block["scope_key"]))
                    conn.commit()
                    return replace(failure, resume_at=max(failure.resume_at or 0, failure.probe_until)), 0
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
            if probe:
                block, failure = probe
                self._write_circuit(conn, block, replace(failure, probe_id=request_id, probe_until=deadline))
            conn.execute("UPDATE external_attempts SET decision='allowed',sent=1,released=?,lease_until=? WHERE id=?", (now,deadline,request_id))
            conn.commit()
        return None, 0

    def finish(self, request_id, *, decision=None, result=None, not_sent=False):
        # result 仅接收管理器构造的白名单摘要，禁止传平台响应对象。
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE external_attempts SET completed=?,decision=COALESCE(?,decision),result=json_patch(result,?),lease_until=0,"
                         "sent=CASE WHEN ? THEN 0 ELSE sent END,released=CASE WHEN ? THEN NULL ELSE released END WHERE id=?",
                         (time.time(),decision,json.dumps(result or {},ensure_ascii=False),not_sent,not_sent,request_id))
            self._settle_circuit(conn, request_id, result or {})
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

    @staticmethod
    def _write_circuit(conn, block, failure):
        conn.execute("UPDATE external_blocks SET failure=? WHERE platform=? AND account_id=? AND scope=? AND scope_key=?",
                     (json.dumps(asdict(failure), ensure_ascii=False), block["platform"], block["account_id"], block["scope"], block["scope_key"]))

    @staticmethod
    def _transient(result):
        return not result.get("cancelled") and (
            result.get("outcome") == "network_error"
            or (result.get("outcome") == "failed" and (result.get("failure") or {}).get("retryable") is True
                and not (result.get("failure") or {}).get("scope")))

    def _settle_circuit(self, conn, request_id, result):
        block = conn.execute("SELECT * FROM external_blocks WHERE json_extract(failure,'$.code')='EXTERNAL_TRANSIENT_FAILURE' AND json_extract(failure,'$.probe_id')=?", (request_id,)).fetchone()
        if not block:
            return
        failure = RequestFailure(**json.loads(block["failure"]))
        if result.get("outcome") in {"success", "stream_closed"} and failure.probe_until > time.time():
            conn.execute("DELETE FROM external_blocks WHERE platform=? AND account_id=? AND scope=? AND scope_key=?",
                         (block["platform"], block["account_id"], block["scope"], block["scope_key"]))
            conn.execute("INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)",
                         (uuid4().hex, block["platform"], block["account_id"], block["scope"], block["scope_key"], time.time(), "冷却后的只读请求成功，自动恢复"))
        else:
            delay = min(1800, max(60, failure.cooldown_seconds * 2))
            self._write_circuit(conn, block, replace(failure, probe_id="", probe_until=0,
                                                     cooldown_seconds=delay, resume_at=time.time() + delay))

    def request_probe(self, platform, account_id, interfaces, *, expected_failure=None):
        """人工请求仅提前一次临时故障探测，不删除授权、限流或写入未知阻断。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for block in conn.execute("SELECT * FROM external_blocks WHERE platform=? AND account_id=? AND scope='interface'", (platform, account_id)).fetchall():
                failure = RequestFailure(**json.loads(block["failure"]))
                if block["scope_key"] not in interfaces:
                    continue
                if expected_failure is not None and json.loads(block["failure"]) != expected_failure:
                    raise ValueError("中断状态已变化，请刷新后重试")
                if failure.code != "EXTERNAL_TRANSIENT_FAILURE" or (failure.probe_id and failure.probe_until > time.time()):
                    continue
                self._write_circuit(conn, block, replace(failure, resume_at=time.time(), probe_id="", probe_until=0))
                conn.execute("INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)",
                             (uuid4().hex, platform, account_id, "interface", block["scope_key"], time.time(), "用户请求提前进行一次只读恢复检查"))
            conn.commit()

    def record_failure_streak(self, ctx):
        """只读临时故障进入冷却；确定性拒绝和写入未知仍要求明确恢复。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM external_blocks WHERE platform=? AND account_id=? AND scope='interface' AND scope_key=?", (ctx.platform, ctx.account_id, ctx.interface)).fetchone():
                return
            policies = conn.execute("SELECT consecutive_failure_limit FROM external_policies WHERE platform=? AND interface IN ('*',?) AND consecutive_failure_limit IS NOT NULL",(ctx.platform,ctx.interface)).fetchall()
            limit = min((r[0] for r in policies),default=5)
            rows = conn.execute("SELECT result,semantics FROM external_attempts WHERE platform=? AND account_id=? AND interface=? AND sent=1 AND completed IS NOT NULL AND created>? ORDER BY created DESC LIMIT ?",(ctx.platform,ctx.account_id,ctx.interface,time.time()-600,limit)).fetchall()
            outcomes = [json.loads(row["result"]) for row in rows]
            if len(rows) != limit or not all(row.get("outcome") in {"failed","network_error","outcome_unknown"} and not row.get("cancelled") for row in outcomes):
                return
            transient = all(row["semantics"] == "read" and self._transient(value) for row, value in zip(rows, outcomes))
            failure = RequestFailure(
                "EXTERNAL_TRANSIENT_FAILURE" if transient else "EXTERNAL_REPEATED_FAILURE",
                "接口连续失败，暂时暂停；冷却后自动进行一次只读恢复检查" if transient else "该接口连续失败，已停止新请求；请查明原因后明确恢复",
                "interface", resume_at=time.time()+60 if transient else None,
                retryable=transient, cooldown_seconds=60 if transient else 0)
            conn.execute("INSERT INTO external_blocks(platform,account_id,scope,scope_key,failure,created) VALUES(?,?,?,?,?,?)",
                         (ctx.platform,ctx.account_id,"interface",ctx.interface,json.dumps(asdict(failure),ensure_ascii=False),time.time()))
            conn.commit()
        logging.getLogger(__name__).warning("外部接口连续失败，已暂停：平台=%s，账号=%s，接口=%s",ctx.platform,ctx.account_id,ctx.interface)

    def _migrate_transient_blocks(self):
        """旧永久阻断仅在原始审计能证明全部为只读临时故障时转为冷却。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            blocks = conn.execute("SELECT * FROM external_blocks WHERE scope='interface' AND json_extract(failure,'$.code')='EXTERNAL_REPEATED_FAILURE'").fetchall()
            for block in blocks:
                policies = conn.execute("SELECT consecutive_failure_limit FROM external_policies WHERE platform=? AND interface IN ('*',?) AND consecutive_failure_limit IS NOT NULL", (block["platform"], block["scope_key"])).fetchall()
                limit = min((row[0] for row in policies), default=5)
                rows = conn.execute("SELECT semantics,result FROM external_attempts WHERE platform=? AND account_id=? AND interface=? AND sent=1 AND completed IS NOT NULL AND created BETWEEN ? AND ? ORDER BY created DESC LIMIT ?",
                                    (block["platform"], block["account_id"], block["scope_key"], block["created"]-600, block["created"], limit)).fetchall()
                if len(rows) != limit or not all(row["semantics"] == "read" and self._transient(json.loads(row["result"])) for row in rows):
                    continue
                self._write_circuit(conn, block, RequestFailure("EXTERNAL_TRANSIENT_FAILURE", "接口连续失败，暂时暂停；冷却后自动进行一次只读恢复检查", "interface", resume_at=block["created"]+60, retryable=True, cooldown_seconds=60))
                conn.execute("INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)", (uuid4().hex, block["platform"], block["account_id"], "interface", block["scope_key"], time.time(), "依据原始只读临时失败审计，将旧阻断转为冷却检查；尚未确认恢复"))
            conn.commit()

    def recover_confirmed(self, blocks, *, reason):
        """人工确认已处理原因后恢复指定快照；拒绝解除其后新产生的阻断。"""
        if not reason.strip():
            raise ValueError("请填写已处理的故障原因")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for block in blocks:
                failure = block["failure"]
                allowed = block["scope"] in {"account", "credential", "interface"} or is_definite_request_rejection(block["scope"], failure["code"])
                if failure.get("resume_at") is not None or failure["code"] == "EXTERNAL_WRITE_OUTCOME_UNKNOWN" or not allowed:
                    raise ValueError("平台限流或等待中的请求不能通过此操作解除")
                keys = (block["platform"], block["account_id"], block["scope"], block["scope_key"])
                current = conn.execute("SELECT failure,created FROM external_blocks WHERE platform=? AND account_id=? AND scope=? AND scope_key=?", keys).fetchone()
                if not current or json.loads(current[0]) != failure or current[1] != block["created"]:
                    raise ValueError("同步状态已变化，请刷新后重试")
                conn.execute("DELETE FROM external_blocks WHERE platform=? AND account_id=? AND scope=? AND scope_key=?", keys)
                conn.execute("INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)", (uuid4().hex, *keys, time.time(), reason.strip()[:500]))
            conn.commit()

    def interruptions(self, *, offset=0, limit=50):
        where = "(decision IN ('rejected','cancelled') OR json_extract(result,'$.outcome') IN ('failed','network_error','outcome_unknown','interrupted'))"
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM external_attempts WHERE " + where).fetchone()[0]
            rows = conn.execute("SELECT id,platform,interface,source,trigger,created,result,decision,semantics FROM external_attempts WHERE " + where + " ORDER BY created DESC LIMIT ? OFFSET ?", (limit,max(0,offset))).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["result"])
            failure = value.get("failure") or value
            outcome = value.get("outcome", "")
            result.append({"id": row["id"], "platform": row["platform"], "interface": row["interface"],
                           "trigger": row["trigger"], "created_at": row["created"], "semantics": row["semantics"],
                           "code": failure.get("code") or outcome,
                           "message": failure.get("message") or {"network_error": "网络中断，未取得完整响应", "outcome_unknown": "写入结果未知，请先核对业务回执", "interrupted": "请求中断，未确认结果", "failed": "平台请求失败", "cancelled": "请求已取消"}.get(outcome, "请求已取消" if row["decision"] == "cancelled" else "请求被暂停"),
                           "local_rejection": row["decision"] == "rejected"})
        return result, total

    def rejection_notices(self, operation_ids):
        if not operation_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT id,operation_id,platform,result,created FROM external_attempts WHERE decision='rejected' AND created>? AND operation_id IN (" + ",".join("?" for _ in operation_ids) + ") ORDER BY created DESC LIMIT 50", (time.time()-86400, *operation_ids)).fetchall()
        return [{"id": row["id"], "operation_id": row["operation_id"], "platform": row["platform"], "created_at": row["created"],
                 "code": json.loads(row["result"]).get("code", ""),
                 "message": json.loads(row["result"]).get("message", "平台请求已暂停")}
                for row in rows]

    def matching_blocks(self, contexts):
        contexts = list(contexts)
        now = time.time()
        return [block for block in self.blocks() if any(
            block["platform"] == ctx.platform and block["account_id"] == ctx.account_id
            and block["scope_key"] == self.scope_key(ctx, block["scope"])
            for ctx in contexts)
            and (block["failure"]["code"] == "EXTERNAL_TRANSIENT_FAILURE"
                 or block["failure"].get("resume_at") is None or block["failure"]["resume_at"] > now)]

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
