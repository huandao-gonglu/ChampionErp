# -*- coding: utf-8 -*-
"""当前账号空间的短期导出归档存储与自动清理。

导出文件写入当前登录账号自己的 ``data/exports/`` 目录，与账号空间天然隔离；
配合 ``GET /api/exports/download?token=...`` 流式分块与 Range 断点续传，
避免大体积 ZIP 占用 Python 内存或触发前端 JS 超时。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from erp_web.services.workspace_files import owned_path

EXPORT_ARCHIVE_TTL_SECONDS = 30 * 60
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_ARCHIVE_PREFIX = "export_"


@dataclass(frozen=True)
class ExportArchiveEntry:
    token: str
    path: Path
    filename: str
    content_type: str
    size_bytes: int
    expires_at: float

    @property
    def download_url(self) -> str:
        return f"/api/exports/download?token={self.token}"


def exports_dir(app_dir: Path) -> Path:
    target = owned_path(Path(app_dir) / "data" / "exports", app_dir=app_dir)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _archive_path(root: Path, token: str) -> Path:
    return root / f"{_ARCHIVE_PREFIX}{token}.zip"


def _meta_path(root: Path, token: str) -> Path:
    return root / f"{_ARCHIVE_PREFIX}{token}.meta.json"


def cleanup_expired_exports(app_dir: Path, *, now: float | None = None) -> int:
    """清理当前空间内已过期的短期导出文件，返回删除的归档数。"""
    root = exports_dir(app_dir)
    current = time.time() if now is None else now
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for meta_file in entries:
        name = meta_file.name
        if not (name.startswith(_ARCHIVE_PREFIX) and name.endswith(".meta.json")):
            continue
        token = name[len(_ARCHIVE_PREFIX):-len(".meta.json")]
        if not _TOKEN_RE.match(token):
            continue
        expired = False
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            expires_at = float(data.get("expires_at") or 0)
            expired = expires_at <= current
        except (OSError, ValueError, TypeError, AttributeError):
            expired = True
        if expired:
            for victim in (_archive_path(root, token), meta_file):
                try:
                    victim.unlink(missing_ok=True)
                except OSError:
                    pass
            removed += 1
    # 顺手清理超过 1 小时的无元数据孤立临时包。
    orphan_cutoff = current - max(EXPORT_ARCHIVE_TTL_SECONDS * 2, 3600)
    for item in entries:
        name = item.name
        if not (name.startswith(_ARCHIVE_PREFIX) and name.endswith(".zip")):
            continue
        token = name[len(_ARCHIVE_PREFIX):-len(".zip")]
        if not _TOKEN_RE.match(token):
            continue
        if _meta_path(root, token).exists():
            continue
        try:
            if item.stat().st_mtime < orphan_cutoff:
                item.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def allocate_export_target(app_dir: Path) -> tuple[str, Path]:
    """为本次导出分配唯一 token 与目标落盘路径，同时顺手清理过期文件。"""
    cleanup_expired_exports(app_dir)
    root = exports_dir(app_dir)
    token = uuid4().hex
    return token, owned_path(_archive_path(root, token), app_dir=app_dir)


def finalize_export_archive(
    app_dir: Path,
    token: str,
    file_path: Path,
    *,
    filename: str,
    content_type: str = "application/zip",
    ttl_seconds: int = EXPORT_ARCHIVE_TTL_SECONDS,
) -> ExportArchiveEntry:
    """将已落盘的导出文件登记为可下载归档。"""
    if not _TOKEN_RE.match(token):
        raise ValueError("导出归档 token 无效")
    root = exports_dir(app_dir)
    target = owned_path(file_path, app_dir=app_dir)
    size_bytes = target.stat().st_size
    now = time.time()
    expires_at = now + max(60, int(ttl_seconds))
    meta = {
        "token": token,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "created_at": now,
        "expires_at": expires_at,
    }
    meta_file = owned_path(_meta_path(root, token), app_dir=app_dir)
    pending_path = ""
    try:
        with NamedTemporaryFile("w", dir=root, encoding="utf-8", delete=False) as pending:
            json.dump(meta, pending, ensure_ascii=False)
            pending_path = pending.name
        os.replace(pending_path, meta_file)
    except Exception:
        if pending_path:
            try:
                os.unlink(pending_path)
            except OSError:
                pass
        raise
    return ExportArchiveEntry(
        token=token,
        path=target,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        expires_at=expires_at,
    )


def resolve_export_archive(app_dir: Path, token: str) -> ExportArchiveEntry | None:
    """解析当前空间内的导出下载票据；跨空间、不存在或已过期一律返回 None。"""
    clean = str(token or "").strip().lower()
    if not _TOKEN_RE.match(clean):
        return None
    root = exports_dir(app_dir)
    meta_file = _meta_path(root, clean)
    archive_file = _archive_path(root, clean)
    if not meta_file.is_file() or not archive_file.is_file():
        return None
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        expires_at = float(data.get("expires_at") or 0)
        if expires_at <= time.time():
            cleanup_expired_exports(app_dir)
            return None
        filename = str(data.get("filename") or f"export-{clean[:8]}.zip").strip()
        content_type = str(data.get("content_type") or "application/zip").strip() or "application/zip"
        size_bytes = archive_file.stat().st_size
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return ExportArchiveEntry(
        token=clean,
        path=owned_path(archive_file, app_dir=app_dir),
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        expires_at=expires_at,
    )


__all__ = [
    "EXPORT_ARCHIVE_TTL_SECONDS",
    "ExportArchiveEntry",
    "allocate_export_target",
    "cleanup_expired_exports",
    "exports_dir",
    "finalize_export_archive",
    "resolve_export_archive",
]
