"""账号空间内的展示标题索引。规范消息和运行版本仍由原生历史独占。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import time


class ConversationTitles:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS titles (
                conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL, claimed_at REAL)""")

            db.execute("CREATE TABLE IF NOT EXISTS pins (conversation_id TEXT PRIMARY KEY)")

    def pinned_ids(self):
        with closing(self.connect()) as db:
            return {key for key, in db.execute("SELECT conversation_id FROM pins")}

    def set_pin(self, key, pinned):
        with closing(self.connect()) as db, db:
            if pinned:
                db.execute("INSERT OR IGNORE INTO pins VALUES (?)", (key,))
            else:
                db.execute("DELETE FROM pins WHERE conversation_id=?", (key,))

    def rename(self, key, title):
        with closing(self.connect()) as db, db:
            db.execute("""INSERT INTO titles(conversation_id,title,state) VALUES (?,?,'manual')
                          ON CONFLICT(conversation_id) DO UPDATE SET title=excluded.title,state='manual'""", (key, title))

    def is_deleted(self, key):
        with closing(self.connect()) as db:
            return db.execute("SELECT 1 FROM titles WHERE conversation_id=? AND state='deleted'", (key,)).fetchone() is not None

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get_many(self, ids):
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with closing(self.connect()) as db:
            return {key: {"title": title, "title_manual": state == "manual", "title_pending": state == "running" and claimed_at > time.time() - 120}
                    for key, title, state, claimed_at in db.execute(
                        f"SELECT conversation_id,title,state,claimed_at FROM titles WHERE conversation_id IN ({placeholders})", ids)}

    def seed(self, rows):
        with closing(self.connect()) as db, db:
            db.executemany("INSERT OR IGNORE INTO titles VALUES(?,?,'initial',NULL)", rows)

    def claim(self, key, fallback, ready):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO titles VALUES(?,?,'initial',NULL)", (key, fallback))
            return ready and db.execute(
                "UPDATE titles SET state='running',claimed_at=? WHERE conversation_id=? AND state='initial'",
                (time.time(), key),
            ).rowcount == 1

    def finish(self, key, title):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE titles SET title=COALESCE(?,title), state='finished' WHERE conversation_id=? AND state='running'",
                       (title, key))
