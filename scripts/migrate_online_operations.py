"""将完整 v16 主库无损升级到 v17：在线商品唯一占用仅覆盖排队和执行阶段。"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp_web.db import _SCHEMA_SQL, _current_schema_signature, _schema_signature


V16_SCHEMA_SQL = _SCHEMA_SQL.replace(
    "WHERE status IN ('queued','running');",
    "WHERE status IN ('queued','running','submitted','waiting_confirmation','outcome_unknown');",
)


def v16_schema_signature():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(V16_SCHEMA_SQL)
        return _schema_signature(conn)


def upgrade_online_index(conn: sqlite3.Connection) -> None:
    """由已校验并备份的迁移事务调用；不改任务、回执、幂等键或商品。"""
    conn.execute("DROP INDEX online_jobs_active_target")
    conn.execute("""CREATE UNIQUE INDEX online_jobs_active_target ON online_jobs(platform, account_id, target_id)
        WHERE status IN ('queued','running')""")
    conn.execute("PRAGMA user_version=17")
    if _schema_signature(conn) != _current_schema_signature():
        raise ValueError("迁移后的结构校验失败，已取消修改")


def migrate(database: Path) -> Path:
    database = database.resolve()
    if not database.is_file():
        raise ValueError("数据库不存在；空库由应用自行初始化")
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=10)) as conn:
        # 锁定写入后另开只读连接备份，确保备份与迁移基线一致。
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 16 or _schema_signature(conn) != v16_schema_signature():
            raise ValueError("只允许升级结构完整的 v16 数据库；其他差异须先核对")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.with_name(database.name + f".v16-{stamp}.bak")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(backup)) as saved:
                reader.backup(saved)
        upgrade_online_index(conn)
        conn.commit()
        return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(f"已升级到 v17，原数据完整备份：{migrate(args.database)}")
