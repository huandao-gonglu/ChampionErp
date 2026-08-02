from __future__ import annotations

import logging
import os
from datetime import date
from io import BytesIO
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
        if os.name != "nt":
            assert log_file.parent.stat().st_mode & 0o777 == 0o700
            assert expected_log_file.stat().st_mode & 0o777 == 0o600
    finally:
        cleanup_managed_handlers()


def test_debug_level_can_be_written_to_file(tmp_path: Path, monkeypatch) -> None:
    cleanup_managed_handlers()
    log_file = tmp_path / "logs" / "backend.log"
    expected_log_file = log_file.with_name(f"backend-{date.today():%Y-%m-%d}.log")
    monkeypatch.setenv("ERP_LOG_FILE", str(log_file))
    monkeypatch.setenv("ERP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("ERP_LOG_FILE_LEVEL", "DEBUG")
    monkeypatch.setenv("ERP_LOG_CONSOLE_LEVEL", "ERROR")

    try:
        logging_config.configure_logging(app_dir=tmp_path)
        logging.getLogger("erp_web.test").debug("debug diagnostics")
        flush_managed_handlers()

        assert "debug diagnostics" in expected_log_file.read_text(encoding="utf-8")
    finally:
        cleanup_managed_handlers()


def test_file_level_filters_lower_severity_records(tmp_path: Path, monkeypatch) -> None:
    cleanup_managed_handlers()
    log_file = tmp_path / "logs" / "backend.log"
    expected_log_file = log_file.with_name(f"backend-{date.today():%Y-%m-%d}.log")
    monkeypatch.setenv("ERP_LOG_FILE", str(log_file))
    monkeypatch.setenv("ERP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ERP_LOG_FILE_LEVEL", "WARNING")
    monkeypatch.setenv("ERP_LOG_CONSOLE_LEVEL", "ERROR")

    try:
        logging_config.configure_logging(app_dir=tmp_path)
        logger = logging.getLogger("erp_web.test")
        logger.debug("filtered debug")
        logger.info("filtered info")
        logger.warning("written warning")
        flush_managed_handlers()

        text = expected_log_file.read_text(encoding="utf-8")
        assert "filtered debug" not in text
        assert "filtered info" not in text
        assert "written warning" in text
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
        if os.name != "nt":
            assert log_file.parent.stat().st_mode & 0o777 == 0o700
            assert log_file.stat().st_mode & 0o777 == 0o600
    finally:
        cleanup_managed_handlers()


def test_rotated_backend_logs_remain_private(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cleanup_managed_handlers()
    log_file = tmp_path / "logs" / "backend.log"
    monkeypatch.setenv("ERP_LOG_FILE", str(log_file))
    monkeypatch.setenv("ERP_LOG_DATE_NAMED", "0")
    monkeypatch.setenv("ERP_LOG_MAX_BYTES", "128")
    monkeypatch.setenv("ERP_LOG_BACKUP_COUNT", "2")

    try:
        logging_config.configure_logging(app_dir=tmp_path)
        for index in range(20):
            logging.getLogger("erp_web.test").info(
                "rotation-%s-%s",
                index,
                "x" * 80,
            )
        flush_managed_handlers()

        rotated = [
            path
            for path in log_file.parent.glob("backend.log*")
            if path.is_file()
        ]
        assert len(rotated) >= 2
        if os.name != "nt":
            assert all(
                path.stat().st_mode & 0o777 == 0o600
                for path in rotated
            )
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


def test_http_access_log_never_records_query_string(caplog) -> None:
    request_handler = object.__new__(Handler)
    request_handler.address_string = lambda: "127.0.0.1"  # type: ignore[method-assign]
    request_handler.command = "GET"
    request_handler.path = (
        "/auth/mercadolibre/callback?code=oauth-secret-code&state=secret-state"
    )
    request_handler.request_version = "HTTP/1.1"

    with caplog.at_level(logging.INFO, logger="erp.access"):
        request_handler.log_request(200, 42)

    assert (
        '127.0.0.1 - "GET /auth/mercadolibre/callback HTTP/1.1" 200 42'
        in caplog.text
    )
    assert "oauth-secret-code" not in caplog.text
    assert "secret-state" not in caplog.text


def test_http_json_failure_logs_safe_business_diagnostics(caplog) -> None:
    request_handler = object.__new__(Handler)
    request_handler.path = "/api/category-match?token=query-secret"
    request_handler.send_response = lambda status: None  # type: ignore[method-assign]
    request_handler.send_header = lambda key, value: None  # type: ignore[method-assign]
    request_handler.end_headers = lambda: None  # type: ignore[method-assign]
    request_handler.wfile = BytesIO()

    with caplog.at_level(logging.DEBUG, logger="erp.http.response"):
        request_handler.send_json(
            {
                "ok": False,
                "failure": {
                    "code": "AI_MODEL_TOOL_CALLING_UNSUPPORTED",
                    "message": "authorization=secret-value 当前模型缺少 tool_calling",
                    "stage": "model",
                    "retryable": False,
                },
            },
            424,
        )

    assert "path=/api/category-match status=424" in caplog.text
    assert "error_code=AI_MODEL_TOOL_CALLING_UNSUPPORTED" in caplog.text
    assert "error_stage=model" in caplog.text
    assert "当前模型缺少 tool_calling" in caplog.text
    assert "secret-value" not in caplog.text
    assert "query-secret" not in caplog.text
