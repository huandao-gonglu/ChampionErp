from __future__ import annotations

import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import erp_db
import erp_web_app
from services import image_translate_service
from tests.test_erp_db import sample_product


class ErpWebDbIntegrationTests(unittest.TestCase):
    def with_temp_app(self, callback) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            output_dir = app_dir / "output"
            original_globals = {
                "APP_DIR": erp_web_app.APP_DIR,
                "DIST_DIR": erp_web_app.DIST_DIR,
                "OUTPUT_DIR": erp_web_app.OUTPUT_DIR,
                "PRODUCT_PATH": erp_web_app.PRODUCT_PATH,
                "DIST_PRODUCT_PATH": erp_web_app.DIST_PRODUCT_PATH,
                "PRODUCTS_INDEX_PATH": erp_web_app.PRODUCTS_INDEX_PATH,
                "PRODUCTS_DIR": erp_web_app.PRODUCTS_DIR,
                "PUBLISH_LOG_PATH": erp_web_app.PUBLISH_LOG_PATH,
                "STORE_CONFIG_PATH": erp_web_app.STORE_CONFIG_PATH,
                "DIST_STORE_CONFIG_PATH": erp_web_app.DIST_STORE_CONFIG_PATH,
                "APP_CONFIG_PATH": erp_web_app.APP_CONFIG_PATH,
                "DIST_APP_CONFIG_PATH": erp_web_app.DIST_APP_CONFIG_PATH,
            }
            try:
                erp_web_app.APP_DIR = app_dir
                erp_web_app.DIST_DIR = app_dir / "dist"
                erp_web_app.OUTPUT_DIR = output_dir
                erp_web_app.PRODUCT_PATH = app_dir / "product.json"
                erp_web_app.DIST_PRODUCT_PATH = app_dir / "dist" / "product.json"
                erp_web_app.PRODUCTS_INDEX_PATH = output_dir / "products_index.json"
                erp_web_app.PRODUCTS_DIR = output_dir / "products"
                erp_web_app.PUBLISH_LOG_PATH = output_dir / "publish_logs.json"
                erp_web_app.STORE_CONFIG_PATH = app_dir / "store_config.json"
                erp_web_app.DIST_STORE_CONFIG_PATH = app_dir / "dist" / "store_config.json"
                erp_web_app.APP_CONFIG_PATH = app_dir / "app_config.json"
                erp_web_app.DIST_APP_CONFIG_PATH = app_dir / "dist" / "app_config.json"
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
            index_records = erp_web_app.load_products_index()
            self.assertEqual(index_records[0]["product_id"], saved["product_id"])
            self.assertTrue(index_records[0]["product_file_path"].startswith("sqlite://"))
            loaded = erp_web_app.load_product_from_index(saved["product_id"], "")
            self.assertEqual(loaded["product_id"], saved["product_id"])
            self.assertEqual(loaded["name"], "Imported title")

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

        product["drafts"]["mercadolibre"]["images"] = ["https://example.com/ai.jpg"]
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

    def test_save_image_pool_changes_persists_media_and_moves_copy_ready_to_images_ready(self) -> None:
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
            self.assertEqual(loaded["drafts"]["mercadolibre"]["status"], "images_ready")
            self.assertEqual(loaded["drafts"]["mercadolibre"]["images"], ["https://example.com/ai-image.jpg"])
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
            self.assertEqual(records[0]["workflow_status"], "images_ready")

        self.with_temp_app(run)

    def test_image_translate_items_persist_and_move_copy_ready_to_images_ready(self) -> None:
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
                {"image_ai": {"platform": "OpenAI", "api_key": "test-key"}},
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

            self.assertEqual(loaded["drafts"]["mercadolibre"]["status"], "images_ready")
            self.assertEqual(len(translated_items), 1)
            self.assertEqual(translated_items[0]["origin"], "ai_generated")
            self.assertEqual(translated_items[0]["translated_from_id"], source_item_id)
            self.assertIn(translated_items[0]["path"], loaded["drafts"]["mercadolibre"]["images"])
            self.assertTrue((app_dir / translated_items[0]["path"]).exists())

            conn = sqlite3.connect(app_dir / erp_db.DEFAULT_DB_NAME)
            try:
                translated_media_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM media_assets
                    WHERE product_id = ? AND origin = 'ai_generated' AND local_path = ?
                    """,
                    (persisted["product_id"], translated_items[0]["path"]),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(translated_media_count, 1)

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
                "images": ["https://example.com/ai.jpg"],
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

    def test_publish_queue_requires_ready_to_publish_workflow(self) -> None:
        ready_product = sample_product("Queue ready item", "https://example.com/queue-ready")
        ready_product["source"]["image_pool"][0]["origin"] = "ai_generated"
        ready_product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "title": "AI title",
            "description": "AI description",
            "copy_generated_at": "2026-05-30T11:00:00",
            "images": ["https://example.com/ai.jpg"],
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
            "images": ["https://example.com/ai.jpg"],
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

    def test_publish_bus_terminal_result_persists_product_and_log_once(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Persist publish result", "https://example.com/publish-result")
            product["source"]["image_pool"][0]["origin"] = "ai_generated"
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-30T11:00:00",
                "images": ["https://example.com/ai.jpg"],
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


if __name__ == "__main__":
    unittest.main()
