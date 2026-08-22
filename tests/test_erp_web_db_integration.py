from __future__ import annotations

import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from erp_web import db as erp_db
from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.http_route_units import image_routes
from erp_web.runtime_units import (
    category_providers,
    category_store,
    collect_helpers,
    copy_generation,
    image_pool,
    publish_adapter,
    publish_bus,
    publish_helpers,
    publish_mercadolibre,
    publish_validation,
    source_collect_workflows,
    store_credentials,
)
from erp_web.runtime_units.publish_logs_runtime import mercadolibre_test_error_code
from erp_web.services import image_service, image_translate_service
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.stores import config_store
from tests.runtime_test_utils import temp_app_context
from tests.test_erp_db import sample_product


def pricing_targets(platform: str, site: str, currency: str, amount: str) -> dict:
    basis = {"listing_currency": currency}
    return {
        "targets": {
            f"{platform}:{site}".lower(): {
                "listing_currency": currency,
                "suggested_price": {"amount": amount, "currency": currency},
                "applied_price": {"amount": amount, "currency": currency},
                "calculation_basis": basis,
                "calculation_fingerprint": pricing_calculation_fingerprint(basis),
            }
        }
    }


class ErpWebDbIntegrationTests(unittest.TestCase):
    def with_temp_app(self, callback) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            with temp_app_context(app_dir):
                callback(app_dir)

    def test_save_product_uses_sqlite_as_primary_product_index(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product())

            db_records = get_context().db.list_product_records()
            self.assertEqual(len(db_records), 1)
            self.assertEqual(db_records[0]["product_id"], saved["product_id"])
            self.assertFalse((app_dir / "product.json").exists())
            index_records = get_context().products.load_products_index()
            self.assertEqual(index_records[0]["product_id"], saved["product_id"])
            self.assertTrue(index_records[0]["product_file_path"].startswith("sqlite://"))
            loaded = get_context().products.load_product_from_index(saved["product_id"], "")
            self.assertEqual(loaded["product_id"], saved["product_id"])
            self.assertEqual(loaded["name"], "Imported title")

        self.with_temp_app(run)

    def test_delete_products_from_index_removes_selected_sqlite_products(self) -> None:
        def run(app_dir: Path) -> None:
            first = get_context().products.save_product(sample_product("Delete me", "https://example.com/delete-me"))
            second = get_context().products.save_product(sample_product("Keep me", "https://example.com/keep-me"))

            result = get_context().products.delete_products_from_index([first["product_id"]])

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 1)
            self.assertEqual(result["deletedIds"], [first["product_id"]])
            remaining = get_context().db.list_product_records()
            self.assertEqual([item["product_id"] for item in remaining], [second["product_id"]])
            self.assertEqual(result["productsIndex"][0]["product_id"], second["product_id"])
            self.assertEqual(get_context().db.load_product_model(first["product_id"]), {})

        self.with_temp_app(run)

    def test_delete_draft_from_index_removes_only_selected_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product("Draft delete", "https://example.com/draft-delete"))
            draft_id = get_context().db.list_draft_records()[0]["draft_id"]

            result = get_context().products.delete_draft_from_index(draft_id)

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 1)
            self.assertEqual(result["deletedDraftId"], draft_id)
            self.assertEqual(result["draftsIndex"], [])
            self.assertEqual(result["product"]["product_id"], saved["product_id"])
            self.assertEqual(get_context().db.list_product_records()[0]["product_id"], saved["product_id"])
            self.assertEqual(get_context().db.list_draft_records(), [])

        self.with_temp_app(run)

    def test_delete_draft_from_index_accepts_draft_id_list(self) -> None:
        def run(app_dir: Path) -> None:
            first = get_context().products.save_product(sample_product("Draft delete 1", "https://example.com/draft-delete-1"))
            second = get_context().products.save_product(sample_product("Draft delete 2", "https://example.com/draft-delete-2"))
            draft_ids = [item["draft_id"] for item in get_context().db.list_draft_records()]

            result = get_context().products.delete_draft_from_index(draft_ids)

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], 2)
            self.assertEqual(result["deletedDraftIds"], draft_ids)
            self.assertEqual(result["deletedIds"], draft_ids)
            self.assertEqual(result["missingIds"], [])
            self.assertEqual(result["draftsIndex"], [])
            self.assertEqual(sorted(result["affectedProductIds"]), sorted([first["product_id"], second["product_id"]]))
            self.assertEqual(len(get_context().db.list_product_records()), 2)
            self.assertEqual(get_context().db.list_draft_records(), [])

        self.with_temp_app(run)

    def test_save_product_profile_does_not_overwrite_platform_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product("Profile boundary", "https://example.com/profile-boundary"))
            draft = get_context().db.list_draft_records()[0]

            profile = dict(saved)
            profile["name"] = "Profile boundary updated"
            profile["drafts"] = {
                "mercadolibre": {
                    "draft_id": draft["draft_id"],
                    "title": "Should not overwrite draft",
                    "description": "Should not overwrite draft description",
                }
            }
            updated = get_context().products.save_product_profile(profile)

            reloaded_draft = get_context().db.load_draft_model(draft["draft_id"])
            self.assertEqual(updated["name"], "Profile boundary updated")
            self.assertEqual(reloaded_draft["title"], "Titulo MX")
            self.assertEqual(reloaded_draft["description"], "Descripcion MX")

        self.with_temp_app(run)

    def test_save_product_profile_does_not_create_default_platform_drafts(self) -> None:
        def run(app_dir: Path) -> None:
            profile = sample_product(
                "Profile without draft",
                "https://example.com/profile-without-draft",
            )
            profile.pop("drafts")
            profile["attributes"] = {"source_attribute": "source value"}

            saved = get_context().products.save_product_profile(profile)
            get_context().products.save_product_profile(saved)

            self.assertEqual(
                get_context().db.list_draft_records(scope="all"),
                [],
            )

            result = collect_helpers.claim_products_to_platforms(
                [saved["product_id"]],
                ["ozon"],
            )
            drafts = get_context().db.list_draft_records(scope="all")

            self.assertTrue(result["ok"])
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["platform"], "ozon")

        self.with_temp_app(run)

    def test_save_draft_detail_updates_only_selected_draft(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product("Draft boundary", "https://example.com/draft-boundary"))
            product_id = saved["product_id"]
            ozon_draft_id = get_context().db.upsert_draft_model(product_id,
                "ozon",
                {
                    "title": "Ozon original",
                    "description": "Ozon description",
                    "pricing": {"targets": {"ozon:global": {"listing_currency": "RUB", "applied_price": {"amount": "22", "currency": "RUB"}}}},
                    "status": "copy_ready",
                },
            )
            yandex_draft_id = get_context().db.upsert_draft_model(product_id,
                "yandex",
                {
                    "title": "Yandex original",
                    "description": "Yandex description",
                    "pricing": {"targets": {"yandex:global": {"listing_currency": "RUB", "applied_price": {"amount": "21", "currency": "RUB"}}}},
                    "status": "copy_ready",
                },
            )

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": yandex_draft_id,
                    "title": "Yandex independent title",
                    "description": "Yandex independent description",
                    "pricing": {"targets": {"yandex:global": {"listing_currency": "RUB", "applied_price": {"amount": "33", "currency": "RUB"}}}},
                    "status": "copy_ready",
                    "language": "ru-RU",
                    "target_sites": [
                        {
                            "platform": "yandex",
                            "site": "global",
                            "category_id": "yandex-category-1",
                            "category_attribute_schema": {
                                "platform": "yandex",
                                "site": "global",
                                "category_id": "yandex-category-1",
                                "category_path": "测试类目",
                                "source": "platform_live",
                                "fetched_at": "2026-07-25T12:00:00Z",
                                "required": [
                                    {
                                        "id": "BRAND",
                                        "name": "Brand",
                                        "required": True,
                                        "options": [],
                                        "value_type": "string",
                                    }
                                ],
                                "optional": [],
                            },
                            "attributes": {"BRAND": "Test Brand"},
                        },
                        {"platform": "ozon", "site": "global"},
                    ],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(result["draft"]["title"], "Yandex independent title")
            self.assertEqual(result["draft"]["platforms"], ["yandex", "ozon"])
            self.assertEqual(
                result["draft"]["target_sites"][0]["category_attribute_schema"]["required"][0]["id"],
                "BRAND",
            )
            self.assertEqual(get_context().db.load_draft_model(yandex_draft_id)["title"], "Yandex independent title")
            self.assertEqual(get_context().db.load_draft_model(yandex_draft_id)["platforms"], ["yandex", "ozon"])
            updated_record = next(item for item in get_context().db.list_draft_records(scope="all") if item["draft_id"] == yandex_draft_id)
            self.assertEqual(updated_record["platforms"], ["yandex", "ozon"])
            self.assertEqual(get_context().db.load_draft_model(ozon_draft_id)["title"], "Ozon original")

        self.with_temp_app(run)

    def test_same_product_drafts_keep_separate_platform_selections(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product("Two draft copies", "https://example.com/two-drafts"))
            first_result = collect_helpers.claim_products_to_platforms([saved["product_id"]], ["yandex"])
            second_result = collect_helpers.claim_products_to_platforms([saved["product_id"]], ["yandex"])
            first_draft_id = first_result["items"][0]["draft_ids"][0]
            second_draft_id = second_result["items"][0]["draft_ids"][0]
            draft_ids_before_update = [item["draft_id"] for item in get_context().db.list_draft_records(scope="all")]

            self.assertNotEqual(first_draft_id, second_draft_id)
            result, error, status = get_context().products.save_draft_detail(
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
            self.assertEqual(get_context().db.load_draft_model(first_draft_id)["platforms"], ["yandex"])
            self.assertEqual(get_context().db.load_draft_model(second_draft_id)["platforms"], ["yandex", "ozon"])
            self.assertEqual(
                [item["draft_id"] for item in get_context().db.list_draft_records(scope="all")],
                draft_ids_before_update,
            )

        self.with_temp_app(run)

    def test_draft_ids_are_opaque_and_primary_platform_can_change(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product("Opaque draft", "https://example.com/opaque-draft"))
            product_id = saved["product_id"]
            created_id = get_context().db.upsert_draft_model(
                product_id,
                "mercadolibre",
                {"title": "ML draft", "status": "claimed"},
            )
            self.assertTrue(created_id.startswith("d"))
            self.assertEqual(len(created_id), 13)
            self.assertNotIn("mercadolibre", created_id)

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": created_id,
                    "language": "ru-RU",
                    "target_sites": [
                        {"platform": "ozon", "site": "global"},
                    ],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(result["draft"]["draft_id"], created_id)
            self.assertEqual(result["draft"]["platform"], "ozon")
            self.assertEqual(result["draft"]["site"], "global")
            self.assertEqual(result["draft"]["language"], "ru-RU")
            self.assertEqual(get_context().db.load_draft_model(created_id)["platform"], "ozon")
            records = [record for record in get_context().db.list_draft_records(scope="all") if record["draft_id"] == created_id]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["platform"], "ozon")

        self.with_temp_app(run)

    def test_1688_collect_images_are_limited_to_first_five(self) -> None:
        source = {
            "images": [f"https://img.example/{index}.jpg" for index in range(8)],
        }

        normalized = collect_helpers.normalize_collect_source_images(source, "1688", "http", ["mercadolibre"])

        self.assertEqual(len(normalized["image_pool"]), 5)
        self.assertEqual(normalized["images"], [f"https://img.example/{index}.jpg" for index in range(5)])

    def test_failed_new_collect_creates_failed_sqlite_record(self) -> None:
        def run(app_dir: Path) -> None:
            url = "https://detail.1688.com/offer/123456.html"
            with patch.object(
                source_collect_workflows,
                "fetch_page_html_with_status",
                return_value=("", ""),
            ):
                result = source_collect_workflows.collect_source_product(url, mode="http", platform="1688", claim_platforms=["mercadolibre"])

            self.assertFalse(result["ok"])
            records = get_context().db.list_product_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source_url"], url)
            self.assertEqual(records[0]["collect_status"], "failed")
            loaded = get_context().db.load_product_model(records[0]["product_id"])
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
                saved = get_context().products.save_product(product)
                return {"ok": True, "product": saved, "diagnostics": {"success": True, "error_code": ""}}

            with patch.object(
                source_collect_workflows,
                "collect_source_product",
                side_effect=fake_collect,
            ):
                result = source_collect_workflows.collect_batch_products(
                    "https://detail.1688.com/offer/1.html\nhttps://detail.1688.com/offer/bad.html",
                    mode="http",
                    platforms=["mercadolibre"],
                )

            self.assertEqual(calls, ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/bad.html"])
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["success_count"], 1)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual([row["status"] for row in result["items"]], ["success", "failed"])
            self.assertEqual(len(get_context().db.list_product_records()), 1)

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
            "pricing": {"targets": {}},
            "status": "claimed",
        }
        self.assertEqual(get_context().products.draft_workflow_status(product, "mercadolibre"), "claimed")

        product["drafts"]["mercadolibre"].update(
            {
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
            }
        )
        self.assertEqual(get_context().products.draft_workflow_status(product, "mercadolibre"), "copy_ready")

        product["drafts"]["mercadolibre"]["images"] = [{"asset_id": "img_1", "role": "main", "order": 0}]
        self.assertEqual(get_context().products.draft_workflow_status(product, "mercadolibre"), "images_ready")

        product["drafts"]["mercadolibre"].update(
            {
                "category_id": "MLM123",
                "attributes": {"BRAND": "BrandX"},
                "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
                "stock": "5",
            }
        )
        self.assertEqual(get_context().products.draft_workflow_status(product, "mercadolibre"), "images_ready")

        product = publish_validation.apply_precheck_to_product(
            product,
            "mercadolibre",
            {"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T10:00:00"},
            status="ready",
        )
        self.assertEqual(get_context().products.draft_workflow_status(product, "mercadolibre"), "ready_to_publish")

    def test_claim_products_to_platforms_creates_claimed_drafts_in_sqlite(self) -> None:
        def run(app_dir: Path) -> None:
            saved = get_context().products.save_product(sample_product())

            result = collect_helpers.claim_products_to_platforms([saved["product_id"]], ["mercadolibre", "ozon"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["claimed_count"], 1)
            created_drafts = [get_context().db.load_draft_model(draft_id) for draft_id in result["items"][0]["draft_ids"]]
            self.assertTrue(all(draft["source_product_id"] == saved["product_id"] for draft in created_drafts))
            self.assertTrue(any(draft["title"] == saved["source"]["title"] for draft in created_drafts))
            self.assertTrue(all(draft["description"] == saved["source"]["description"] for draft in created_drafts))
            self.assertTrue(all(draft["bullets"] == saved["source"]["bullets"] for draft in created_drafts))
            self.assertTrue(any(draft["images"] for draft in created_drafts))
            loaded = get_context().db.load_product_model(saved["product_id"])
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
                product["drafts"]["mercadolibre"] = {
                    "enabled": True,
                    "title": product["name"],
                    "description": "",
                    "images": [],
                    "status": "claimed",
                }
            first_saved = get_context().products.save_product(first)
            second_saved = get_context().products.save_product(second)

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

            with patch.object(
                copy_generation,
                "generate_ai_copy_bundle",
                side_effect=fake_bundle,
            ):
                result = copy_generation.batch_generate_copy_for_products(
                    [first_saved["product_id"], second_saved["product_id"]],
                    platform="mercadolibre",
                    language="Spanish",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["success_count"], 2)
            loaded_first = get_context().db.load_product_model(first_saved["product_id"])
            loaded_second = get_context().db.load_product_model(second_saved["product_id"])
            self.assertEqual(loaded_first["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(loaded_second["drafts"]["mercadolibre"]["status"], "copy_ready")
            self.assertEqual(loaded_first["drafts"]["mercadolibre"]["title"], "AI First collected")
            records = get_context().db.list_product_records()
            self.assertEqual({record["workflow_status"] for record in records}, {"copy_ready"})

        self.with_temp_app(run)

    def test_save_image_pool_changes_persists_media_without_touching_draft_images(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Image ready item", "https://example.com/image-ready")
            product["source"]["image_pool"] = []
            product["source"]["images"] = []
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "title": "AI title",
                "description": "AI description",
                "copy_generated_at": "2026-05-29T10:00:00",
                "images": [],
                "status": "copy_ready",
            }
            saved = get_context().products.save_product(product)

            result = image_pool.save_image_pool_for_product(
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
            loaded = get_context().db.load_product_model(saved["product_id"])
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
            records = get_context().db.list_product_records()
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
            product["source"]["image_pool"] = image_service.upload_images(
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
            saved = get_context().products.save_product(product)
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

            merged = image_pool.append_images_to_product_pool(saved, result["imagePoolItems"])
            persisted = get_context().products.save_product(merged)
            loaded = get_context().db.load_product_model(persisted["product_id"])
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
            product["source"]["image_pool"] = image_service.upload_images(
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
            saved = get_context().products.save_product(product)
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

            merged = image_pool.append_images_to_product_pool(saved, result["imagePoolItems"])
            persisted = get_context().products.save_product(merged)
            draft_result, draft_error, status = get_context().products.apply_image_assets_to_draft(
                draft_id,
                result["imagePoolItems"],
                "replace_selected",
            )

            self.assertIsNone(draft_error)
            self.assertEqual(status, 200)
            self.assertTrue(draft_result["ok"])
            loaded = get_context().db.load_product_model(persisted["product_id"])
            translated_item = next(item for item in loaded["source"]["image_pool"] if item.get("derived_from_id") == source_item_id)
            draft_images = get_context().db.load_draft_model(draft_id)["images"]
            self.assertEqual(
                draft_images,
                [{"asset_id": translated_item["id"], "role": "main", "order": 0, "source_asset_id": source_item_id}],
            )

        self.with_temp_app(run)

    def test_category_search_and_attrs_use_live_mercadolibre_api(self) -> None:
        def run(app_dir: Path) -> None:
            responses = {
                "https://api.mercadolibre.com/sites/MLM/domain_discovery/search?q=necklace&limit=5": [
                    {"domain_id": "MLM-NECKLACES", "domain_name": "Necklaces", "category_id": "MLM999", "category_name": "Necklaces"}
                ],
                "https://api.mercadolibre.com/categories/MLM999": {
                    "id": "MLM999",
                    "name": "Necklaces",
                    "path_from_root": [
                        {"id": "MLM1430", "name": "Clothes, Bags and Shoes"},
                        {"id": "MLM999", "name": "Necklaces"},
                    ],
                    "children_categories": [],
                },
                "https://api.mercadolibre.com/categories/MLM999/attributes": [
                    {"id": "BRAND", "name": "Brand", "tags": {"required": True}, "value_type": "string"},
                ],
            }

            http_json_mock = MagicMock(side_effect=lambda url, access_token=None: responses[url])
            with patch.object(
                category_providers,
                "http_json",
                http_json_mock,
            ):
                results = category_store.search_categories_live("mercadolibre", query="necklace", site="MLM", limit=5)
                attrs = category_store.fetch_category_attributes("mercadolibre", "MLM999", site="MLM")
            product = sample_product()
            product["drafts"]["mercadolibre"]["category_id"] = "MLM999"
            product["drafts"]["mercadolibre"]["attributes"] = {}
            product["local_platform_categories"] = {"mercadolibre": attrs["category"]}
            summary = publish_helpers._required_attribute_summary(product, "mercadolibre")

            self.assertEqual(results[0]["category_id"], "MLM999")
            self.assertEqual(results[0]["path"], "Necklaces")
            self.assertEqual(attrs["required"][0]["id"], "BRAND")
            self.assertEqual(attrs["source"], "mercadolibre_live")
            self.assertEqual(summary["required_count"], 1)
            self.assertEqual(summary["filled_count"], 0)
            self.assertTrue(
                all(
                    (not call.args[1:] or call.args[1] is None)
                    and call.kwargs == {}
                    for call in http_json_mock.call_args_list
                )
            )

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
                "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
                "stock": "5",
                "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
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
            saved = get_context().products.save_product(product)
            self.assertEqual(saved["drafts"]["mercadolibre"]["status"], "images_ready")

            prechecked = publish_validation.apply_precheck_to_product(
                saved,
                "mercadolibre",
                {"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T10:05:00"},
                status="ready",
            )
            persisted = get_context().products.save_product(prechecked)

            self.assertEqual(persisted["drafts"]["mercadolibre"]["status"], "ready_to_publish")
            self.assertTrue(persisted["publish_preview"]["mercadolibre"]["ok"])
            self.assertEqual(persisted["workflow_statuses"]["mercadolibre"], "ready_to_publish")

            records = get_context().db.list_product_records()
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
            "site": "MLM",
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
            "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "9.59"),
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

        payload = publish_adapter.require_publishing_adapter(
            "mercadolibre"
        ).build_payload(product, config)
        attributes = {item["id"]: item["value_name"] for item in payload["attributes"]}

        self.assertEqual(payload["title"], "Draft title for ML")
        self.assertEqual(payload["category_id"], "MLM455865")
        self.assertEqual(payload["price"], 9.59)
        self.assertEqual(payload["currency_id"], "MXN")
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

    def test_mercadolibre_publish_payload_uses_draft_target_site_over_store_config(self) -> None:
        product = sample_product("Draft CBT payload", "https://example.com/draft-cbt-payload")
        product["source"]["image_pool"][0].update(
            {
                "url": "https://example.com/draft-cbt-main.jpg",
                "preview_url": "https://example.com/draft-cbt-main.jpg",
                "platforms": ["mercadolibre"],
                "is_main": True,
                "selected": True,
                "status": "ready",
            }
        )
        product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "site": "CBT",
            "title": "Draft CBT title",
            "description": "Draft CBT description",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "category_id": "CBT457856",
            "attributes": {"BRAND": "DraftBrand", "MODEL": "DraftModel"},
            "target_sites": [{"platform": "mercadolibre", "site": "CBT", "market_currency": "USD", "listing_currency": "USD"}],
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "18.00"),
            "stock": "10",
            "sku": "DRAFT-CBT-SKU",
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

        payload = publish_adapter.require_publishing_adapter(
            "mercadolibre"
        ).build_payload(product, config)
        attributes = {item["id"]: item["value_name"] for item in payload["attributes"]}

        self.assertTrue(payload["_global_selling"])
        self.assertEqual(payload["category_id"], "CBT457856")
        self.assertEqual(payload["sites_to_sell"][0]["site_id"], "CBT")
        self.assertEqual(attributes["PACKAGE_LENGTH"], "11.0 cm")
        self.assertNotIn("SELLER_PACKAGE_LENGTH", attributes)

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
                    "stock": "5",
                    "publish_status": "real_publish_success",
                    "status": "published",
                }
            )
            saved = get_context().products.save_product(product)
            published_sku = str(
                saved["drafts"]["mercadolibre"]["sku"]
            )

            result = collect_helpers.claim_products_to_platforms([saved["product_id"]], ["mercadolibre"])

            self.assertTrue(result["ok"])
            all_drafts = get_context().db.list_draft_records(scope="all")
            active_drafts = get_context().db.list_draft_records()
            statuses = sorted(item["status"] for item in all_drafts if item["platform"] == "mercadolibre")
            self.assertEqual(statuses, ["claimed", "published"])
            self.assertEqual([item["status"] for item in active_drafts], ["claimed"])
            claimed = get_context().db.load_draft_model(
                result["items"][0]["draft_ids"][0]
            )
            self.assertNotEqual(claimed["sku"], published_sku)

        self.with_temp_app(run)

    def test_mercadolibre_remote_items_lists_seller_items(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})

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
                patch.object(publisher, "fetch_mercadolibre_shop_name", return_value="shop"),
                patch.object(publisher, "request_json", side_effect=fake_request),
            ):
                result = publish_mercadolibre.mercadolibre_remote_items("active")

            self.assertTrue(result["ok"])
            self.assertEqual([item["id"] for item in result["items"]], ["MLM2", "MLM1"])
            self.assertEqual(result["items"][1]["seller_sku"], "SKU-1")
            self.assertEqual(result["paging"]["active"]["total"], 2)
            self.assertEqual(result["pagination"]["page"], 1)
            self.assertEqual(result["pagination"]["total"], 2)

        self.with_temp_app(run)

    def test_mercadolibre_remote_items_supports_second_page(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})

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

            with patch.object(publisher, "request_json", side_effect=fake_request):
                result = publish_mercadolibre.mercadolibre_remote_items("active", page=2, per_page=50)

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
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345"}})
            calls: list[tuple[str, str, dict | list | None]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url, payload))
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "MLM"}
                return {"id": "MLM1", "title": "First", "status": "closed"}

            with (
                patch.object(publisher, "fetch_mercadolibre_shop_name", return_value="shop"),
                patch.object(publisher, "request_json", side_effect=fake_request),
            ):
                result = publish_mercadolibre.mercadolibre_close_remote_item("MLM1")

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "closed")
            self.assertEqual(calls, [
                ("GET", "https://api.mercadolibre.com/users/me", None),
                ("PUT", "https://api.mercadolibre.com/items/MLM1", {"status": "closed"}),
            ])

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_deletes_global_site_listing(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})
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

            with patch.object(publisher, "request_json", side_effect=fake_request):
                result = publish_mercadolibre.mercadolibre_close_remote_item("CBT3475477379")

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
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})
            calls: list[tuple[str, str, dict | list | None]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url, payload))
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if method == "GET" and url == "https://api.mercadolibre.com/items/CBT3475477379":
                    return {"id": "CBT3475477379", "title": "First", "status": "paused"}
                return {}

            with patch.object(publisher, "request_json", side_effect=fake_request):
                result = publish_mercadolibre.mercadolibre_close_remote_item("CBT3475477379")

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "paused")
            self.assertEqual(calls, [
                ("GET", "https://api.mercadolibre.com/users/me", None),
                ("GET", "https://api.mercadolibre.com/items/CBT3475477379", None),
            ])

        self.with_temp_app(run)

    def test_mercadolibre_close_remote_item_rejects_unchanged_global_status(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config({"mercadolibre": {"access_token": "token", "user_id": "12345", "site_id": "MLM"}})

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                if url == "https://api.mercadolibre.com/users/me":
                    return {"id": "12345", "nickname": "shop", "site_id": "CBT"}
                if method == "GET" and url == "https://api.mercadolibre.com/items/CBT3475477379":
                    return {"id": "CBT3475477379", "title": "First", "status": "active"}
                return {}

            with patch.object(publisher, "request_json", side_effect=fake_request):
                result = publish_mercadolibre.mercadolibre_close_remote_item("CBT3475477379")

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
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
            "stock": "5",
            "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
            "publish_status": "ready",
        }
        ready_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:05:00"}
        }
        ready_status = get_context().products.product_index_status(ready_product, "mercadolibre")

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
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
            "stock": "5",
            "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
            "publish_status": "not_ready",
        }
        pending_status = get_context().products.product_index_status(pending_product, "mercadolibre")

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
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
            "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
            "stock": "5",
            "publish_status": "ready",
        }
        precheck_only_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:10:00"}
        }
        precheck_status = get_context().products.product_index_status(precheck_only_product, "mercadolibre")

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
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
            "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
            "stock": "5",
            "publish_status": "ready",
        }
        payload_ready_product["publish_preview"] = {
            "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:20:00"}
        }
        payload_ready_status = get_context().products.product_index_status(payload_ready_product, "mercadolibre")

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
                "target_sites": [{"platform": "mercadolibre", "site": "MLM", "market_currency": "MXN", "listing_currency": "MXN"}],
                "stock": "5",
                "pricing": pricing_targets("mercadolibre", "MLM", "MXN", "19.99"),
                "publish_status": "ready",
            }
            product["publish_preview"] = {
                "mercadolibre": {"ok": True, "errors": [], "warnings": [], "checked_at": "2026-05-30T11:05:00"}
            }
            saved = get_context().products.save_product(product)
            job_state = {
                "job_id": "job-persist-1",
                "status": "completed",
                "created_at": "2026-05-30 12:00:00",
                "updated_at": "2026-05-30 12:01:00",
                "product": saved,
                "platforms": {
                    "mercadolibre": {
                        "platform": "mercadolibre",
                        "draft_id": str(saved["drafts"]["mercadolibre"]["draft_id"]),
                        "site": str(saved["drafts"]["mercadolibre"].get("site") or "MLM"),
                        "product_id": str(saved["product_id"]),
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

            publish_bus.persist_publish_bus_terminal_results(job_state)
            publish_bus.persist_publish_bus_terminal_results(job_state)

            loaded = get_context().db.load_product_model(saved["product_id"])
            draft = loaded["drafts"]["mercadolibre"]
            self.assertEqual(draft["publish_status"], "published")
            self.assertEqual(draft["status"], "published")
            self.assertEqual(draft["last_publish_task"]["job_id"], "job-persist-1")

            logs = publish_bus.load_publish_logs()
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
                patch.object(
                    publish_mercadolibre,
                    "ensure_mercadolibre_auth_ready",
                    return_value={"ok": True, "token": "token"},
                ),
                patch.object(
                    publish_mercadolibre,
                    "validate_mercadolibre_draft",
                    return_value={"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-06-11T00:00:00"},
                ),
                patch.object(publisher, "upload_mercadolibre_picture", side_effect=RuntimeError(ml_error)),
            ):
                result = publish_mercadolibre.mercadolibre_real_publish(product, confirm=True)

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
                patch.object(
                    publish_mercadolibre,
                    "ensure_mercadolibre_auth_ready",
                    return_value={"ok": True, "token": "token"},
                ),
                patch.object(
                    publish_mercadolibre,
                    "validate_mercadolibre_draft",
                    return_value={"platform": "mercadolibre", "ok": True, "errors": [], "warnings": [], "checked_at": "2026-06-11T00:00:00"},
                ),
                patch.object(
                    publish_mercadolibre,
                    "ensure_mercadolibre_pictures_uploaded",
                    return_value={"ok": True, "product": product, "picture_refs": []},
                ),
                patch.object(
                    publish_mercadolibre,
                    "build_mercadolibre_payload_preview",
                    return_value={"_global_selling": True, "category_id": "CBT457856", "sites_to_sell": [{"site_id": "MLM"}]},
                ),
                patch.object(
                    publish_mercadolibre,
                    "validate_publish_payload",
                    return_value=[],
                ),
                patch.object(publisher, "publish_mercadolibre", return_value=api_result),
            ):
                result = publish_mercadolibre.mercadolibre_real_publish(product, confirm=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "real_publish_failed")
            self.assertIn("RECOMMENDED_AGE_GROUP", result["error"])
            self.assertIn("site_item_errors", result["error_map"])
            saved = get_context().db.load_product_model(result["product_id"])
            self.assertEqual(saved["drafts"]["mercadolibre"]["publish_status"], "real_publish_failed")

            logs = publish_bus.load_publish_logs()
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
            saved = get_context().products.save_product(product)
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

            image_routes.handle_post(handler, "/api/image-pool/action")

            self.assertEqual(captured["status"], 200)
            loaded = get_context().db.load_product_model(saved["product_id"])
            image_ids = [item["id"] for item in loaded["source"]["image_pool"]]
            self.assertEqual(image_ids, ["keep_me"])

        self.with_temp_app(run)

    def test_exchange_mercadolibre_code_returns_live_category_next_action(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
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
                publisher,
                "exchange_mercadolibre_code",
                return_value={"access_token": "token-123", "refresh_token": "refresh-123", "user_id": "seller-1"},
            ), patch.object(publisher, "fetch_mercadolibre_shop_name", return_value="Demo Shop"):
                result = store_credentials.exchange_mercadolibre_code_from_body({"code_or_url": "https://example.com/callback?code=TG-1"})

            self.assertEqual(result["status"], "测试成功")
            self.assertIn("实时匹配", result["next_action"])
            saved = get_context().config.load_store_config()["mercadolibre"]
            self.assertEqual(saved["access_token"], "token-123")
            self.assertNotIn("code_verifier", saved)

        self.with_temp_app(run)

    def test_failed_mercadolibre_code_exchange_keeps_code_verifier_for_retry(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
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
                publisher,
                "exchange_mercadolibre_code",
                side_effect=RuntimeError("invalid_client"),
            ):
                with self.assertRaises(RuntimeError):
                    store_credentials.exchange_mercadolibre_code_from_body({"code_or_url": "https://example.com/callback?code=TG-1"})

            saved = get_context().config.load_store_config()["mercadolibre"]
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
                explanation = config_store.explain_mercadolibre_auth_error(code, message)

                self.assertEqual(explanation["platform"], "mercadolibre")
                self.assertTrue(explanation["title"])
                self.assertIn(expected, explanation["next_action"])

    def test_mercadolibre_auth_checklist_reports_missing_and_copyable_lines(self) -> None:
        checklist = get_context().config.mercadolibre_auth_checklist(
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
            get_context().config.save_store_config(
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

            get_context().config.save_store_config(
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

            saved = get_context().config.load_store_config()["mercadolibre"]
            self.assertEqual(saved["app_id"], "app-123")
            self.assertEqual(saved["app_secret"], "secret-123")
            self.assertEqual(saved["client_secret"], "secret-123")
            self.assertEqual(saved["access_token"], "access-123")
            self.assertEqual(saved["refresh_token"], "refresh-123")
            self.assertEqual(saved["auth_status"], "测试失败")

        self.with_temp_app(run)

    def test_failed_store_auth_test_replaces_prior_success_state(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "app-123",
                        "app_secret": "secret-123",
                        "client_secret": "secret-123",
                        "redirect_uri": "https://example.com/callback",
                        "access_token": "access-123",
                        "refresh_token": "refresh-123",
                        "auth_status": "测试成功",
                        "auth_checked_at": "2000-01-01T00:00:00Z",
                        "auth_masked_account": "old-shop",
                    }
                }
            )

            tester = MagicMock(side_effect=RuntimeError("401 unauthorized"))
            with (
                patch.object(
                    store_credentials,
                    "resolve_store_auth_tester",
                    return_value=tester,
                ),
                self.assertRaisesRegex(RuntimeError, "测试失败：401 unauthorized"),
            ):
                store_credentials.test_store_auth("mercadolibre")

            saved = get_context().config.load_store_config()["mercadolibre"]
            self.assertEqual(saved["app_id"], "app-123")
            self.assertEqual(saved["app_secret"], "secret-123")
            self.assertEqual(saved["access_token"], "access-123")
            self.assertEqual(saved["refresh_token"], "refresh-123")
            self.assertEqual(saved["auth_status"], "测试失败")
            self.assertEqual(saved["auth_error_code"], "permission_denied")
            self.assertEqual(saved["auth_error_message"], "401 unauthorized")
            self.assertTrue(saved["auth_next_action"])
            self.assertNotEqual(saved["auth_checked_at"], "2000-01-01T00:00:00Z")
            summary = config_store.summarize_store_auth_states(
                get_context().config.load_store_config()
            )["mercadolibre"]
            self.assertEqual(summary["status"], "权限不足")
            self.assertFalse(summary["bound"])
            tester.assert_called_once()

        self.with_temp_app(run)

    def test_returned_store_auth_failure_replaces_prior_success_state(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {
                    "ozon": {
                        "client_id": "client-123",
                        "api_key": "api-key-123",
                        "auth_status": "测试成功",
                        "auth_checked_at": "2000-01-01T00:00:00Z",
                        "auth_masked_account": "old-ozon-shop",
                    }
                }
            )
            tester = MagicMock(
                return_value={
                    "ok": False,
                    "status": "failed",
                    "error_code": "seller_blocked",
                    "error": "店铺已被冻结",
                    "next_action": "联系平台客服解除冻结",
                }
            )
            with (
                patch.object(
                    store_credentials,
                    "resolve_store_auth_tester",
                    return_value=tester,
                ),
                self.assertRaisesRegex(RuntimeError, "测试失败：店铺已被冻结"),
            ):
                store_credentials.test_store_auth("ozon")

            saved_config = get_context().config.load_store_config()
            saved = saved_config["ozon"]
            self.assertEqual(saved["client_id"], "client-123")
            self.assertEqual(saved["api_key"], "api-key-123")
            self.assertEqual(saved["auth_status"], "测试失败")
            self.assertEqual(saved["auth_error_code"], "seller_blocked")
            self.assertEqual(saved["auth_error_message"], "店铺已被冻结")
            self.assertEqual(saved["auth_next_action"], "联系平台客服解除冻结")
            self.assertNotEqual(saved["auth_checked_at"], "2000-01-01T00:00:00Z")
            summary = config_store.summarize_store_auth_states(saved_config)["ozon"]
            self.assertEqual(summary["status"], "测试失败")
            self.assertFalse(summary["bound"])
            tester.assert_called_once()

        self.with_temp_app(run)

    def test_store_auth_preview_uses_unsaved_ozon_credentials_without_persisting_them(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {
                    "ozon": {
                        "client_id": "saved-client",
                        "api_key": "saved-api-key",
                    }
                }
            )

            def tester(config: dict, scope: str) -> dict:
                self.assertEqual(scope, "")
                self.assertEqual(config["ozon"]["client_id"], "unsaved-client")
                self.assertEqual(config["ozon"]["api_key"], "unsaved-api-key")
                config["ozon"].update(
                    config_store._store_auth_result_fields(
                        "ozon",
                        "测试成功",
                        "preview-shop",
                    )
                )
                config["ozon"]["shop_name"] = "preview-shop"
                return {"listing_currency": "RUB"}

            with (
                patch.object(
                    store_credentials,
                    "resolve_store_auth_tester",
                    return_value=tester,
                ),
                patch.object(get_context().config, "save_store_config") as save_config,
            ):
                result = store_credentials.test_store_auth(
                    "ozon",
                    config_override={
                        "ozon": {
                            "client_id": "unsaved-client",
                            "api_key": "unsaved-api-key",
                        }
                    },
                )

            save_config.assert_not_called()
            saved = get_context().config.load_store_config()["ozon"]
            self.assertEqual(saved["client_id"], "saved-client")
            self.assertEqual(saved["api_key"], "saved-api-key")
            self.assertNotEqual(saved.get("shop_name"), "preview-shop")
            self.assertTrue(result["ok"])
            self.assertEqual(result["shop_name"], "preview-shop")

        self.with_temp_app(run)

    def test_mercadolibre_ssl_eof_error_returns_network_guidance(self) -> None:
        message = "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1081)>"

        code = mercadolibre_test_error_code(message)
        explanation = config_store.explain_mercadolibre_auth_error(code, message)

        self.assertEqual(code, "network_tls_failed")
        self.assertEqual(explanation["code"], "network_tls_failed")
        self.assertIn("网络连接失败", explanation["title"])
        self.assertIn("代理", explanation["plain_message"])
        self.assertIn("api.mercadolibre.com", explanation["next_action"])

    def test_refresh_token_route_returns_auth_error_without_private_nameerror(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config({"mercadolibre": {}})
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
