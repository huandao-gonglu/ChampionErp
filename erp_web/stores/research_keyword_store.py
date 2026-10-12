"""热搜词结果缓存和跨进程查询占位；事务不跨外部请求。"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from erp_web.schemas.product_research import ProductResearchKeywordResponse


class ResearchKeywordStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS keyword_cache (
                cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '',
                expires REAL NOT NULL DEFAULT 0, token TEXT NOT NULL DEFAULT '',
                lease_until REAL NOT NULL DEFAULT 0)""")

    def reserve(self, key: str, *, refresh: bool, now: float) -> tuple[str, ProductResearchKeywordResponse | None]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM keyword_cache WHERE expires<=? AND lease_until<=?", (now, now))
            row = conn.execute("SELECT payload, expires, lease_until FROM keyword_cache WHERE cache_key=?", (key,)).fetchone()
            if row and row[2] > now:
                raise ValueError("相同条件的热搜词正在查询，请稍后查看，未发送重复请求。")
            if row and not refresh and row[1] > now:
                return "", json.loads(row[0])
            token = uuid4().hex
            conn.execute("""INSERT INTO keyword_cache(cache_key, token, lease_until) VALUES(?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET token=excluded.token, lease_until=excluded.lease_until""",
                (key, token, now + 180))
            return token, None

    def save(self, key: str, token: str, response: ProductResearchKeywordResponse, expires: float) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            cursor = conn.execute("""UPDATE keyword_cache SET payload=?, expires=?
                WHERE cache_key=? AND token=?""", (json.dumps(response, ensure_ascii=False), expires, key, token))
            if cursor.rowcount != 1:
                raise ValueError("热搜词查询占位已失效，请重新查看缓存。")

    def release(self, key: str, token: str) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            conn.execute("UPDATE keyword_cache SET token='', lease_until=0 WHERE cache_key=? AND token=?", (key, token))


__all__ = ["ResearchKeywordStore"]
