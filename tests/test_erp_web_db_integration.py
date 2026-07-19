from __future__ import annotations

import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from erp_web import db as erp_db
from erp_web import runtime as erp_web_app
from erp_web.http_handler import Handler
from erp_web.http_route_units import image_routes
from erp_web.services import image_translate_service
from tests.test_erp_db import sample_product


class ErpWebDbIntegrationTests(unittest.TestCase):
    def with_temp_app(self, callback) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            output_dir = app_dir / "output"
            original_globals = {
                "APP_DIR": erp_web_app.APP_DIR,
                "DIST_DIR": erp_web_app.DIST_DIR,
                "CONFIG_DIR": erp_web_app.CONFIG_DIR,
                "OUTPUT_DIR": erp_web_app.OUTPUT_DIR,
                "PUBLISH_LOG_PATH": erp_web_app.PUBLISH_LOG_PATH,
                "STORE_CONFIG_PATH": erp_web_app.STORE_CONFIG_PATH,
                "APP_CONFIG_PATH": erp_web_app.APP_CONFIG_PATH,
                "LEGACY_STORE_CONFIG_PATHS": erp_web_app.LEGACY_STORE_CONFIG_PATHS,
                "LEGACY_APP_CONFIG_PATHS": erp_web_app.LEGACY_APP_CONFIG_PATHS,
            }
            try:
                erp_web_app.APP_DIR = app_dir
                erp_web_app.DIST_DIR = app_dir / "dist"
                erp_web_app.CONFIG_DIR = app_dir / "config"
                erp_web_app.OUTPUT_DIR = output_dir
                erp_web_app.PUBLISH_LOG_PATH = output_dir / "publish_logs.json"
                erp_web_app.STORE_CONFIG_PATH = erp_web_app.CONFIG_DIR / "store_config.json"
                erp_web_app.APP_CONFIG_PATH = erp_web_app.CONFIG_DIR / "app_config.json"
                erp_web_app.LEGACY_STORE_CONFIG_PATHS = (app_dir / "store_config.json", app_dir / "dist" / "store_config.json")
                erp_web_app.LEGACY_APP_CONFIG_PATHS = (app_dir / "app_config.json", app_dir / "dist" / "app_config.json")
                callback(app_dir)
            finally:
                for name, value in original_globals.items():
                    setattr(erp_web_app, name, value)

    def test_save_product_uses_sqlite_as_primary_product_index(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product())

            db_records = erp_db.list_product_records(app_dir)
            self.assertEqual(len(db_records), 1)
            self.assertEqual(db_records[0]["product_id"], saved["product_id"])
            self.assertFalse((app_dir / "product.json").exists())
            index_records = erp_web_app.load_products_index()
            self.assertEqual(index_records[0]["product_id"], saved["product_id"])
            self.assertTrue(index_records[0]["product_file_path"].startswith("sqlite://"))
            loaded = erp_web_app.load_product_from_index(saved["product_id"], "")
            self.assertEqual(loaded["product_id"], saved["product_id"])
            self.assertEqual(loaded["name"], "Imported title")

        self.with_temp_app(run)

    def test_delete_products_from_index_removes_selected_sqlite_products(self) -> None:
        def run(app_dir: Path) -> None:
            first = erp_web_app.save_product(sample_product("Delete me", "https://example.com/delete-me"))
            second = erp_web_app.save_product(sample_product("Keep me", "https://example.com/keep-me"))

            result = erp_web_app.delete_products_from_index([first["product_id"]])

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 1)
            self.assertEqual(result["deletedIds"], [first["product_id"]])
            remaining = erp_db.list_product_records(app_dir)
            self.assertEqual([item["product_id"] for item in remaining], [second["product_id"]])
            self.assertEqual(result["productsIndex"][0]["product_id"], second["product_id"])
            self.assertEqual(erp_db.load_product_model(app_dir, first["product_id"]), {})

        self.with_temp_app(run)

    def test_delete_draft_from_index_removes_only_selected_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product("Draft delete", "https://example.com/draft-delete"))
            draft_id = erp_db.list_draft_records(app_dir)[0]["draft_id"]

            result = erp_web_app.delete_draft_from_index(draft_id)

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 1)
            self.assertEqual(result["deletedDraftId"], draft_id)
            self.assertEqual(result["draftsIndex"], [])
            self.assertEqual(result["product"]["product_id"], saved["product_id"])
            self.assertEqual(erp_db.list_product_records(app_dir)[0]["product_id"], saved["product_id"])
            self.assertEqual(erp_db.list_draft_records(app_dir), [])

        self.with_temp_app(run)

    def test_delete_draft_from_index_accepts_draft_id_list(self) -> None:
        def run(app_dir: Path) -> None:
            first = erp_web_app.save_product(sample_product("Draft delete 1", "https://example.com/draft-delete-1"))
            second = erp_web_app.save_product(sample_product("Draft delete 2", "https://example.com/draft-delete-2"))
            draft_ids = [item["draft_id"] for item in erp_db.list_draft_records(app_dir)]

            result = erp_web_app.delete_draft_from_index(draft_ids)

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 2)
            self.assertEqual(result["deletedDraftIds"], draft_ids)
            self.assertEqual(result["deletedIds"], draft_ids)
            self.assertEqual(result["missingIds"], [])
            self.assertEqual(result["draftsIndex"], [])
            self.assertEqual(sorted(result["affectedProductIds"]), sorted([first["product_id"], second["product_id"]]))
            self.assertEqual(len(erp_db.list_product_records(app_dir)), 2)
            self.assertEqual(erp_db.list_draft_records(app_dir), [])

        self.with_temp_app(run)

    def test_save_product_profile_does_not_overwrite_platform_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product("Profile boundary", "https://example.com/profile-boundary"))
            draft = erp_db.list_draft_records(app_dir)[0]

            profile = dict(saved)
            profile["name"] = "Profile boundary updated"
            profile["drafts"] = {
                "mercadolibre": {
                    "draft_id": draft["draft_id"],
                    "title": "Should not overwrite draft",
                    "description": "Should not overwrite draft description",
                }
            }
            updated = erp_web_app.save_product_profile(profile)

            reloaded_draft = erp_db.load_draft_model(app_dir, draft["draft_id"])
            self.assertEqual(updated["name"], "Profile boundary updated")
            self.assertEqual(reloaded_draft["title"], "Titulo MX")
            self.assertEqual(reloaded_draft["description"], "Descripcion MX")

        self.with_temp_app(run)

    def test_save_draft_detail_updates_only_selected_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product("Draft boundary", "https://example.com/draft-boundary"))
            product_id = saved["product_id"]
            ozon_draft_id = erp_db.upsert_draft_model(
                app_dir,
                product_id,
                "ozon",
                {
                    "title": "Ozon original",
                    "description": "Ozon description",
                    "price": "22",
                    "status": "copy_ready",
                },
            )
            yandex_draft_id = erp_db.upsert_draft_model(
                app_dir,
                product_id,
                "yandex",
                {
                    "title": "Yandex original",
                    "description": "Yandex description",
                    "price": "21",
                    "status": "copy_ready",
                },
            )

            result, error, status = erp_web_app.save_draft_detail(
                {
                    "draft_id": yandex_draft_id,
                    "title": "Yandex independent title",
                    "description": "Yandex independent description",
                    "price": "33",
                    "status": "copy_ready",
                    "language": "ru-RU",
                    "target_sites": [
                        {"platform": "yandex", "site": "global"},
                        {"platform": "ozon", "site": "global"},
                    ],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(result["draft"]["title"], "Yandex independent title")
            self.assertEqual(result["draft"]["platforms"], ["yandex", "ozon"])
            self.assertEqual(erp_db.load_draft_model(app_dir, yandex_draft_id)["title"], "Yandex independent title")
            self.assertEqual(erp_db.load_draft_model(app_dir, yandex_draft_id)["platforms"], ["yandex", "ozon"])
            updated_record = next(item for item in erp_db.list_draft_records(app_dir, scope="all") if item["draft_id"] == yandex_draft_id)
            self.assertEqual(updated_record["platforms"], ["yandex", "ozon"])
            self.assertEqual(erp_db.load_draft_model(app_dir, ozon_draft_id)["title"], "Ozon original")

        self.with_temp_app(run)

    def test_same_product_drafts_keep_separate_platform_selections(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product("Two draft copies", "https://example.com/two-drafts"))
            first_result = erp_web_app.claim_products_to_platforms([saved["product_id"]], ["yandex"])
            second_result = erp_web_app.claim_products_to_platforms([saved["product_id"]], ["yandex"])
            first_draft_id = first_result["items"][0]["draft_ids"][0]
            second_draft_id = second_result["items"][0]["draft_ids"][0]
            draft_ids_before_update = [item["draft_id"] for item in erp_db.list_draft_records(app_dir, scope="all")]

            self.assertNotEqual(first_draft_id, second_draft_id)
            result, error, status = erp_web_app.save_draft_detail(
                {
                    "draft_id": second_draft_id,
                    "language": "ru-RU",
                    "target_sites": [
                        {"platform": "yandex", "site": "global"},
                        {"platform": "ozon", "site": "global"},
                    ],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(result["draft"]["draft_id"], second_draft_id)
            self.assertEqual(erp_db.load_draft_model(app_dir, first_draft_id)["platforms"], ["yandex"])
            self.assertEqual(erp_db.load_draft_model(app_dir, second_draft_id)["platforms"], ["yandex", "ozon"])
            self.assertEqual(
                [item["draft_id"] for item in erp_db.list_draft_records(app_dir, scope="all")],
                draft_ids_before_update,
            )

        self.with_temp_app(run)

    def test_1688_collect_images_are_limited_to_first_five(self) -> None:
        source = {
            "images": [f"https://img.example/{index}.jpg" for index in range(8)],
        }

        normalized = erp_web_app.normalize_collect_source_images(source, "1688", "http", ["mercadolibre"])

        self.assertEqual(len(normalized["image_pool"]), 5)
        self.assertEqual(normalized["images"], [f"https://img.example/{index}.jpg" for index in range(5)])

    def test_failed_new_collect_creates_failed_sqlite_record(self) -> None:
        def run(app_dir: Path) -> None:
            url = "https://detail.1688.com/offer/123456.html"
            with patch.object(erp_web_app, "fetch_page_html_with_status", return_value=("", "")):
                result = erp_web_app.collect_source_product(url, mode="http", platform="1688", claim_platforms=["mercadolibre"])

            self.assertFalse(result["ok"])
            records = erp_db.list_product_records(app_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source_url"], url)
            self.assertEqual(records[0]["collect_status"], "failed")
            loaded = erp_db.load_product_model(app_dir, records[0]["product_id"])
            self.assertEqual(loaded["source"]["collect_diagnostics"]["error_code"], "1688_SELECTOR_FAILED")

        self.with_temp_app(run)

    def test_collect_batch_products_returns_each_row_and_saves_successes(self) -> None:
        def run(app_dir: Path) -> None:
            calls: list[str] = []

            def fake_collect(url: str, mode: str = "browser", cookie: str | None = None, platform: str | None = None, claim_platforms: list[str] | None = None) -> dict:
                calls.append(url)
                if "bad" in url:
                    raise RuntimeError("blocked")
                product = sample_product()
                product["source"]["source_url"] = url
                product["source"]["source_platform"] = platform or "1688"
                saved = erp_web_app.save_product(product)
                return {"ok": True, "product": saved, "diagnostics": {"success": True, "error_code": ""}}

            with patch.object(erp_web_app, "collect_source_product", side_effect=fake_collect):
                result = erp_web_app.collect_batch_products(
                    "https://detail.1688.com/offer/1.html\nhttps://detail.1688.com/offer/bad.html",
                    mode="http",
                    platforms=["mercadolibre"],
                )

            self.assertEqual(calls, ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/bad.html"])
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["success_count"], 1)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual([row["status"] for row in result["items"]], ["success", "failed"])
            self.assertEqual(len(erp_db.list_product_records(app_dir)), 1)

        self.with_temp_app(run)

    def test_workflow_status_moves_from_claimed_to_ready_to_publish(self) -> None:
        product = sample_product()
        product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "Source placeholder",
            "description": "",
            "images": [],
            "category_id": "",
            "attributes": {},
            "price": "",
            "status": "claimed",
        }
        self.assertEqual(erp_web_app.draft_workflow_status(product, "mercadolibre"), "claimed")

        product["drafts"]["mercadolibre"].update(
            {
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
            }
        )
        self.assertEqual(erp_web_app.draft_workflow_status(product, "mercadolibre"), "copy_ready")

        product["drafts"]["mercadolibre"]["images"] = [{"asset_id": "img_1", "role": "main", "order": 0}]
        self.assertEqual(erp_web_app.draft_workflow_status(product, "mercadolibre"), "images_ready")

        product["drafts"]["mercadolibre"].update(
            {
                "category_id": "MLM123",
                "attributes": {"BRAND": "BrandX"},
                "price": "19.99",
                "stock": "5",
            }
        )
        self.assertEqual(erp_web_app.draft_workflow_status(product, "mercadolibre"), "images_ready")

        product = erp_web_app.apply_precheck_to_product(
            product,
            "mercadolibre",
            {"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T10:00:00"},
            status="ready",
        )
        self.assertEqual(erp_web_app.draft_workflow_status(product, "mercadolibre"), "ready_to_publish")

    def test_claim_products_to_platforms_creates_claimed_drafts_in_sqlite(self) -> None:
        def run(app_dir: Path) -> None:
            saved = erp_web_app.save_product(sample_product())

            result = erp_web_app.claim_products_to_platforms([saved["product_id"]], ["mercadolibre", "ozon"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["claimed_count"], 1)
            created_drafts = [erp_db.load_draft_model(app_dir, draft_id) for draft_id in result["items"][0]["draft_ids"]]
            self.assertTrue(all(draft["source_product_id"] == saved["product_id"] for draft in created_drafts))
            self.assertTrue(any(draft["title"] == saved["source"]["title"] for draft in created_drafts))
            self.assertTrue(any(draft["images"] for draft in created_drafts))
            loaded = erp_db.load_product_model(app_dir, saved["product_id"])
            self.assertEqual(loaded["drafts"]["mercadolibre"]["status"], "claimed")
            self.assertEqual(loaded["drafts"]["ozon"]["status"], "claimed")
            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                rows = conn.execute(
                    "SELECT platform, status FROM platform_drafts WHERE product_id = ? ORDER BY platform",
                    (saved["product_id"],),
                ).fetchall()
            finally:
                conn.close()
            self.assertIn(("mercadolibre", "claimed"), rows)
            self.assertIn(("ozon", "claimed"), rows)

        self.with_temp_app(run)

    def test_batch_generate_copy_updates_selected_products_to_copy_ready(self) -> None:
        def run(app_dir: Path) -> None:
            first = sample_product("First collected", "https://example.com/first")
            second = sample_product("Second collected", "https://example.com/second")
            for product in (first, second):
                product["source"]["image_pool"] = []
                product["source"]["images"] = []
                product["source_images"] = []
                product["source_image_urls"] = []
                product["drafts"]["mercadolibre"] = {
                    "enabled": True,
                    "title": product["name"],
                    "description": "",
                    "images": [],
                    "status": "claimed",
                }
            first_saved = erp_web_app.save_product(first)
            second_saved = erp_web_app.save_product(second)

            def fake_bundle(product: dict, source_platform: str, target_market: str, language: str, mode: str, app_cfg: dict) -> dict:
                return {
                    "ok": True,
                    "source_platform": source_platform,
                    "target_market": target_market,
                    "language": language,
                    "mode": mode,
                    "copy": {
                        "title": f"AI {product['source']['title']}",
                        "description": "AI description",
                        "bullets": ["A", "B"],
                        "search_keywords": ["keyword"],
                    },
                    "warning": "",
                }

            with patch.object(erp_web_app, "generate_ai_copy_bundle", side_effect=fake_bundle):
                result = erp_web_app.batch_generate_copy_for_products(
                    [first_saved["product_id"], second_saved["product_id"]],
                    platform="mercadolibre",
                    language="Spanish",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["success_count"], 2)
            loaded_first = erp_db.load_product_model(app_dir, first_saved["product_id"])
            loaded_second = erp_db.load_product_model(app_dir, second_saved["product_id"])
            self.assertEqual(loaded_first["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(loaded_second["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(loaded_first["drafts"]["mercadolibre"]["title"], "AI First collected")
            records = erp_db.list_product_records(app_dir)
            self.assertEqual({record["workflow_status"] for record in records}, {"copy_ready"})

        self.with_temp_app(run)

    def test_save_image_pool_changes_persists_media_without_touching_draft_images(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Image ready item", "https://example.com/image-ready")
            product["source"]["image_pool"] = []
            product["source"]["images"] = []
            product["source_images"] = []
            product["source_image_urls"] = []
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
                "images": [],
                "status": "copy_ready",
            }
            saved = erp_web_app.save_product(product)

            result = erp_web_app.save_image_pool_for_product(
                saved["product_id"],
                [
                    {
                        "id": "ai_1",
                        "url": "https://example.com/ai-image.jpg",
                        "preview_url": "https://example.com/ai-image.jpg",
                        "origin": "ai_generated",
                        "usage": "main",
                        "platforms": ["mercadolibre"],
                        "is_main": True,
                        "selected": True,
                        "order": 0,
                        "status": "ready",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            loaded = erp_db.load_product_model(app_dir, saved["product_id"])
            self.assertEqual(loaded["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(loaded["drafts"]["mercadolibre"]["images"], [])
            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                media_count = conn.execute(
                    "SELECT COUNT(*) FROM media_assets WHERE product_id = ?",
                    (saved["product_id"],),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(media_count, 1)
            records = erp_db.list_product_records(app_dir)
            self.assertEqual(records[0]["workflow_status"], "copy_ready")

        self.with_temp_app(run)

    def test_image_translate_items_persist_without_touching_draft_images(self) -> None:
        def run(app_dir: Path) -> None:
            source_image = app_dir / "source.png"
            translated_image = app_dir / "translated.png"
            from PIL import Image

            Image.new("RGB", (8, 6), (255, 0, 0)).save(source_image, format="PNG")
            Image.new("RGB", (8, 6), (0, 255, 0)).save(translated_image, format="PNG")

            product = sample_product("Translate image item", "https://example.com/translate-image")
            product["source"]["image_pool"] = erp_web_app.image_service.upload_images(
                app_dir,
                [{"path": str(source_image), "platforms": ["mercadolibre"], "is_main": True, "selected": True}],
                "translate-image-item",
            )
            product["source"]["images"] = []
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
                "images": [],
                "status": "copy_ready",
            }
            saved = erp_web_app.save_product(product)
            source_item_id = saved["source"]["image_pool"][0]["id"]

            result = image_translate_service.translate_images(
                app_dir,
                saved,
                {
                    "ai_models": [
                        {
                            "id": "image_model",
                            "provider": "OpenAI",
                            "api_key": "test-key",
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-image-1",
                            "capabilities": ["image_edit", "image_generate"],
                        }
                    ]
                },
                target_language="Spanish (Mexico)",
                platform="mercadolibre",
                image_ids=[source_item_id],
                provider=lambda _config, _request: [{"path": str(translated_image), "provider": "fake-image-ai"}],
            )
            self.assertTrue(result["ok"])

            merged = erp_web_app.append_images_to_product_pool(saved, result["imagePoolItems"])
            persisted = erp_web_app.save_product(merged)
            loaded = erp_db.load_product_model(app_dir, persisted["product_id"])
            translated_items = [item for item in loaded["source"]["image_pool"] if item.get("target_language") == "Spanish (Mexico)"]

            self.assertEqual(loaded["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(len(translated_items), 1)
            self.assertEqual(translated_items[0]["origin"], "ai_translated")
            self.assertEqual(translated_items[0]["derived_from_id"], source_item_id)
            self.assertEqual(loaded["drafts"]["mercadolibre"]["images"], [])
            self.assertTrue((app_dir / translated_items[0]["path"]).exists())

            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                translated_media_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM media_assets
                    WHERE product_id = ? AND origin = 'ai_translated' AND local_path = ?
                    """,
                    (persisted["product_id"], translated_items[0]["path"]),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(translated_media_count, 1)

        self.with_temp_app(run)

    def test_image_translate_items_can_apply_to_draft_refs(self) -> None:
        def run(app_dir: Path) -> None:
            source_image = app_dir / "source.png"
            translated_image = app_dir / "translated.png"
            from PIL import Image

            Image.new("RGB", (8, 6), (255, 0, 0)).save(source_image, format="PNG")
            Image.new("RGB", (8, 6), (0, 255, 0)).save(translated_image, format="PNG")

            product = sample_product("Translate image draft item", "https://example.com/translate-image-draft")
            product["source"]["image_pool"] = erp_web_app.image_service.upload_images(
                app_dir,
                [{"path": str(source_image), "platforms": ["mercadolibre"], "is_main": True, "selected": True}],
                "translate-image-draft-item",
            )
            source_item_id = product["source"]["image_pool"][0]["id"]
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
                "images": [{"asset_id": source_item_id, "role": "main", "order": 0}],
                "status": "images_ready",
            }
            saved = erp_web_app.save_product(product)
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]

            result = image_translate_service.translate_images(
                app_dir,
                saved,
                {
                    "ai_models": [
                        {
                            "id": "image_model",
                            "provider": "OpenAI",
                            "api_key": "test-key",
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-image-1",
                            "capabilities": ["image_edit", "image_generate"],
                        }
                    ]
                },
                target_language="Spanish (Mexico)",
                platform="mercadolibre",
                image_ids=[source_item_id],
                provider=lambda _config, _request: [{"path": str(translated_image), "provider": "fake-image-ai"}],
            )
            self.assertTrue(result["ok"])

            merged = erp_web_app.append_images_to_product_pool(saved, result["imagePoolItems"])
            persisted = erp_web_app.save_product(merged)
            draft_result, draft_error, status = erp_web_app.apply_image_assets_to_draft(
                draft_id,
                result["imagePoolItems"],
                "replace_selected",
            )

            self.assertIsNone(draft_error)
            self.assertEqual(status, 200)
            self.assertTrue(draft_result["ok"])
            loaded = erp_db.load_product_model(app_dir, persisted["product_id"])
            translated_item = next(item for item in loaded["source"]["image_pool"] if item.get("derived_from_id") == source_item_id)
            draft_images = erp_db.load_draft_model(app_dir, draft_id)["images"]
            self.assertEqual(
                draft_images,
                [{"asset_id": translated_item["id"], "role": "main", "order": 0, "source_asset_id": source_item_id}],
            )

        self.with_temp_app(run)

    def test_category_search_and_attrs_use_sqlite_cache(self) -> None:
        def run(app_dir: Path) -> None:
            erp_db.import_category_cache(
                app_dir,
                {
                    "platform": "mercadolibre",
                    "site": "MLM",
                    "updated_at": "2026-05-29T10:00:00Z",
                    "records": [
                        {
                            "platform": "mercadolibre",
                            "site": "MLM",
                            "category_id": "MLM999",
                            "name_original": "Necklaces",
                            "name_cn": "项链",
                            "path_original": ["Jewelry", "Necklaces"],
                            "path_cn": ["珠宝", "项链"],
                            "keywords": ["吊坠"],
                            "attributes_cache": {
                                "required": [{"id": "BRAND", "name": "品牌", "required": True}],
                                "optional": [],
                            },
                        }
                    ],
                },
            )

            results = erp_web_app.search_category_cache("mercadolibre", query="项链", site="MLM")
            attrs = erp_web_app.mock_category_attrs("mercadolibre", "MLM999")
            product = sample_product()
            product["drafts"]["mercadolibre"]["category_id"] = "MLM999"
            product["drafts"]["mercadolibre"]["attributes"] = {}
            summary = erp_web_app._required_attribute_summary(product, "mercadolibre")

            self.assertEqual(results[0]["category_id"], "MLM999")
            self.assertEqual(attrs["required"][0]["id"], "BRAND")
            self.assertEqual(attrs["cache_status"]["storage"], "sqlite")
            self.assertEqual(summary["required_count"], 1)
            self.assertEqual(summary["filled_count"], 0)

        self.with_temp_app(run)

    def test_apply_precheck_promotes_workflow_and_syncs_sqlite_index(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Ready draft item", "https://example.com/ready-draft")
            product["source"]["image_pool"][0]["origin"] = "ai_generated"
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-30T10:00:00",
                "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
                "category_id": "MLM123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
                "price": "19.99",
                "stock": "5",
                "pricing": {"suggested_price": "19.99"},
                "package_dimensions": {
                    "length_cm": "10",
                    "width_cm": "8",
                    "height_cm": "3",
                    "weight_kg": "0.5",
                },
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "No warranty"}],
                "shipping": {"logistic_type": "remote"},
                "upc": "123456789012",
                "status": "images_ready",
            }
            saved = erp_web_app.save_product(product)
            self.assertEqual(saved["drafts"]["mercadolibre"]["status"], "images_ready")

            prechecked = erp_web_app.apply_precheck_to_product(
                saved,
                "mercadolibre",
                {"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T10:05:00"},
                status="ready",
            )
            persisted = erp_web_app.save_product(prechecked)

            self.assertEqual(persisted["drafts"]["mercadolibre"]["status"], "ready_to_publish")
            self.assertTrue(persisted["publish_preview"]["mercadolibre"]["ok"])
            self.assertEqual(persisted["workflow_statuses"]["mercadolibre"], "ready_to_publish")

            records = erp_db.list_product_records(app_dir)
            self.assertEqual(records[0]["workflow_status"], "ready_to_publish")
            self.assertEqual(records[0]["precheck_status"], True)

        self.with_temp_app(run)

    def test_mercadolibre_publish_payload_uses_draft_fields_over_listing_defaults(self) -> None:
        product = sample_product("Draft payload source title", "https://example.com/draft-payload")
        product["source"]["image_pool"][0].update(
            {
                "url": "https://example.com/draft-main.jpg",
                "preview_url": "https://example.com/draft-main.jpg",
                "platforms": ["mercadolibre"],
                "is_main": True,
                "selected": True,
                "status": "ready",
            }
        )
        product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "Draft title for ML",
            "description": "Draft description",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "MLM455865",
            "attributes": {"BRAND": "DraftBrand", "MODEL": "DraftModel", "MATERIAL": "ABS"},
            "price": "9.59",
            "stock": "10",
            "sku": "DRAFT-SKU-1",
            "upc": "123456789012",
            "package_dimensions": {
                "length_cm": "11",
                "width_cm": "7",
                "height_cm": "5",
                "weight_kg": "0.35",
            },
            "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "No warranty"}],
            "shipping": {"logistic_type": "remote"},
            "status": "ready_to_publish",
        }
        config = {
            "mercadolibre": {"site_id": "MLM", "category_id": "WRONG-CATEGORY", "access_token": "token"},
            "listing": {
                "currency_id": "USD",
                "price": "0",
                "mercadolibre_price": "0",
                "stock": "0",
                "sku": "CONFIG-SKU",
            },
        }

        payload = erp_web_app.build_publish_payload(product, "mercadolibre", config)
        attributes = {item["id"]: item["value_name"] for item in payload["attributes"]}

        self.assertEqual(payload["title"], "Draft title for ML")
        self.assertEqual(payload["category_id"], "MLM455865")
        self.assertEqual(payload["price"], 9.59)
        self.assertEqual(payload["currency_id"], "USD")
        self.assertEqual(payload["available_quantity"], 10)
        self.assertEqual(payload["pictures"], [{"source": "https://example.com/draft-main.jpg"}])
        self.assertEqual(attributes["SELLER_SKU"], "DRAFT-SKU-1")
        self.assertEqual(attributes["GTIN"], "123456789012")
        self.assertEqual(attributes["BRAND"], "DraftBrand")
        self.assertEqual(attributes["MODEL"], "DraftModel")
        self.assertEqual(attributes["MATERIAL"], "ABS")
        self.assertEqual(attributes["SELLER_PACKAGE_LENGTH"], "11.0 cm")
        self.assertEqual(attributes["SELLER_PACKAGE_WEIGHT"], "350 g")
        self.assertEqual(payload["sale_terms"], [{"id": "WARRANTY_TYPE", "value_name": "Sin garantía", "value_id": "6150835"}])

    def test_claiming_published_product_creates_a_new_active_draft(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Published item", "https://example.com/published-item")
            product["drafts"]["mercadolibre"].update(
                {
                    "enabled": True,
                    "title": "Published title",
                    "description": "Published description",
                    "category_id": "MLM123",
                    "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
                    "price": "19.99",
                    "stock": "5",
                    "publish_status": "real_publish_success",
                    "status": "published",
                }
            )
            saved = erp_web_app.save_product(product)

            result = erp_web_app.claim_products_to_platforms([saved["product_id"]], ["mercadolibre"])

            self.assertTrue(result["ok"])
            all_drafts = erp_db.list_draft_records(app_dir, scope="all")
            active_drafts = erp_db.list_draft_records(app_dir)
            statuses = sorted(item["status"] for item in all_drafts if item["platform"] == "mercadolibre")
            self.assertEqual(statuses, ["claimed", "published"])
            self.assertEqual([item["status"] for item in active_drafts], ["claimed"])

        self.with_temp_app(run)

    def test_mercadolibre_remote_items_lists_seller_items(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "MLM"}
                if url == "https://api.mercadolibre.com/users/12345/items/search?limit=50&offset=0&orders=start_time_desc&status=active":
                    return {"results": ["MLM2", "MLM1"], "paging": {"total": 2}}
                if url == "https://api.mercadolibre.com/items?ids=MLM2%2CMLM1":
                    return [
                        {"code": 200, "body": {"id": "MLM1", "title": "First", "status": "active", "price": 9.59, "currency_id": "USD", "available_quantity": 10, "sold_quantity": 1, "date_created": "2026-06-10T10:00:00.000Z", "attributes": [{"id": "SELLER_SKU", "value_name": "SKU-1"}]}},
                        {"code": 200, "body": {"id": "MLM2", "title": "Second", "status": "active", "price": 12, "currency_id": "USD", "available_quantity": 3, "sold_quantity": 0, "date_created": "2026-06-12T10:00:00.000Z"}},
                    ]
                raise AssertionError(f"Unexpected request: {method} {url}")

            with (
                patch.object(erp_web_app.publisher, "fetch_mercadolibre_shop_name", return_value="shop"),
                patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request),
            ):
                result = erp_web_app.mercadolibre_remote_items("active")

            self.assertTrue(result["ok"])
            self.assertEqual([item["id"] for item in result["items"]], ["MLM2", "MLM1"])
            self.assertEqual(result["items"][1]["seller_sku"], "SKU-1")
            self.assertEqual(result["paging"]["active"]["total"], 2)
            self.assertEqual(result["pagination"]["page"], 1)
            self.assertEqual(result["pagination"]["total"], 2)

        self.with_temp_app(run)

    def test_mercadolibre_remote_items_supports_second_page(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})

            calls: list[str] = []
            ids = [f"CBT{i:02d}" for i in range(50, 54)]

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append(url)
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if url == "https://api.mercadolibre.com/users/12345/items/search?limit=50&offset=50&orders=start_time_desc&status=active":
                    return {"results": ids, "paging": {"total": 54, "limit": 100, "offset": 0}}
                if url.startswith("https://api.mercadolibre.com/items?ids="):
                    requested = urllib.parse.unquote(url.rsplit("ids=", 1)[-1]).split(",")
                    return [
                        {"code": 200, "body": {"id": item_id, "title": item_id, "status": "active", "date_created": f"2026-06-{int(item_id[-2:]) + 1:02d}T10:00:00.000Z"}}
                        for item_id in requested
                    ]
                raise AssertionError(f"Unexpected request: {method} {url}")

            with patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request):
                result = erp_web_app.mercadolibre_remote_items("active", page=2, per_page=50)

            self.assertTrue(result["ok"])
            self.assertEqual([item["id"] for item in result["items"]], ["CBT50", "CBT51", "CBT52", "CBT53"])
            self.assertEqual(result["pagination"]["page"], 2)
            self.assertEqual(result["pagination"]["offset"], 50)
            self.assertEqual(result["pagination"]["total"], 54)
            self.assertTrue(result["pagination"]["has_prev"])
            self.assertFalse(result["pagination"]["has_next"])
            self.assertIn("https://api.mercadolibre.com/users/12345/items/search?limit=50&offset=50&orders=start_time_desc&status=active", calls)
            self.assertNotIn("https://api.mercadolibre.com/users/12345/items/search?limit=50&offset=0&orders=start_time_desc&status=active", calls)

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_marks_listing_closed(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})
            calls: list[tuple[str, str, dict | list | None]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url, payload))
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "MLM"}
                return {"id": "MLM1", "title": "First", "status": "closed"}

            with (
                patch.object(erp_web_app.publisher, "fetch_mercadolibre_shop_name", return_value="shop"),
                patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request),
            ):
                result = erp_web_app.mercadolibre_close_remote_item("MLM1")

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "closed")
            self.assertEqual(calls, [
                ("GET", "https://api.mercadolibre.com/users/me", None),
                ("PUT", "https://api.mercadolibre.com/items/MLM1", {"status": "closed"}),
            ])

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_deletes_global_site_listing(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})
            calls: list[tuple[str, str, dict | list | None]] = []
            item_gets = 0

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                nonlocal item_gets
                calls.append((method, url, payload))
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if method == "GET" and url == "https://api.mercadolibre.com/items/CBT3475477379":
                    item_gets += 1
                    return {"id": "CBT3475477379", "title": "First", "status": "active" if item_gets == 1 else "paused"}
                return {}

            with patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request):
                result = erp_web_app.mercadolibre_close_remote_item("CBT3475477379")

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "paused")
            self.assertEqual(calls, [
                ("GET", "https://api.mercadolibre.com/users/me", None),
                ("GET", "https://api.mercadolibre.com/items/CBT3475477379", None),
                ("PUT", "https://api.mercadolibre.com/global/items/CBT3475477379", {"status": "paused"}),
                ("GET", "https://api.mercadolibre.com/items/CBT3475477379", None),
            ])

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_is_idempotent_for_paused_global_listing(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})
            calls: list[tuple[str, str, dict | list | None]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url, payload))
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if method == "GET" and url == "https://api.mercadolibre.com/items/CBT3475477379":
                    return {"id": "CBT3475477379", "title": "First", "status": "paused"}
                return {}

            with patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request):
                result = erp_web_app.mercadolibre_close_remote_item("CBT3475477379")

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "paused")
            self.assertEqual(calls, [
                ("GET", "https://api.mercadolibre.com/users/me", None),
                ("GET", "https://api.mercadolibre.com/items/CBT3475477379", None),
            ])

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_rejects_unchanged_global_status(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if method == "GET" and url == "https://api.mercadolibre.com/items/CBT3475477379":
                    return {"id": "CBT3475477379", "title": "First", "status": "active"}
                return {}

            with patch.object(erp_web_app.publisher, "request_json", side_effect=fake_request):
                result = erp_web_app.mercadolibre_close_remote_item("CBT3475477379")

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "MERCADOLIBRE_STATUS_UNCHANGED")
            self.assertEqual(result["status"], "active")

        self.with_temp_app(run)

    def test_publish_queue_requires_ready_to_publish_workflow(self) -> None:
        ready_product = sample_product("Queue ready item", "https://example.com/queue-ready")
        ready_product["source"]["image_pool"][0]["origin"] = "ai_generated"
        ready_product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "AI title",
            "description": "AI description",
            "copy_generated_at": "2026-05-30T11:00:00",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "MLM123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "price": "19.99",
            "stock": "5",
            "pricing": {"suggested_price": "19.99"},
            "publish_status": "ready",
        }
        ready_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:05:00"}
        }
        ready_status = erp_web_app.product_index_status(ready_product, "mercadolibre")

        pending_product = sample_product("Queue blocked item", "https://example.com/queue-blocked")
        pending_product["source"]["image_pool"][0]["origin"] = "ai_generated"
        pending_product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "AI title",
            "description": "AI description",
            "copy_generated_at": "2026-05-30T11:00:00",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "MLM123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "price": "19.99",
            "stock": "5",
            "pricing": {"suggested_price": "19.99"},
            "publish_status": "not_ready",
        }
        pending_status = erp_web_app.product_index_status(pending_product, "mercadolibre")

        self.assertEqual(ready_status["workflow_status"], "ready_to_publish")
        self.assertTrue(ready_status["publish_queue_ready"])
        self.assertEqual(ready_status["publish_queue_platforms"], ["mercadolibre"])

        self.assertEqual(pending_status["workflow_status"], "images_ready")
        self.assertFalse(pending_status["publish_queue_ready"])
        self.assertEqual(pending_status["publish_queue_platforms"], [])

        precheck_only_product = sample_product("Queue precheck item", "https://example.com/queue-precheck")
        precheck_only_product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "Manual title",
            "description": "Manual description",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "MLM123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "price": "19.99",
            "stock": "5",
            "publish_status": "ready",
        }
        precheck_only_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:10:00"}
        }
        precheck_status = erp_web_app.product_index_status(precheck_only_product, "mercadolibre")

        self.assertEqual(precheck_status["workflow_status"], "ready_to_publish")
        self.assertTrue(precheck_status["publish_queue_ready"])
        self.assertEqual(precheck_status["publish_queue_platforms"], ["mercadolibre"])

        payload_ready_product = sample_product("Queue payload-ready item", "https://example.com/queue-payload-ready")
        payload_ready_product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "Payload title",
            "description": "Payload description",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "MLM123",
            "attributes": {},
            "price": "19.99",
            "stock": "5",
            "publish_status": "ready",
        }
        payload_ready_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:20:00"}
        }
        payload_ready_status = erp_web_app.product_index_status(payload_ready_product, "mercadolibre")

        self.assertTrue(payload_ready_status["publish_queue_ready"])
        self.assertEqual(payload_ready_status["publish_queue_platforms"], ["mercadolibre"])

    def test_publish_bus_terminal_result_persists_product_and_log_once(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Persist publish result", "https://example.com/publish-result")
            product["source"]["image_pool"][0]["origin"] = "ai_generated"
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-30T11:00:00",
                "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
                "category_id": "MLM123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
                "price": "19.99",
                "stock": "5",
                "pricing": {"suggested_price": "19.99"},
                "publish_status": "ready",
            }
            product["publish_preview"] = {
                "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:05:00"}
            }
            saved = erp_web_app.save_product(product)
            job_state = {
                "job_id": "job-persist-1",
                "status": "completed",
                "created_at": "2026-05-30 12:00:00",
                "updated_at": "2026-05-30 12:01:00",
                "product": saved,
                "platforms": {
                    "mercadolibre": {
                        "platform": "mercadolibre",
                        "status": "success",
                        "stage": "finished",
                        "error": "",
                        "attempts": 1,
                        "created_at": "2026-05-30 12:00:00",
                        "updated_at": "2026-05-30 12:01:00",
                        "result": {"ok": True, "id": "MLMITEM1"},
                    }
                },
            }

            erp_web_app.persist_publish_bus_terminal_results(job_state)
            erp_web_app.persist_publish_bus_terminal_results(job_state)

            loaded = erp_db.load_product_model(app_dir, saved["product_id"])
            draft = loaded["drafts"]["mercadolibre"]
            self.assertEqual(draft["publish_status"], "published")
            self.assertEqual(draft["status"], "published")
            self.assertEqual(draft["last_publish_task"]["job_id"], "job-persist-1")

            logs = erp_web_app.load_publish_logs()
            matching = [item for item in logs if item.get("job_id") == "job-persist-1"]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["status"], "published")

        self.with_temp_app(run)

    def test_mercadolibre_image_upload_failure_returns_compact_response(self) -> None:
        def run(app_dir: Path) -> None:
            first_image = app_dir / "first.jpg"
            second_image = app_dir / "second.jpg"
            first_image.write_bytes(b"not-a-real-jpeg")
            second_image.write_bytes(b"also-not-a-real-jpeg")
            product = sample_product("Upload failure item", "https://example.com/upload-failure")
            product["source"]["image_pool"] = [
                {
                    "id": "img_1",
                    "path": str(first_image),
                    "origin": "local_upload",
                    "platforms": ["mercadolibre"],
                    "is_main": True,
                    "selected": True,
                    "order": 0,
                },
                {
                    "id": "img_2",
                    "path": str(second_image),
                    "origin": "local_upload",
                    "platforms": ["mercadolibre"],
                    "is_main": False,
                    "selected": True,
                    "order": 1,
                },
            ]
            ml_error = 'POST Mercado Libre picture upload failed: 400 {"message":"Error creating image. File not compatible with pictures engine","error":"bad_request","status":400,"cause":[]}'

            with (
                patch.object(erp_web_app, "ensure_mercadolibre_auth_ready", return_value={"ok": True, "token": "token"}),
                patch.object(erp_web_app, "validate_mercadolibre_draft", return_value={"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-06-11T00:00:00"}),
                patch.object(erp_web_app.publisher, "upload_mercadolibre_picture", side_effect=RuntimeError(ml_error)),
            ):
                result = erp_web_app.mercadolibre_real_publish(product, confirm=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "图片上传失败，已禁止真实发布")
            self.assertNotIn("product", result)
            self.assertIn("product_id", result)
            self.assertIn("productsIndex", result)
            errors = result["precheck"]["errors"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["code"], "IMAGE_UPLOAD_FAILED")
            self.assertIn("不兼容 Mercado Libre 图片引擎", errors[0]["message"])
            self.assertIn("共 2 次", errors[0]["message"])
            self.assertNotIn('"cause"', errors[0]["message"])

        self.with_temp_app(run)

    def test_mercadolibre_global_site_item_error_is_publish_failure(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("CBT site item error", "https://example.com/cbt-site-error")
            api_result = {
                "site_id": "CBT",
                "site_items": [
                    {
                        "site_id": "MLM",
                        "logistic_type": "remote",
                        "error": {
                            "message": "Validation error",
                            "error": "validation_error",
                            "status": 400,
                            "cause": [
                                {
                                    "code": "invalid.item.attribute.values",
                                    "message": "Attribute [RECOMMENDED_AGE_GROUP] is not valid, item values [(null:1)]",
                                    "references": ["item.name"],
                                    "type": "error",
                                }
                            ],
                        },
                    }
                ],
            }

            with (
                patch.object(erp_web_app, "ensure_mercadolibre_auth_ready", return_value={"ok": True, "token": "token"}),
                patch.object(erp_web_app, "validate_mercadolibre_draft", return_value={"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-06-11T00:00:00"}),
                patch.object(erp_web_app, "ensure_mercadolibre_pictures_uploaded", return_value={"ok": True, "product": product, "picture_refs": []}),
                patch.object(erp_web_app, "build_mercadolibre_payload_preview", return_value={"_global_selling": True, "category_id": "CBT457856", "sites_to_sell": [{"site_id": "MLM"}]}),
                patch.object(erp_web_app, "validate_publish_payload", return_value=[]),
                patch.object(erp_web_app.publisher, "publish_mercadolibre", return_value=api_result),
            ):
                result = erp_web_app.mercadolibre_real_publish(product, confirm=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "real_publish_failed")
            self.assertIn("RECOMMENDED_AGE_GROUP", result["error"])
            self.assertIn("site_item_errors", result["error_map"])
            saved = erp_db.load_product_model(app_dir, result["product_id"])
            self.assertEqual(saved["drafts"]["mercadolibre"]["publish_status"], "real_publish_failed")

            logs = erp_web_app.load_publish_logs()
            self.assertEqual(logs[0]["status"], "real_publish_failed")
            self.assertIn("RECOMMENDED_AGE_GROUP", logs[0]["error_message"])

        self.with_temp_app(run)

    def test_image_pool_delete_uses_current_saved_product_not_stale_request_body(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Delete image current state", "https://example.com/delete-image-current")
            product["source"]["image_pool"] = [
                {
                    "id": "remove_me",
                    "url": "https://example.com/remove.jpg",
                    "preview_url": "https://example.com/remove.jpg",
                    "origin": "source",
                    "platforms": ["mercadolibre"],
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                },
                {
                    "id": "keep_me",
                    "url": "https://example.com/keep.jpg",
                    "preview_url": "https://example.com/keep.jpg",
                    "origin": "source",
                    "platforms": ["mercadolibre"],
                    "selected": True,
                    "is_main": False,
                    "order": 1,
                },
            ]
            saved = erp_web_app.save_product(product)
            stale_product = {
                "product_id": saved["product_id"],
                "source": {
                    "image_pool": [saved["source"]["image_pool"][0]],
                },
            }
            captured: dict[str, object] = {}
            class FakeHandler:
                pass

            handler = FakeHandler()
            handler.read_body = lambda: {"product_id": stale_product["product_id"], "action": "delete", "image_ids": ["remove_me"]}
            handler.send_json = lambda data, status=200: captured.update({"data": data, "status": status})

            image_routes.handle_post(handler, "/api/image-pool/action", erp_web_app)

            self.assertEqual(captured["status"], 200)
            loaded = erp_db.load_product_model(app_dir, saved["product_id"])
            image_ids = [item["id"] for item in loaded["source"]["image_pool"]]
            self.assertEqual(image_ids, ["keep_me"])

        self.with_temp_app(run)

    def test_refresh_mercadolibre_category_cache_imports_official_tree_and_attributes(self) -> None:
        def run(app_dir: Path) -> None:
            responses = {
                "https://api.mercadolibre.com/sites/MLM/categories": [
                    {"id": "MLM1", "name": "Home, Furniture and Garden"},
                    {"id": "MLM2", "name": "Sports and Fitness"},
                ],
                "https://api.mercadolibre.com/categories/MLM1": {
                    "id": "MLM1",
                    "name": "Home, Furniture and Garden",
                    "path_from_root": [{"id": "MLM1", "name": "Home, Furniture and Garden"}],
                    "children_categories": [
                        {"id": "MLM10", "name": "Kitchen & Housewares", "total_items_in_this_category": 10},
                    ],
                },
                "https://api.mercadolibre.com/categories/MLM2": {
                    "id": "MLM2",
                    "name": "Sports and Fitness",
                    "path_from_root": [{"id": "MLM2", "name": "Sports and Fitness"}],
                    "children_categories": [],
                },
                "https://api.mercadolibre.com/categories/MLM10": {
                    "id": "MLM10",
                    "name": "Water Bottles",
                    "path_from_root": [
                        {"id": "MLM1", "name": "Home, Furniture and Garden"},
                        {"id": "MLM10", "name": "Kitchen & Housewares"},
                    ],
                    "children_categories": [],
                },
                "https://api.mercadolibre.com/categories/MLM2/attributes": [],
                "https://api.mercadolibre.com/categories/MLM10/attributes": [
                    {"id": "BRAND", "name": "Brand", "tags": {"required": True}, "value_type": "string"},
                    {"id": "MODEL", "name": "Model", "tags": {"required": True}, "value_type": "string"},
                ],
            }

            with patch.object(erp_web_app, "http_json", side_effect=lambda url, access_token=None: responses[url]):
                result = erp_web_app.refresh_official_category_cache("mercadolibre", site="MLM", max_categories=20)

            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["imported"], 2)
            results = erp_web_app.search_category_cache("mercadolibre", query="Bottle", site="MLM", limit=10)
            self.assertEqual(results[0]["category_id"], "MLM10")
            attrs = results[0]["attributes_cache"]
            self.assertEqual([item["id"] for item in attrs["required"]], ["BRAND", "MODEL"])
            self.assertEqual(erp_db.category_cache_status(app_dir, "mercadolibre")["records"], result["imported"])

        self.with_temp_app(run)

    def test_refresh_mercadolibre_category_cache_auth_error_keeps_cache_and_returns_next_action(self) -> None:
        def run(app_dir: Path) -> None:
            erp_db.import_category_cache(
                app_dir,
                {
                    "platform": "mercadolibre",
                    "site": "MLM",
                    "records": [
                        {
                            "platform": "mercadolibre",
                            "site": "MLM",
                            "category_id": "MLM-200",
                            "name_original": "Bottles",
                            "name_cn": "水瓶",
                            "path_original": ["Home", "Bottles"],
                            "path_cn": ["家居", "水瓶"],
                            "attributes_cache": {"required": [], "optional": []},
                        }
                    ],
                },
            )
            error = urllib.error.HTTPError("https://api.mercadolibre.com/sites/MLM/categories", 401, "Unauthorized", {}, None)

            with patch.object(erp_web_app, "http_json", side_effect=error):
                result = erp_web_app.refresh_official_category_cache("mercadolibre", site="MLM", max_categories=20)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED")
            self.assertIn("授权", result["next_action"])
            self.assertEqual(erp_db.category_cache_status(app_dir, "mercadolibre")["records"], 1)

        self.with_temp_app(run)

    def test_refresh_mercadolibre_category_cache_refreshes_token_once_after_auth_error(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "123",
                        "app_secret": "secret",
                        "access_token": "expired-token",
                        "refresh_token": "refresh-123",
                        "site_id": "MLM",
                    }
                }
            )
            auth_error = urllib.error.HTTPError("https://api.mercadolibre.com/sites/MLM/categories", 401, "Unauthorized", {}, None)
            responses = {
                "https://api.mercadolibre.com/sites/MLM/categories": [{"id": "MLM1", "name": "Home"}],
                "https://api.mercadolibre.com/categories/MLM1": {
                    "id": "MLM1",
                    "name": "Home",
                    "path_from_root": [{"id": "MLM1", "name": "Home"}],
                    "children_categories": [],
                },
                "https://api.mercadolibre.com/categories/MLM1/attributes": [],
            }
            calls: list[tuple[str, str | None]] = []

            def fake_http_json(url: str, access_token: str | None = None):
                calls.append((url, access_token))
                if len(calls) == 1:
                    raise auth_error
                return responses[url]

            with patch.object(erp_web_app, "http_json", side_effect=fake_http_json), patch.object(
                erp_web_app.publisher,
                "refresh_mercadolibre_token",
                return_value={"access_token": "fresh-token", "refresh_token": "refresh-456"},
            ):
                result = erp_web_app.refresh_official_category_cache("mercadolibre", max_categories=20)

            self.assertTrue(result["ok"])
            self.assertTrue(result["token_refreshed"])
            self.assertEqual(calls[0][1], "expired-token")
            self.assertEqual(calls[1][1], "fresh-token")
            saved = erp_web_app.load_store_config()["mercadolibre"]
            self.assertEqual(saved["access_token"], "fresh-token")
            self.assertEqual(saved["refresh_token"], "refresh-456")

        self.with_temp_app(run)

    def test_exchange_mercadolibre_code_returns_category_refresh_next_action(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "123",
                        "app_secret": "secret",
                        "redirect_uri": "https://example.com/callback",
                        "code_verifier": "verifier",
                    }
                }
            )

            with patch.object(
                erp_web_app.publisher,
                "exchange_mercadolibre_code",
                return_value={"access_token": "token-123", "refresh_token": "refresh-123", "user_id": "seller-1"},
            ), patch.object(erp_web_app.publisher, "fetch_mercadolibre_shop_name", return_value="Demo Shop"):
                result = erp_web_app.exchange_mercadolibre_code_from_body({"code_or_url": "https://example.com/callback?code=TG-1"})

            self.assertEqual(result["status"], "测试成功")
            self.assertIn("类目", result["next_action"])
            saved = erp_web_app.load_store_config()["mercadolibre"]
            self.assertEqual(saved["access_token"], "token-123")
            self.assertNotIn("code_verifier", saved)

        self.with_temp_app(run)

    def test_failed_mercadolibre_code_exchange_keeps_code_verifier_for_retry(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "123",
                        "app_secret": "secret",
                        "redirect_uri": "https://example.com/callback",
                        "code_verifier": "verifier",
                    }
                }
            )

            with patch.object(
                erp_web_app.publisher,
                "exchange_mercadolibre_code",
                side_effect=RuntimeError("invalid_client"),
            ):
                with self.assertRaises(RuntimeError):
                    erp_web_app.exchange_mercadolibre_code_from_body({"code_or_url": "https://example.com/callback?code=TG-1"})

            saved = erp_web_app.load_store_config()["mercadolibre"]
            self.assertEqual(saved["code_verifier"], "verifier")
            self.assertEqual(saved["app_id"], "123")
            self.assertEqual(saved["app_secret"], "secret")

        self.with_temp_app(run)

    def test_mercadolibre_auth_error_explainer_maps_common_errors_to_plain_next_actions(self) -> None:
        cases = [
            (
                "invalid_grant",
                "invalid_grant",
                "重新生成授权链接",
            ),
            (
                "redirect_uri_mismatch",
                "redirect_uri mismatch",
                "Redirect URI",
            ),
            (
                "CODE_VERIFIER_MISSING",
                "CODE_VERIFIER_MISSING",
                "重新生成授权链接",
            ),
            (
                "token_expired",
                "access token expired",
                "刷新 token",
            ),
        ]
        for code, message, expected in cases:
            with self.subTest(code=code):
                explanation = erp_web_app.explain_mercadolibre_auth_error(code, message)

                self.assertEqual(explanation["platform"], "mercadolibre")
                self.assertTrue(explanation["title"])
                self.assertIn(expected, explanation["next_action"])

    def test_mercadolibre_auth_checklist_reports_missing_and_copyable_lines(self) -> None:
        checklist = erp_web_app.mercadolibre_auth_checklist(
            {
                "app_id": "123",
                "app_secret": "",
                "redirect_uri": "http://localhost/callback",
                "code_verifier": "",
                "access_token": "",
                "refresh_token": "",
                "site_id": "MLM",
            }
        )

        self.assertFalse(checklist["ready_for_auth_link"])
        self.assertIn("CLIENT_SECRET_MISSING", checklist["missing_codes"])
        self.assertIn("REDIRECT_URI_MUST_BE_HTTPS", checklist["missing_codes"])
        self.assertIn("App ID", checklist["copy_text"])
        self.assertIn("下一步", checklist["copy_text"])
        self.assertIn("Client Secret", checklist["next_action"])

    def test_store_config_field_merge_preserves_saved_authorization_secrets(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "app-123",
                        "app_secret": "secret-123",
                        "client_secret": "secret-123",
                        "redirect_uri": "https://example.com/callback",
                        "access_token": "access-123",
                        "refresh_token": "refresh-123",
                    }
                }
            )

            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "",
                        "client_secret": "",
                        "app_secret": "",
                        "access_token": "",
                        "refresh_token": "",
                        "auth_status": "测试失败",
                        "auth_error_message": "missing token",
                    }
                }
            )

            saved = erp_web_app.load_store_config()["mercadolibre"]
            self.assertEqual(saved["app_id"], "app-123")
            self.assertEqual(saved["app_secret"], "secret-123")
            self.assertEqual(saved["client_secret"], "secret-123")
            self.assertEqual(saved["access_token"], "access-123")
            self.assertEqual(saved["refresh_token"], "refresh-123")
            self.assertEqual(saved["auth_status"], "测试失败")

        self.with_temp_app(run)

    def test_failed_store_auth_test_does_not_write_failure_result_to_store_config(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "app-123",
                        "app_secret": "secret-123",
                        "client_secret": "secret-123",
                        "redirect_uri": "https://example.com/callback",
                        "access_token": "",
                        "refresh_token": "refresh-123",
                    }
                }
            )

            with self.assertRaises(RuntimeError):
                erp_web_app.test_store_auth("mercadolibre")

            saved = erp_web_app.load_store_config()["mercadolibre"]
            self.assertEqual(saved["app_id"], "app-123")
            self.assertEqual(saved["app_secret"], "secret-123")
            self.assertEqual(saved["refresh_token"], "refresh-123")
            self.assertEqual(saved.get("auth_status") or "", "")
            self.assertEqual(saved.get("auth_error_message") or "", "")

        self.with_temp_app(run)

    def test_mercadolibre_ssl_eof_error_returns_network_guidance(self) -> None:
        message = "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1081)>"

        code = erp_web_app._mercadolibre_test_error_code(message)
        explanation = erp_web_app.explain_mercadolibre_auth_error(code, message)

        self.assertEqual(code, "network_tls_failed")
        self.assertEqual(explanation["code"], "network_tls_failed")
        self.assertIn("网络连接失败", explanation["title"])
        self.assertIn("代理", explanation["plain_message"])
        self.assertIn("api.mercadolibre.com", explanation["next_action"])

    def test_refresh_token_route_returns_auth_error_without_private_nameerror(self) -> None:
        def run(app_dir: Path) -> None:
            erp_web_app.save_store_config({"mercadolibre": {}})
            captured: dict[str, object] = {}
            handler = object.__new__(Handler)
            handler.path = "/api/mercadolibre/refresh-token"
            handler.read_body = lambda: {"app_id": "", "client_secret": "", "refresh_token": ""}
            handler.send_json = lambda data, status=200: captured.update({"data": data, "status": status})

            Handler.do_POST(handler)

            data = captured["data"]
            self.assertEqual(captured["status"], 400)
            self.assertIsInstance(data, dict)
            self.assertFalse(data["ok"])
            self.assertIn("Refresh Token", data["error"])
            self.assertNotIn("_mercadolibre_test_error_code", data["error"])

        self.with_temp_app(run)


if __name__ == "__main__":
    unittest.main()
