from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from erp_web import db as erp_db
from erp_web.db import ErpDatabase


def sample_product(title: str = "Imported title", source_url: str = "https://example.com/item") -> dict:
    return {
        "product_id": "",
        "name": title,
        "brand": "BrandX",
        "model": "ModelY",
        "selling_points": ["Point A", "Point B"],
        "weight_kg": "0.5",
        "source": {
            "source_url": source_url,
            "source_platform": "1688",
            "title": title,
            "price": "12.34",
            "currency": "CNY",
            "bullets": ["Point A", "Point B"],
            "description": "Original description",
            "dimensions": {"length_cm": "10", "width_cm": "8", "height_cm": "3"},
            "weight_kg": "0.5",
            "image_pool": [
                {
                    "id": "img_1",
                    "url": "https://example.com/1.jpg",
                    "preview_url": "https://example.com/1.jpg",
                    "origin": "1688",
                    "usage": "main",
                    "platforms": ["mercadolibre"],
                    "is_main": True,
                    "selected": True,
                    "order": 0,
                    "width": 1500,
                    "height": 1500,
                    "size_label": "1500 x 1500",
                }
            ],
        },
        "drafts": {
            "mercadolibre": {
                "enabled": True,
                "title": "Titulo MX",
                "description": "Descripcion MX",
                "category_id": "MLM123",
                "attributes": {"BRAND": "BrandX"},
                "price": "19.99",
                "status": "copy_ready",
            }
        },
    }


