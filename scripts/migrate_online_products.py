"""显式、无损地将完整 v15 数据库升级到 v16；先保存 SQLite 一致性备份。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from erp_web.db import ONLINE_SCHEMA_SQL, _SCHEMA_SQL, _schema_signature


def migrate(path: Path) -> Path:
    if not path.is_file():
        raise ValueError("数据库不存在；空库由应用自行初始化")
    expected = sqlite3.connect(":memory:")
    expected.executescript(_SCHEMA_SQL.removesuffix(ONLINE_SCHEMA_SQL))
    signature = _schema_signature(expected)
    expected.close()
    conn = sqlite3.connect(path, timeout=10)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] != 15 or _schema_signature(conn) != signature:
            raise ValueError("只允许升级结构完整的 v15 数据库")
        backup = path.with_name(path.name + ".v15-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as dest:
            conn.backup(dest)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 15 or _schema_signature(conn) != signature:
            raise ValueError("备份期间数据库结构发生变化，已取消迁移")
        for statement in ONLINE_SCHEMA_SQL.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("PRAGMA user_version=16")
        conn.commit()
        return backup
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(f"已升级到 v16，原数据备份：{migrate(args.database)}")
