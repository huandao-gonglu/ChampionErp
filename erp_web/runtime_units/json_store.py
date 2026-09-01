# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("read_json 读取失败，已使用默认值：%s", path, exc_info=True)
    return default


def write_json(path: Path, data: Any) -> None:
    """以同目录临时文件原子写入 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if os.name != "nt":
            tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


__all__ = ["read_json", "write_json"]
