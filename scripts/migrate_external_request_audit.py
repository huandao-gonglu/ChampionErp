"""将 v16 主库中早期开发版本的请求审计表无损迁出；不修改业务数据或自动解除阻断。"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp_web.db import _current_schema_signature, _schema_signature
from erp_web.stores.external_request_store import EXTERNAL_REQUEST_SCHEMA_SQL

# 这是 2026-09-28 开发中间版本真实写入主库的格式，仅用于显式数据迁移。
SOURCE_AUDIT_SCHEMA_SQL = '''
CREATE TABLE external_attempts (
    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, parent_id TEXT NOT NULL,
    attempt INTEGER NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
    credential_id TEXT NOT NULL, interface TEXT NOT NULL, source TEXT NOT NULL,
    trigger TEXT NOT NULL, method TEXT NOT NULL, semantics TEXT NOT NULL,
    created REAL NOT NULL, released REAL, completed REAL, sent INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', lease_until REAL NOT NULL DEFAULT 0);
CREATE INDEX external_attempt_filter ON external_attempts(platform,account_id,created);
CREATE INDEX external_attempt_operation ON external_attempts(operation_id);
CREATE TABLE external_blocks (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, scope TEXT NOT NULL, scope_key TEXT NOT NULL,
    failure TEXT NOT NULL, created REAL NOT NULL, blocked_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(platform,account_id,scope,scope_key));
CREATE TABLE external_recoveries (
    id TEXT PRIMARY KEY, platform TEXT, account_id TEXT, scope TEXT, scope_key TEXT,
    created REAL, reason TEXT);
CREATE TABLE external_policies (
    platform TEXT NOT NULL, interface TEXT NOT NULL, concurrency INTEGER,
    requests_per_minute INTEGER, PRIMARY KEY(platform,interface));
'''
TABLE_KEYS = {
    "external_attempts": ("id",),
    "external_blocks": ("platform", "account_id", "scope", "scope_key"),
    "external_recoveries": ("id",),
    "external_policies": ("platform", "interface"),
}


def _signature(sql):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(sql)
        return _schema_signature(conn)


def _check_source(conn):
    expected = tuple(sorted(_current_schema_signature() + _signature(SOURCE_AUDIT_SCHEMA_SQL)))
    if conn.execute("PRAGMA user_version").fetchone()[0] != 16 or _schema_signature(conn) != expected:
        raise ValueError("只允许迁出完整 v16 主库中已识别的早期请求审计表；其他结构差异须单独核对")


def _private_file(path):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _copy_audit(source, destination):
    for table, keys in TABLE_KEYS.items():
        source_columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
        columns = [row[1] for row in destination.execute(f'PRAGMA table_info("{table}")')]
        defaults = {"quota_key": "", "consecutive_failure_limit": None}
        if set(columns) - set(source_columns) - defaults.keys():
            raise ValueError(f"审计表存在未识别的目标字段：{table}")
        for row in source.execute(f'SELECT * FROM "{table}"'):
            old = dict(zip(source_columns, row, strict=True))
            values = tuple(old[column] if column in old else defaults[column] for column in columns)
            where = " AND ".join(f'"{key}"=?' for key in keys)
            existing = destination.execute(f'SELECT * FROM "{table}" WHERE {where}', tuple(old[key] for key in keys)).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError(f"目标审计库存在冲突记录，未覆盖：{table}")
                continue
            placeholders = ",".join("?" for _ in columns)
            destination.execute(f'INSERT INTO "{table}" VALUES({placeholders})', values)


def migrate(database: Path, destination: Path | None = None) -> Path:
    database = database.resolve()
    destination = (destination or database.parent / "data" / "external-requests.sqlite3").resolve()
    if not database.is_file() or destination == database:
        raise ValueError("主库必须存在，目标审计库必须使用不同文件")
    source_uri = database.as_uri() + "?mode=rw"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
        # 锁定主库的写入后再校验、备份、复制，避免迁移期间仍有旧进程追加记录。
        source.execute("BEGIN IMMEDIATE")
        _check_source(source)
        backup_dir = database.parent / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"erp-before-external-audit-{stamp}.sqlite3"
        _private_file(backup)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(backup)) as saved:
                reader.backup(saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        new_destination = not destination.exists()
        if new_destination:
            _private_file(destination)
        try:
            with closing(sqlite3.connect(destination, timeout=10)) as target:
                if new_destination:
                    target.executescript(EXTERNAL_REQUEST_SCHEMA_SQL)
                    target.execute("PRAGMA user_version=1")
                if target.execute("PRAGMA user_version").fetchone()[0] != 1 or _schema_signature(target) != _signature(EXTERNAL_REQUEST_SCHEMA_SQL):
                    raise ValueError("目标审计库不是结构完整的 v1，未覆盖")
                target.execute("BEGIN IMMEDIATE")
                _copy_audit(source, target)
                target.commit()
            # 目标已独立提交，再删除源表。中断后可重跑：相同记录跳过，冲突则停止。
            for table in TABLE_KEYS:
                source.execute(f'DROP TABLE "{table}"')
            if _schema_signature(source) != _current_schema_signature():
                raise ValueError("迁出后的主库结构仍不匹配，已取消主库修改")
            source.commit()
        except BaseException:
            source.rollback()
            raise
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    print(f"审计已迁至独立数据库；主库完整备份：{migrate(args.database, args.destination)}")
