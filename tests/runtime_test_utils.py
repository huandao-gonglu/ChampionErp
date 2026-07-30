# -*- coding: utf-8 -*-
from __future__ import annotations

"""测试用临时 AppContext，隔离路径、数据库和有状态服务。"""

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from erp_web.context import AppContext, AppPaths, get_context, set_context
from erp_web.db import ErpDatabase


@contextmanager
def temp_app_context(app_dir: Path) -> Iterator[AppContext]:
    """Install a process context (paths + ErpDatabase) rooted at ``app_dir``.

    ``ErpDatabase`` runs its schema initialization in the constructor, so the
    temporary directory gets its own isolated SQLite store. The previous
    context is restored on exit. 退出时会先关闭临时上下文已经创建的资源，再
    恢复外层上下文；外层上下文本身不会被 ``set_context`` 自动关闭。
    """
    previous = get_context()
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
            set_context(previous)
