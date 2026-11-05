"""离线集中既有授权：先备份、校验并提交主库，再清理旧凭据；不迁移业务记录。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp_web.db import SCHEMA_VERSION, _current_schema_signature, _schema_signature
from erp_web.stores.credential_layout import assert_credentials_migrated


def _read_plan(connections):
    records, removals = [], []
    orders = connections.get("data/order-notifications.sqlite3")
    if orders is not None:
        tables = {row[0] for row in orders.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "bus_settings" in tables:
            row = orders.execute("SELECT value FROM bus_settings WHERE key='credentials'").fetchone()
            if row:
                value = json.loads(row[0])
                if not isinstance(value, dict):
                    raise ValueError("旧跨境巴士凭据不是对象，迁移已停止")
                records.append(("credentials:crossborderbus", "active", value))
                removals.append(("data/order-notifications.sqlite3", "DELETE FROM bus_settings WHERE key='credentials'", ()))
        if "settings" in tables:
            for key, value in orders.execute("SELECT key,value FROM settings WHERE key LIKE 'token:%' OR key='public_url'"):
                records.append(("order_notifications", key, value))
                removals.append(("data/order-notifications.sqlite3", "DELETE FROM settings WHERE key=?", (key,)))
    wechat = connections.get("data/wechat-channel.sqlite3")
    if wechat is not None and wechat.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel_state'").fetchone():
        row = wechat.execute("SELECT payload FROM channel_state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {}
        if "credentials" in state:
            credentials = state.pop("credentials")
            if credentials:
                if not isinstance(credentials, dict) or not all(credentials.get(key) for key in ("token", "account_id", "user_id")):
                    raise ValueError("旧微信凭据不完整，迁移已停止")
                if state.get("credential_id"):
                    raise ValueError("微信同时存在新旧凭据引用，请先核对；未自动覆盖")
                encoded = json.dumps(credentials, sort_keys=True, ensure_ascii=False).encode()
                ident = "migrated-" + hashlib.sha256(encoded).hexdigest()
                records.append(("credentials:wechat", ident, credentials))
                state["credential_id"] = ident
            else:
                state.setdefault("credential_id", "")
            removals.append(("data/wechat-channel.sqlite3", "UPDATE channel_state SET payload=? WHERE id=1",
                             (json.dumps(state, ensure_ascii=False),)))
    return records, removals


def _backup(app_dir, names):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = app_dir / "data/backups" / ("credentials-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    for name in names:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect((app_dir / name).as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(target)) as saved:
                reader.backup(saved)
    (backup / "manifest.json").write_text(json.dumps({"source": str(app_dir), "databases": names,
        "purpose": "统一授权存储前的一致性备份"}, ensure_ascii=False, indent=2) + "\n")
    (backup / "manifest.json").chmod(0o600)
    return backup


def _remove_legacy(connections, removals):
    for name, sql, parameters in removals:
        connections[name].execute(sql, parameters)
    for name, connection in connections.items():
        if name != "erp.sqlite3":
            connection.commit()


def migrate(app_dir: Path) -> dict:
    """重复执行不会更换令牌；遇到不同的新凭据拒绝覆盖，保留两侧数据供核对。"""
    app_dir = app_dir.expanduser().resolve()
    if not (app_dir / "erp.sqlite3").is_file():
        raise ValueError("主数据库不存在；新环境由应用正常初始化即可")
    names = [name for name in ("erp.sqlite3", "data/order-notifications.sqlite3", "data/wechat-channel.sqlite3")
             if (app_dir / name).is_file()]
    with ExitStack() as stack:
        connections = {}
        for name in names:
            connection = stack.enter_context(closing(sqlite3.connect((app_dir / name).as_uri() + "?mode=rw", uri=True, timeout=10)))
            connection.execute("BEGIN IMMEDIATE")
            connections[name] = connection
        main = connections["erp.sqlite3"]
        if main.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION or _schema_signature(main) != _current_schema_signature():
            raise ValueError("主数据库结构不是当前完整版本，请先完成对应版本迁移")
        records, removals = _read_plan(connections)
        for namespace, key, value in records:
            existing = main.execute("SELECT secret_json FROM runtime_secrets WHERE namespace=? AND secret_path=?", (namespace, key)).fetchone()
            if existing is not None and json.loads(existing[0]) != value:
                raise ValueError(f"主库与旧库的 {namespace} 配置冲突，未覆盖任何凭据；请先核对")
        if not removals:
            return {"status": "already_current", "records": 0, "backup": None}
        backup = _backup(app_dir, names)
        now = datetime.now(timezone.utc).isoformat()
        for namespace, key, value in records:
            main.execute("INSERT OR IGNORE INTO runtime_secrets(namespace,secret_path,secret_json,updated_at) VALUES(?,?,?,?)",
                         (namespace, key, json.dumps(value, ensure_ascii=False), now))
        # 先持久提交凭据，再清理旧记录。中途失败时旧记录尚在，重跑会核对后继续。
        main.commit()
        _remove_legacy(connections, removals)
    assert_credentials_migrated(app_dir)
    return {"status": "migrated", "records": len(records), "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__ + " 运行前须退出使用该数据目录的开发服务或桌面程序。")
    parser.add_argument("app_dir", type=Path)
    args = parser.parse_args()
    result = migrate(args.app_dir)
    print(json.dumps(result, ensure_ascii=False))