class ErpDbTests(unittest.TestCase):
    def _db(self, app_dir: Path) -> ErpDatabase:
        return ErpDatabase(app_dir / erp_db.DEFAULT_DB_NAME)

    @staticmethod
    def _create_v4_draft_table(
        conn: sqlite3.Connection,
        table: str,
    ) -> None:
        if table not in {"platform_drafts", "platform_drafts_v4"}:
            raise ValueError(table)
        conn.execute(
            f"""
            CREATE TABLE "{table}" (
                draft_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'claimed',
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL DEFAULT '',
                category_path TEXT NOT NULL DEFAULT '',
                attributes_json TEXT NOT NULL DEFAULT '{{}}',
                price_json TEXT NOT NULL DEFAULT '{{}}',
                draft_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _insert_v4_draft(
        conn: sqlite3.Connection,
        table: str,
        *,
        draft_id: str,
        product_id: str,
    ) -> None:
        if table not in {"platform_drafts", "platform_drafts_v4"}:
            raise ValueError(table)
        conn.execute(
            f"""
            INSERT INTO "{table}" VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                draft_id,
                product_id,
                "mercadolibre",
                "MLM",
                "copy_ready",
                "迁移草稿",
                "迁移描述",
                "MLM-LEGACY",
                "Root / Legacy",
                json.dumps({"BRAND": "Legacy"}),
                json.dumps({"price": "20"}),
                json.dumps({"bullets": ["A"]}),
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )

    def _downgrade_drafts_to_v4(
        self,
        db_path: Path,
        product_id: str,
    ) -> str:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "ALTER TABLE platform_drafts RENAME TO drafts_v5_seed"
            )
            self._create_v4_draft_table(conn, "platform_drafts")
            draft_id = str(
                conn.execute(
                    "SELECT draft_id FROM drafts_v5_seed LIMIT 1"
                ).fetchone()[0]
            )
            self._insert_v4_draft(
                conn,
                "platform_drafts",
                draft_id=draft_id,
                product_id=product_id,
            )
            conn.execute("DROP TABLE drafts_v5_seed")
            stored_product = json.loads(
                conn.execute(
                    """
                    SELECT product_json FROM products
                    WHERE product_id = ?
                    """,
                    (product_id,),
                ).fetchone()[0]
            )
            stored_product["drafts"] = {
                "mercadolibre": {"title": "重复草稿"}
            }
            stored_product["publish_logs"] = [{"status": "legacy"}]
            conn.execute(
                """
                UPDATE products SET product_json = ?
                WHERE product_id = ?
                """,
                (json.dumps(stored_product), product_id),
            )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            return draft_id
        finally:
            conn.close()

    def test_constructor_creates_required_tables_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)

            db = self._db(app_dir)

            self.assertTrue(db.db_path.exists())
            conn = sqlite3.connect(db.db_path)
            try:
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            finally:
                conn.close()
            self.assertTrue(set(erp_db.REQUIRED_TABLES).issubset(table_names))
            self.assertEqual(version, erp_db.SCHEMA_VERSION)
            if os.name != "nt":
                self.assertEqual(
                    db.db_path.stat().st_mode & 0o777,
                    0o600,
                )
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(f"{db.db_path}{suffix}")
                    if sidecar.exists():
                        self.assertEqual(
                            sidecar.stat().st_mode & 0o777,
                            0o600,
                        )

    def test_future_schema_is_rejected_without_any_file_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE future_sentinel (value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO future_sentinel VALUES ('preserve-me')"
                )
                conn.execute(
                    f"PRAGMA user_version = {erp_db.SCHEMA_VERSION + 1}"
                )
                conn.execute("PRAGMA journal_mode = DELETE")
                conn.commit()
            finally:
                conn.close()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            sidecars_before = {
                suffix: Path(f"{db_path}{suffix}").exists()
                for suffix in ("-wal", "-shm")
            }

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
            conn = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                journal_mode = conn.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
                value = conn.execute(
                    "SELECT value FROM future_sentinel"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(after, before)
            self.assertEqual(version, erp_db.SCHEMA_VERSION + 1)
            self.assertEqual(journal_mode, "delete")
            self.assertEqual(value, "preserve-me")
            self.assertEqual(
                {
                    suffix: Path(f"{db_path}{suffix}").exists()
                    for suffix in ("-wal", "-shm")
                },
                sidecars_before,
            )

    def test_unknown_schema_version_is_rejected_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            conn = sqlite3.connect(db_path)
            try:
                # 旧世代残留：老结构表 + 已废弃表，user_version=0。
                conn.execute("CREATE TABLE products (legacy_only TEXT)")
                conn.execute("INSERT INTO products (legacy_only) VALUES ('x')")
                conn.execute("CREATE TABLE category_cache (k TEXT)")
                conn.execute("CREATE TABLE draft_id_aliases (legacy_draft_id TEXT)")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            conn = sqlite3.connect(db_path)
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
                count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            finally:
                conn.close()
            self.assertIn("category_cache", tables)
            self.assertIn("draft_id_aliases", tables)
            self.assertEqual(version, 0)
            self.assertEqual(columns, {"legacy_only"})
            self.assertEqual(count, 1)

    def test_schema_v4_is_rejected_without_mutating_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            database = self._db(app_dir)
            product_id = database.upsert_product_model(sample_product())
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            self._downgrade_drafts_to_v4(db_path, product_id)
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            self.assertEqual(
                hashlib.sha256(db_path.read_bytes()).hexdigest(),
                before,
            )
            conn = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                stored_draft = conn.execute(
                    """
                    SELECT title, category_id, attributes_json, price_json
                    FROM platform_drafts
                    """
                ).fetchone()
                stored_product = json.loads(
                    conn.execute(
                        "SELECT product_json FROM products WHERE product_id = ?",
                        (product_id,),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
            self.assertEqual(version, 4)
            self.assertEqual(stored_draft[0], "迁移草稿")
            self.assertEqual(stored_draft[1], "MLM-LEGACY")
            self.assertEqual(
                json.loads(stored_draft[2]),
                {"BRAND": "Legacy"},
            )
            self.assertEqual(json.loads(stored_draft[3]), {"price": "20"})
            self.assertIn("drafts", stored_product)
            self.assertIn("publish_logs", stored_product)

    def test_schema_v4_rejection_does_not_change_journal_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            database = self._db(app_dir)
            product_id = database.upsert_product_model(sample_product())
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            self._downgrade_drafts_to_v4(db_path, product_id)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA journal_mode = DELETE")
                conn.commit()
            finally:
                conn.close()
            sidecars_before = {
                suffix: Path(f"{db_path}{suffix}").exists()
                for suffix in ("-wal", "-shm")
            }

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            conn = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                journal_mode = conn.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(journal_mode, "delete")
            self.assertEqual(
                {
                    suffix: Path(f"{db_path}{suffix}").exists()
                    for suffix in ("-wal", "-shm")
                },
                sidecars_before,
            )

    def test_schema_v4_marker_with_current_tables_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            database = self._db(app_dir)
            product_id = database.upsert_product_model(sample_product())
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA user_version = 4")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            conn = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                product_count = conn.execute(
                    "SELECT COUNT(*) FROM products WHERE product_id = ?",
                    (product_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(version, 4)
            self.assertEqual(product_count, 1)

    def test_current_version_with_half_migration_table_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            database = self._db(app_dir)
            product_id = database.upsert_product_model(sample_product())
            db_path = app_dir / erp_db.DEFAULT_DB_NAME
            conn = sqlite3.connect(db_path)
            try:
                self._create_v4_draft_table(
                    conn,
                    "platform_drafts_v4",
                )
                self._insert_v4_draft(
                    conn,
                    "platform_drafts_v4",
                    draft_id="d_backup",
                    product_id=product_id,
                )
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "仅接受空库或当前完整 schema",
            ):
                self._db(app_dir)

            conn = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                backup_count = conn.execute(
                    "SELECT COUNT(*) FROM platform_drafts_v4"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertIn("platform_drafts_v4", tables)
            self.assertEqual(backup_count, 1)

    def test_upsert_product_model_writes_product_drafts_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            db = self._db(app_dir)
            product = sample_product()

            product_id = db.upsert_product_model(product)

            loaded = db.load_product_model(product_id)
            self.assertEqual(loaded["name"], "Imported title")
            self.assertEqual(loaded["drafts"]["mercadolibre"]["title"], "Titulo MX")
            records = db.list_product_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["product_id"], product_id)
            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                draft_count = conn.execute("SELECT COUNT(*) FROM platform_drafts").fetchone()[0]
                media_count = conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(draft_count, 1)
            self.assertEqual(media_count, 1)

    def test_new_draft_ids_are_opaque_and_platform_comes_from_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            product_id = db.upsert_product_model(sample_product())

            draft_id = db.upsert_draft_model(product_id, "ozon", {"title": "Ozon", "status": "claimed"})

            self.assertTrue(draft_id.startswith("d"))
            self.assertEqual(len(draft_id), 13)
            self.assertNotIn("ozon", draft_id)
            self.assertEqual(db.load_draft_model(draft_id)["platform"], "ozon")

    def test_draft_persistence_rejects_non_canonical_root_fields(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            product_id = db.upsert_product_model(sample_product())

            with self.assertRaisesRegex(
                ValueError,
                "非 canonical 根字段",
            ):
                db.upsert_draft_model(
                    product_id,
                    "mercadolibre",
                    {
                        "sale_price": "29.90",
                        "future_draft_field": "drop-me",
                    },
                )
            with self.assertRaisesRegex(
                ValueError,
                "已删除的历史字段",
            ):
                db.upsert_draft_model(
                    product_id,
                    "mercadolibre",
                    {"barcode": "legacy-barcode"},
                )

            draft_id = db.upsert_draft_model(
                product_id,
                "mercadolibre",
                {
                    "price": "29.90",
                    "publish_status": "ready",
                    "target_sites": [
                        {
                            "platform": "mercadolibre",
                            "site": "MLM",
                            "category_id": "MLM-1",
                        }
                    ],
                },
            )

            loaded = db.load_draft_model(draft_id)
            self.assertEqual(loaded["price"], "29.90")
            self.assertEqual(loaded["publish_status"], "ready")
            target = loaded["target_sites"][0]
            self.assertEqual(target["category_id"], "MLM-1")
            with db._connect() as conn:
                stored = json.loads(
                    conn.execute(
                        """
                        SELECT draft_json FROM platform_drafts
                        WHERE draft_id = ?
                        """,
                        (draft_id,),
                    ).fetchone()["draft_json"]
                )
            self.assertNotIn("draft_id", stored)
            self.assertNotIn("product_id", stored)
            self.assertNotIn("platform", stored)
            self.assertNotIn("status", stored)

    def test_delete_product_model_cascades_drafts_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            db = self._db(app_dir)
            product_id = db.upsert_product_model(sample_product())

            deleted = db.delete_product_model(product_id)

            self.assertTrue(deleted)
            self.assertEqual(db.load_product_model(product_id), {})
            self.assertEqual(db.list_product_records(), [])
            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                draft_count = conn.execute("SELECT COUNT(*) FROM platform_drafts").fetchone()[0]
                media_count = conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(draft_count, 0)
            self.assertEqual(media_count, 0)

    def test_delete_draft_model_removes_single_draft_without_product_or_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            db = self._db(app_dir)
            product_id = db.upsert_product_model(sample_product())
            draft_id = db.list_draft_records()[0]["draft_id"]

            deleted = db.delete_draft_model(draft_id)

            self.assertTrue(deleted)
            self.assertEqual(db.list_draft_records(), [])
            loaded = db.load_product_model(product_id)
            self.assertEqual(loaded["product_id"], product_id)
            self.assertNotIn("mercadolibre", loaded.get("drafts", {}))
            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                media_count = conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(media_count, 1)

    def test_store_auth_roundtrip_and_preserve_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))

            db.update_store_auth(
                "mercadolibre",
                credentials={"access_token": "tok-1", "refresh_token": "ref-1", "app_id": "app-1"},
                auth_status="测试成功",
                auth_detail={"shop_name": "Demo Shop", "auth_error_code": ""},
                checked_at="2026-07-26T00:00:00Z",
            )
            # 空值不得清掉已存秘密（merge 模式）。
            db.update_store_auth("mercadolibre", credentials={"access_token": "", "app_id": "app-2"})

            record = db.get_store_auth("mercadolibre")
            self.assertEqual(record["credentials"]["access_token"], "tok-1")
            self.assertEqual(record["credentials"]["app_id"], "app-2")
            self.assertEqual(record["auth_status"], "测试成功")
            self.assertEqual(record["auth_detail"]["shop_name"], "Demo Shop")
            self.assertEqual(record["checked_at"], "2026-07-26T00:00:00Z")

            # replace 模式：整体替换（用于成功换 token 后丢弃一次性 code_verifier）。
            db.update_store_auth(
                "mercadolibre",
                credentials={"access_token": "tok-2", "refresh_token": "ref-2"},
                replace_credentials=True,
            )
            replaced = db.get_store_auth("mercadolibre")["credentials"]
            self.assertEqual(replaced, {"access_token": "tok-2", "refresh_token": "ref-2"})

    def test_publish_logs_insert_and_query_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            for index in range(3):
                db.insert_publish_log(
                    {
                        "job_id": f"job-{index}",
                        "platform": "mercadolibre",
                        "product_id": "p1",
                        "status": "published",
                        "error_message": "",
                        "response_body_path": f"/tmp/resp-{index}.json",
                    }
                )

            logs = db.list_publish_logs(limit=2)

            self.assertEqual(len(logs), 2)
            self.assertEqual(logs[0]["job_id"], "job-2")
            self.assertEqual(logs[1]["job_id"], "job-1")
            self.assertTrue(db.publish_log_exists("job-0", "mercadolibre"))
            self.assertFalse(db.publish_log_exists("job-0", "ozon"))
            self.assertFalse(db.publish_log_exists("missing", "mercadolibre"))

    def test_publish_terminal_log_insert_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            entry = {
                "job_id": "job-once",
                "platform": "mercadolibre",
                "product_id": "product-1",
                "draft_id": "draft-1",
                "status": "published",
            }
            results: list[int] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def worker() -> None:
                try:
                    barrier.wait(timeout=5)
                    results.append(
                        db.insert_publish_log_once(entry)
                    )
                except BaseException as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker)
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(
                sum(result > 0 for result in results),
                1,
            )
            logs = db.list_publish_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["job_id"], "job-once")

    def test_assign_upc_concurrent_threads_get_distinct_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            db.import_upcs(["725272000007", "725272000014"])
            results: list[str] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def worker() -> None:
                try:
                    barrier.wait(timeout=5)
                    results.append(db.assign_upc("prod-1"))
                except BaseException as exc:  # noqa: BLE001 - surface in assertion
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertTrue(all(results))
            self.assertEqual(len(set(results)), 2, f"duplicate UPC assigned: {results}")
            self.assertEqual(db.upc_pool_stats(), {"total": 2, "free": 0, "used": 2})
            self.assertEqual(db.assign_upc("prod-2"), "")

    def test_assign_upc_and_product_save_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            db.import_upcs(["725272000021"])
            product = sample_product()

            with mock.patch.object(
                db,
                "_upsert_product_model_in_connection",
                side_effect=RuntimeError("injected product save failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected product save failure",
                ):
                    db.assign_upc_to_product_model(product)

            self.assertEqual(
                db.upc_pool_stats(),
                {"total": 1, "free": 1, "used": 0},
            )
            self.assertEqual(db.list_product_records(), [])

            upc, product_id = db.assign_upc_to_product_model(product)
            self.assertEqual(upc, "725272000021")
            saved = db.load_product_model(product_id)
            self.assertEqual(saved["upc"], upc)
            self.assertTrue(
                all(
                    draft["upc"] == upc
                    for draft in saved["drafts"].values()
                )
            )
            self.assertEqual(
                db.upc_pool_stats(),
                {"total": 1, "free": 0, "used": 1},
            )

    def test_upc_pool_seeds_once_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            (app_dir / "upc_pool.json").write_text(
                json.dumps({"values": ["100000000001", "100000000002"], "used": ["100000000001"]}),
                encoding="utf-8",
            )

            db = self._db(app_dir)

            self.assertEqual(db.upc_pool_stats(), {"total": 2, "free": 1, "used": 1})
            self.assertEqual(db.assign_upc("prod-1"), "100000000002")
            # 已导入过则不再重复 seed。
            db._maybe_seed_upc_pool()
            self.assertEqual(db.upc_pool_stats(), {"total": 2, "free": 0, "used": 2})

    def test_order_notifications_insert_and_list_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            for index in range(3):
                db.insert_order_notification(
                    {"topic": "orders_v2", "resource": f"/orders/{index}", "order_id": str(index)}
                )

            items = db.list_order_notifications(limit=2)

            self.assertEqual([item["order_id"] for item in items], ["2", "1"])
            self.assertEqual(items[0]["resource"], "/orders/2")

    def test_publish_jobs_roundtrip_and_pending_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp))
            running = {
                "job_id": "job-running",
                "status": "running",
                "created_at": "2026-07-26 09:00:00",
                "updated_at": "2026-07-26 09:00:01",
                "product": {"product_id": "p1"},
                "platforms": {"mercadolibre": {"status": "running", "stage": "publishing", "attempts": 1, "error": ""}},
            }
            done = {
                "job_id": "job-done",
                "status": "completed",
                "created_at": "2026-07-26 08:00:00",
                "updated_at": "2026-07-26 08:00:05",
                "product": {"product_id": "p2"},
                "platforms": {"ozon": {"status": "success", "stage": "finished", "attempts": 1, "error": ""}},
            }
            persisted = {
                **done,
                "job_id": "job-persisted",
                "terminal_results_persisted": True,
            }
            db.save_publish_job(running)
            db.save_publish_job(done)
            db.save_publish_job(persisted)

            self.assertEqual(db.load_publish_job("job-running")["status"], "running")
            self.assertEqual(db.load_publish_job("missing"), {})
            pending_ids = [state["job_id"] for state in db.list_pending_publish_jobs()]
            # 旧任务没有 terminal_results_persisted 字段时，按未补偿处理；
            # 只有显式持久化成功的 completed 任务才会退出恢复集合。
            self.assertEqual(pending_ids, ["job-done", "job-running"])
            # job 状态里不得再存 config/凭据。
            self.assertNotIn("config", db.load_publish_job("job-running"))

            first_page, next_cursor = db.list_publish_jobs(limit=2)
            self.assertEqual(
                [state["job_id"] for state in first_page],
                ["job-running", "job-persisted"],
            )
            self.assertEqual(next_cursor, "job-persisted")

            second_page, next_cursor = db.list_publish_jobs(
                limit=2,
                cursor=next_cursor,
            )
            self.assertEqual([state["job_id"] for state in second_page], ["job-done"])
            self.assertEqual(next_cursor, "")

            ozon_jobs, _ = db.list_publish_jobs(platform="ozon")
            self.assertEqual(
                [state["job_id"] for state in ozon_jobs],
                ["job-persisted", "job-done"],
            )
            product_jobs, _ = db.list_publish_jobs(product_id="p1")
            self.assertEqual([state["job_id"] for state in product_jobs], ["job-running"])


if __name__ == "__main__":
    unittest.main()
