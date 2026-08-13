from __future__ import annotations

from erp_web.db import REQUIRED_TABLES, SCHEMA_VERSION, ErpDatabase
from erp_web.runtime_units.publishing_bus_core import PublishingBus

from .support import sensitive_paths


def test_database_owns_schema_and_connection_policy(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    with database._connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        user_version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        journal_mode = connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]
        foreign_keys = connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]
        busy_timeout = connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0]
        publish_job_indexes = connection.execute(
            'PRAGMA index_list("publish_jobs")'
        ).fetchall()
        unique_publish_job_columns = {
            tuple(
                str(column["name"])
                for column in connection.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                ).fetchall()
            )
            for index in publish_job_indexes
            if int(index["unique"] or 0) == 1
        }
        ai_session_indexes = connection.execute(
            'PRAGMA index_list("ai_sessions")'
        ).fetchall()
        ai_session_index_columns = {
            tuple(
                str(column["name"])
                for column in connection.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                ).fetchall()
            )
            for index in ai_session_indexes
        }
        ai_session_foreign_keys = connection.execute(
            'PRAGMA foreign_key_list("ai_sessions")'
        ).fetchall()
    assert set(REQUIRED_TABLES).issubset(tables)
    assert user_version == SCHEMA_VERSION
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout >= 5_000
    assert ("idempotency_key",) in unique_publish_job_columns
    assert (
        "parent_session_id",
        "updated_at",
    ) in ai_session_index_columns
    assert any(
        str(reference["table"]) == "ai_sessions"
        and str(reference["from"]) == "parent_session_id"
        and str(reference["to"]) == "session_id"
        and str(reference["on_delete"]).upper() == "SET NULL"
        for reference in ai_session_foreign_keys
    )


def test_publish_jobs_never_persist_credentials(tmp_path) -> None:
    access_token = "architecture-access-token"
    app_secret = "architecture-app-secret"

    class SuccessfulAdapter:
        @staticmethod
        def resolve_category(
            product: dict,
            config: dict,
        ) -> dict:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict,
            config: dict,
        ) -> list[str]:
            return []

        @staticmethod
        def publish(
            product: dict,
            platform: str,
            config: dict,
        ) -> dict:
            assert (
                config["mercadolibre"]["access_token"]
                == access_token
            )
            return {"ok": True, "status": "published"}

    database = ErpDatabase(tmp_path / "erp.sqlite3")
    bus = PublishingBus(
        database,
        {"mercadolibre": SuccessfulAdapter()},
        config_provider=lambda: {
            "mercadolibre": {
                "access_token": access_token,
                "app_secret": app_secret,
            }
        },
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {
                "product_id": "architecture-product",
                "name": "安全发布任务",
            },
            ["mercadolibre"],
            idempotency_key="architecture:publish-credentials",
            targets={
                "mercadolibre": {
                    "draft_id": "architecture-draft",
                    "site": "mlm",
                    "product_id": "architecture-product",
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
        persisted = database.load_publish_job(
            queued["job_id"]
        )
        with database._connect() as connection:
            raw_payload = connection.execute(
                (
                    "SELECT payload_json FROM publish_jobs "
                    "WHERE job_id = ?"
                ),
                (queued["job_id"],),
            ).fetchone()[0]
    finally:
        bus.executor.shutdown(wait=True)

    assert persisted["status"] == "completed"
    assert "config" not in persisted
    assert not sensitive_paths(persisted)
    assert access_token not in raw_payload
    assert app_secret not in raw_payload
