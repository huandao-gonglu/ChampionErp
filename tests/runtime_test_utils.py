# -*- coding: utf-8 -*-
from __future__ import annotations

"""测试用临时 AppContext，隔离路径、数据库和有状态服务。"""

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from erp_web.context import (
    AppContext,
    AppPaths,
    clear_context,
    get_context,
    peek_context,
    set_context,
)
from erp_web.db import ErpDatabase


def seed_store_currency(
    platform: str,
    currency: str,
    *,
    source: str = "account_api",
    identity: dict[str, Any] | None = None,
    allowed: list[str] | None = None,
    mode: str | None = None,
    status: str | None = None,
) -> str:
    """在当前 context 的 store_auth 中显式写入店铺发布币种状态。

    迁移方案要求核价/发布测试显式创建 ready 店铺授权配置，而不是依赖注册表
    静态注入。返回写入后的 ``currency_fingerprint``，供核价 basis fixture 使用。
    """

    from erp_web.services.listing_currency_service import (
        compute_currency_fingerprint,
        store_identity_for_platform,
    )
    from erp_web.stores.config_store import (
        _STORE_AUTH_DETAIL_FIELDS,
        _STORE_CREDENTIAL_FIELDS,
    )

    platform_key = str(platform or "").strip().lower()
    section: dict[str, Any] = dict(identity or {})
    currency_mode = mode or ("locked" if len(allowed or [currency]) <= 1 else "selectable")
    allowed_currencies = (
        list(allowed)
        if allowed is not None
        else ([currency] if currency else [])
    )
    currency_source = source if currency_mode != "manual" else "manual"
    fingerprint = compute_currency_fingerprint(
        platform_key,
        store_identity_for_platform(platform_key, section),
        currency,
        allowed_currencies,
        currency_mode,
        currency_source,
    )
    section.update(
        {
            "listing_currency": currency,
            "allowed_currencies": allowed_currencies,
            "currency_mode": currency_mode,
            "currency_status": status or ("ready" if currency else "unresolved"),
            "currency_source": currency_source,
            "currency_verified_at": "2026-08-23T12:00:00Z",
            "currency_fingerprint": fingerprint,
            "currency_error_code": "",
            "currency_error_message": "",
        }
    )
    credentials = {
        key: value for key, value in section.items() if key in _STORE_CREDENTIAL_FIELDS
    }
    auth_detail = {
        key: value for key, value in section.items() if key in _STORE_AUTH_DETAIL_FIELDS
    }
    get_context().db.update_store_auth(
        platform_key,
        credentials=credentials or None,
        auth_detail=auth_detail or None,
        auth_status="测试成功",
        checked_at="2026-08-23T12:00:00Z",
    )
    return fingerprint


@contextmanager
def temp_app_context(app_dir: Path) -> Iterator[AppContext]:
    """Install a process context (paths + ErpDatabase) rooted at ``app_dir``.

    ``ErpDatabase`` runs its schema initialization in the constructor, so the
    temporary directory gets its own isolated SQLite store. The previous
    context is restored on exit. 退出时会先关闭临时上下文已经创建的资源，再
    恢复外层上下文；外层上下文本身不会被 ``set_context`` 自动关闭。
    """
    # 不通过 get_context() 获取 previous：首次测试若这样做会先打开真实仓库
    # 数据库，既破坏测试隔离，也会让 schema 升级被本地旧库阻断。
    previous = peek_context()
    paths = AppPaths.from_app_dir(Path(app_dir))
    bundled_presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    if bundled_presets.exists():
        shutil.copytree(
            bundled_presets,
            paths.config_dir / "presets",
            dirs_exist_ok=True,
        )
    context = AppContext(paths=paths, db=ErpDatabase(paths.db_path))
    set_context(context)
    try:
        yield context
    finally:
        try:
            context.close()
        finally:
            if previous is None:
                clear_context()
            else:
                set_context(previous)
