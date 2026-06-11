from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from erp_web import logging_config
from erp_web.http_handler import Handler


def managed_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, logging_config.MANAGED_HANDLER_ATTR, False)
    ]


def cleanup_managed_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in managed_handlers():
        root_logger.removeHandler(handler)
        handler.close()


def flush_managed_handlers() -> None:
    for handler in managed_handlers():
        handler.flush()


def test_configure_logging_writes_to_file(tmp_path: Path, monkeypatch) -> None:
    cleanup_managed_handlers()
    log_file = tmp_path / "logs" / "backend.log"
    expected_log_file = log_file.with_name(f"backend-{date.today():%Y-%m-%d}.log")
    monkeypatch.setenv("ERP_LOG_FILE", str(log_file))
    monkeypatch.setenv("ERP_LOG_LEVEL", "INFO")

    try:
        configured_path = logging_config.configure_logging(app_dir=tmp_path)
        logging.getLogger("erp_web.test").info("logging configured")
        flush_managed_handlers()

        assert configured_path == expected_log_file
        assert "logging configured" in expected_log_file.read_text(encoding="utf-8")
    finally:
        cleanup_managed_handlers()


def test_configure_logging_can_use_fixed_file_name(tmp_path: Path, monkeypatch) -> None:
    cleanup_managed_handlers()
    log_file = tmp_path / "logs" / "backend.log"
    monkeypatch.setenv("ERP_LOG_FILE", str(log_file))
    monkeypatch.setenv("ERP_LOG_DATE_NAMED", "0")

    try:
        configured_path = logging_config.configure_logging(app_dir=tmp_path)
        logging.getLogger("erp_web.test").info("fixed file name")
        flush_managed_handlers()

        assert configured_path == log_file
        assert "fixed file name" in log_file.read_text(encoding="utf-8")
    finally:
        cleanup_managed_handlers()


def test_configure_logging_replaces_managed_handlers(tmp_path: Path, monkeypatch) -> None:
    cleanup_managed_handlers()
    monkeypatch.setenv("ERP_LOG_FILE", str(tmp_path / "backend.log"))

    try:
        logging_config.configure_logging(app_dir=tmp_path)
        logging_config.configure_logging(app_dir=tmp_path)

        assert len(managed_handlers()) == 2
    finally:
        cleanup_managed_handlers()


def test_http_handler_log_message_uses_access_logger(caplog) -> None:
    request_handler = object.__new__(Handler)
    request_handler.address_string = lambda: "127.0.0.1"  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="erp.access"):
        request_handler.log_message('"%s" %s', "GET /api/state HTTP/1.1", "200")

    assert "127.0.0.1 - \"GET /api/state HTTP/1.1\" 200" in caplog.text
