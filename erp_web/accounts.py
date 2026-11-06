"""部署入口：备份现有业务目录并交互式创建初始管理员。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile

from erp_web.schemas.accounts import normalize_email, validate_display_name, validate_password
from erp_web.services.account_service import AccountService
from erp_web.stores.account_store import AccountStore


def backup_business(app_dir: Path) -> Path:
    """调用方须先停止后端；SQLite 使用原生备份，不复制活动 WAL。"""
    directory = app_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"before-accounts-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="champion-account-backup-") as temporary:
        with tarfile.open(destination, "w:gz", dereference=False) as archive:
            files = [app_dir / "erp.sqlite3"]
            for name in ("config", "data"):
                root = app_dir / name
                if root.exists():
                    files.extend(sorted(root.rglob("*")))
            for source in files:
                if not source.exists() or source.is_dir() or source.name.endswith(("-wal", "-shm")):
                    continue
                relative = source.relative_to(app_dir)
                if source.suffix in {".sqlite3", ".sqlite", ".db"} and not source.is_symlink():
                    snapshot = Path(temporary) / relative
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(snapshot) as target:
                        original.backup(target)
                    archive.add(snapshot, arcname=str(relative), recursive=False)
                else:
                    archive.add(source, arcname=str(relative), recursive=False)
    destination.chmod(0o600)
    return destination


def initialize_admin(app_dir: Path, email: str, name: str, password_reader=getpass.getpass):
    app_dir = Path(app_dir).expanduser().resolve()
    store = AccountStore(app_dir / "accounts.sqlite3")
    existing = store.admin()
    if existing:
        if email and normalize_email(email) != existing["email"]:
            raise ValueError("管理员已存在，初始化命令不能更改邮箱、密码或业务归属。")
        return existing, None
    email = normalize_email(email)
    name = validate_display_name(name)
    password = validate_password(password_reader("管理员密码（15 至 128 个字符）："))
    if password_reader("再次输入密码：") != password:
        raise ValueError("两次输入的密码不一致。")
    backup = backup_business(app_dir)
    user = AccountService(store).create({"email": email, "display_name": name, "password": password}, admin=True)
    return user, backup


def main():
    parser = argparse.ArgumentParser(description="停止后端后，备份并初始化个人账号体系。")
    parser.add_argument("action", choices=["init-admin"])
    parser.add_argument("--app-dir", type=Path, default=Path(os.environ.get("ERP_APP_DIR", Path(__file__).resolve().parents[1])))
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="管理员")
    args = parser.parse_args()
    try:
        user, backup = initialize_admin(args.app_dir, args.email, args.name)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")
    print(f"管理员已就绪：{user['email']}（{user['id']}）。现有业务目录归属保持不变。")
    if backup:
        print(f"业务备份：{backup}")


if __name__ == "__main__":
    main()
