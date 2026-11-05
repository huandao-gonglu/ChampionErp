"""旧凭据布局的只读启动检查；迁移由离线脚本完成，运行时不回退到旧库。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def legacy_credential_locations(app_dir: Path) -> list[str]:
    app_dir = Path(app_dir).expanduser().resolve()
    pending = []
    orders = Path(app_dir) / "data/order-notifications.sqlite3"
    if orders.is_file():
        with closing(sqlite3.connect(orders.as_uri() + "?mode=ro", uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "bus_settings" in tables and conn.execute("SELECT 1 FROM bus_settings WHERE key='credentials'").fetchone():
                pending.append("跨境巴士授权")
            if "settings" in tables and conn.execute("SELECT 1 FROM settings WHERE key LIKE 'token:%' OR key='public_url'").fetchone():
                pending.append("订单回调配置")
    wechat = Path(app_dir) / "data/wechat-channel.sqlite3"
    if wechat.is_file():
        with closing(sqlite3.connect(wechat.as_uri() + "?mode=ro", uri=True)) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel_state'").fetchone()
            row = conn.execute("SELECT payload FROM channel_state WHERE id=1").fetchone() if exists else None
            if row and json.loads(row[0]).get("credentials"):
                pending.append("微信绑定凭据")
    return pending


def assert_credentials_migrated(app_dir: Path) -> None:
    pending = legacy_credential_locations(app_dir)
    if pending:
        raise RuntimeError("以下授权仍在旧业务库中：" + "、".join(pending)
                           + "。请停止服务后执行 scripts/migrate_credentials.py <应用数据目录>；原数据不会被启动过程覆盖。")


__all__ = ["assert_credentials_migrated", "legacy_credential_locations"]
