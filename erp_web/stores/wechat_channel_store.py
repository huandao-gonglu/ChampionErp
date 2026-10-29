"""微信渠道的持久收件与投递记录；原生历史仍由 PydanticMessageStore 持有。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from threading import RLock


def _initial_state():
    return {"generation": 0, "credentials": None, "conversation_id": "", "cursor": "",
            "context_token": "", "history_position": 0, "accept_after_ms": 0}


class WechatChannelStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("微信渠道数据库版本不受支持。")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS channel_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS channel_inbox (
                    generation INTEGER NOT NULL, message_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(generation,message_id));
                CREATE TABLE IF NOT EXISTS channel_receipts (
                    account_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    PRIMARY KEY(account_id,message_id));
                CREATE TABLE IF NOT EXISTS channel_conversations (
                    conversation_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL, tenant_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS channel_deliveries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, generation INTEGER NOT NULL,
                    delivery_key TEXT NOT NULL, text TEXT NOT NULL, client_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', forward_type TEXT NOT NULL DEFAULT 'reply',
                    UNIQUE(generation,delivery_key));
                PRAGMA user_version=1;
            """)
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(channel_deliveries)")}
            if "forward_type" not in columns:
                conn.execute("ALTER TABLE channel_deliveries ADD COLUMN forward_type TEXT NOT NULL DEFAULT 'reply'")
                conn.execute("""UPDATE channel_deliveries SET forward_type=CASE
                    WHEN delivery_key LIKE 'new:%' THEN 'command'
                    WHEN delivery_key LIKE 'approval:%' THEN 'approval'
                    WHEN delivery_key LIKE 'error:%' OR delivery_key LIKE 'unsupported:%'
                         OR delivery_key LIKE 'rejected:%' THEN 'error'
                    ELSE 'reply' END""")
            conn.execute("INSERT OR IGNORE INTO channel_state VALUES(1,?)", (json.dumps(_initial_state()),))

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _read(self, conn):
        return json.loads(conn.execute("SELECT payload FROM channel_state WHERE id=1").fetchone()[0])

    def _write(self, conn, state):
        conn.execute("UPDATE channel_state SET payload=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))

    def state(self):
        with self.lock, self._connect() as conn:
            return self._read(conn)

    def reserve_conversation(self, conversation_id, *, profile_id, actor_id, tenant_id):
        with self.lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO channel_conversations VALUES(?,?,?,?)",
                         (conversation_id, profile_id, actor_id, tenant_id))
            owner = conn.execute("SELECT profile_id,actor_id,tenant_id FROM channel_conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            if tuple(owner) != (profile_id, actor_id, tenant_id):
                raise ValueError("该空对话已经属于其他入口。")

    def owns_conversation(self, conversation_id, *, profile_id, actor_id, tenant_id):
        with self.lock, self._connect() as conn:
            owner = conn.execute("SELECT profile_id,actor_id,tenant_id FROM channel_conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            return owner is not None and tuple(owner) == (profile_id, actor_id, tenant_id)

    def new_conversation(self, generation, conversation_id, command_id, *, forward_command=True):
        """/new 的绑定切换、命令确认和出站提示同事务提交；保留后续收件与游标。"""
        import hashlib

        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn)
            if state["generation"] != generation:
                return False
            command = conn.execute("SELECT 1 FROM channel_inbox WHERE generation=? AND message_id=?", (generation, command_id)).fetchone()
            if command is None:
                return False
            state.update(conversation_id=conversation_id, history_position=0)
            conn.execute("DELETE FROM channel_deliveries WHERE generation=? AND status='pending'", (generation,))
            key = "new:" + command_id
            client_id = "erp_" + hashlib.sha256(f"{generation}:{key}".encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO channel_deliveries(generation,delivery_key,text,client_id,forward_type,status) VALUES(?,?,?,?,?,?)",
                         (generation, key, "已新建对话，请发送新的消息。", client_id, "command",
                          "pending" if forward_command else "skipped"))
            conn.execute("DELETE FROM channel_inbox WHERE generation=? AND message_id=?", (generation, command_id))
            self._write(conn, state)
            return True

    def configure(self, *, conversation_id: str, history_position: int, credentials=None, unbind=False):
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn)
            state["generation"] += 1
            if unbind:
                state = {**_initial_state(), "generation": state["generation"]}
            else:
                state.update(conversation_id=conversation_id, history_position=history_position,
                             accept_after_ms=int(time.time() * 1000))
                if credentials is not None:
                    state.update(credentials=credentials, cursor="", context_token="")
            # 更换绑定不向新目标发送旧回复，也不转移未交给 ERP 的旧输入。
            conn.execute("DELETE FROM channel_inbox")
            conn.execute("DELETE FROM channel_deliveries")
            self._write(conn, state)
            return state

    def ingest(self, generation, cursor, messages):
        """整批收件与游标推进同事务完成；崩溃重投由稳定消息 ID 去重。"""
        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn)
            if state["generation"] != generation:
                return False
            for message in messages:
                receipt = conn.execute("INSERT OR IGNORE INTO channel_receipts VALUES(?,?)",
                                       (state["credentials"]["account_id"], message["message_id"]))
                if receipt.rowcount:
                    conn.execute("INSERT INTO channel_inbox VALUES(?,?,?)",
                                 (generation, message["message_id"], json.dumps(message, ensure_ascii=False)))
                if message.get("context_token"):
                    state["context_token"] = message["context_token"]
            state["cursor"] = cursor
            self._write(conn, state)
            return True

    def inbox(self, generation):
        with self.lock, self._connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM channel_inbox WHERE generation=? ORDER BY rowid", (generation,))]

    def received(self, generation, message_id):
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM channel_inbox WHERE generation=? AND message_id=?", (generation, message_id))

    def observe(self, generation, position, replies, *, enabled_types):
        """观察位置与新增待发送文本原子保存，不因重启漏掉已完成回答。"""
        import hashlib

        with self.lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn)
            if state["generation"] != generation:
                return
            for forward_type, key, text in replies:
                # 固定发送标识用于网络结果不明时重试同一消息，不重新运行 ERP。
                client_id = "erp_" + hashlib.sha256(f"{generation}:{key}".encode()).hexdigest()
                conn.execute("""INSERT OR IGNORE INTO channel_deliveries
                    (generation,delivery_key,text,client_id,forward_type,status) VALUES(?,?,?,?,?,?)""",
                    (generation, key, text, client_id, forward_type,
                     "pending" if forward_type in enabled_types else "skipped"))
            state["history_position"] = position
            self._write(conn, state)

    def pending(self, generation):
        with self.lock, self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM channel_deliveries WHERE generation=? AND status='pending' ORDER BY sequence", (generation,))]

    def delivered(self, sequence):
        with self.lock, self._connect() as conn:
            conn.execute("UPDATE channel_deliveries SET status='sent' WHERE sequence=?", (sequence,))

    def skip_disabled(self, enabled_types):
        """停发已取消勾选的排队消息；重新勾选也不补发这些消息。"""
        with self.lock, self._connect() as conn:
            for row in conn.execute("SELECT sequence,forward_type FROM channel_deliveries WHERE status='pending'").fetchall():
                if row["forward_type"] not in enabled_types:
                    conn.execute("UPDATE channel_deliveries SET status='skipped' WHERE sequence=?", (row["sequence"],))


__all__ = ["WechatChannelStore"]
