# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
MANAGED_HANDLER_ATTR = "_champion_erp_logging_handler"


def _parse_log_level(value: str | None) -> int:
    level_name = (value or DEFAULT_LOG_LEVEL).strip().upper()
    level = getattr(logging, level_name, None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _parse_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _default_log_file(app_dir: Path) -> Path:
    return app_dir / "data" / "logs" / "backend.log"


def _resolve_log_file(app_dir: Path) -> Path:
    raw_path = os.environ.get("ERP_LOG_FILE", "").strip()
    if not raw_path:
        return _default_log_file(app_dir)
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return app_dir / path


def _date_named_log_file(base_file: Path, current_date: date | None = None) -> Path:
    day = (current_date or date.today()).strftime("%Y-%m-%d")
    return base_file.with_name(f"{base_file.stem}-{day}{base_file.suffix}")


def _remove_managed_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if getattr(handler, MANAGED_HANDLER_ATTR, False):
            root_logger.removeHandler(handler)
            handler.close()


class DateNamedRotatingFileHandler(logging.Handler):
    """Write to base-YYYY-MM-DD.log and switch files after midnight."""

    def __init__(
        self,
        base_file: Path,
        max_bytes: int,
        backup_count: int,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self.base_file = base_file
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.encoding = encoding
        self._current_day = ""
        self._handler: RotatingFileHandler | None = None

    def active_log_file(self) -> Path:
        return _date_named_log_file(self.base_file)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:  # noqa: N802 - logging API name
        super().setFormatter(fmt)
        if self._handler:
            self._handler.setFormatter(fmt)

    def setLevel(self, level: int | str) -> None:  # noqa: N802 - logging API name
        super().setLevel(level)
        if self._handler:
            self._handler.setLevel(level)

    def _ensure_handler(self) -> RotatingFileHandler:
        today = date.today()
        day = today.strftime("%Y-%m-%d")
        if self._handler and self._current_day == day:
            return self._handler

        if self._handler:
            self._handler.close()

        log_file = _date_named_log_file(self.base_file, today)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(
            log_file,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding=self.encoding,
        )
        self._handler.setLevel(self.level)
        if self.formatter:
            self._handler.setFormatter(self.formatter)
        self._current_day = day
        return self._handler

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_handler().emit(record)
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if self._handler:
            self._handler.flush()

    def close(self) -> None:
        try:
            if self._handler:
                self._handler.close()
        finally:
            super().close()


def configure_logging(app_dir: Path | None = None) -> Path:
    """Configure backend logging once and return the active log file path."""
    resolved_app_dir = app_dir or Path(__file__).resolve().parents[1]
    log_file = _resolve_log_file(resolved_app_dir)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = _parse_log_level(os.environ.get("ERP_LOG_LEVEL"))
    max_bytes = _parse_positive_int(os.environ.get("ERP_LOG_MAX_BYTES"), DEFAULT_LOG_MAX_BYTES)
    backup_count = _parse_positive_int(os.environ.get("ERP_LOG_BACKUP_COUNT"), DEFAULT_LOG_BACKUP_COUNT)
    date_named = _parse_bool(os.environ.get("ERP_LOG_DATE_NAMED"), True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    _remove_managed_handlers(root_logger)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    setattr(console_handler, MANAGED_HANDLER_ATTR, True)

    if date_named:
        file_handler = DateNamedRotatingFileHandler(log_file, max_bytes, backup_count)
        active_log_file = file_handler.active_log_file()
    else:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        active_log_file = log_file
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, MANAGED_HANDLER_ATTR, True)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    logging.captureWarnings(True)
    return active_log_file
