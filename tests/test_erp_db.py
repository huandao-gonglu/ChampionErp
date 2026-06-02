from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import erp_db


def sample_product(title: str = "Imported title", source_url: str = "https://example.com/item") -> dict:
    return {
        "product_id": "",
        "name": title,
        "brand": "BrandX",
        "model": "ModelY",
        "source_url": source_url,
        "source_platform": "1688",
        "detected_price": "12.34",
        "detected_currency": "CNY",
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
    def test_initialize_database_creates_required_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)

            db_path = erp_db.initialize_database(app_dir)

            self.assertTrue(db_path.exists())
            conn = sqlite3.connect(db_path)
            try:
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
            self.assertTrue(set(erp_db.REQUIRED_TABLES).issubset(table_names))

    def test_upsert_product_model_writes_product_drafts_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            erp_db.initialize_database(app_dir)
            product = sample_product()

            product_id = erp_db.upsert_product_model(app_dir, product)

            loaded = erp_db.load_product_model(app_dir, product_id)
            self.assertEqual(loaded["name"], "Imported title")
            self.assertEqual(loaded["drafts"]["mercadolibre"]["title"], "Titulo MX")
            records = erp_db.list_product_records(app_dir)
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

    def test_migrate_legacy_json_imports_current_and_product_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            output_products = app_dir / "output" / "products"
            output_products.mkdir(parents=True)
            (app_dir / "product.json").write_text(
                json.dumps(sample_product("Current product", "https://example.com/current"), ensure_ascii=False),
                encoding="utf-8",
            )
            (output_products / "snapshot.json").write_text(
                json.dumps(sample_product("Snapshot product", "https://example.com/snapshot"), ensure_ascii=False),
                encoding="utf-8",
            )

            result = erp_db.migrate_legacy_json(app_dir)

            self.assertEqual(result["imported"], 2)
            records = erp_db.list_product_records(app_dir)
            self.assertEqual({item["title"] for item in records}, {"Current product", "Snapshot product"})

    def test_import_category_cache_searches_chinese_and_loads_required_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            cache = {
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
                        "keywords": ["项链", "吊坠"],
                        "attributes_cache": {
                            "required": [
                                {"id": "BRAND", "name": "品牌", "required": True},
                                {"id": "MODEL", "name": "型号", "required": True},
                            ],
                            "optional": [{"id": "COLOR", "name": "颜色", "required": False}],
                        },
                    }
                ],
            }

            imported = erp_db.import_category_cache(app_dir, cache)
            results = erp_db.search_category_records(app_dir, "mercadolibre", query="项链", site="MLM")
            record = erp_db.find_category_record(app_dir, "mercadolibre", "MLM999", site="MLM")
            status = erp_db.category_cache_status(app_dir, "mercadolibre")

            self.assertEqual(imported, 1)
            self.assertEqual(results[0]["category_id"], "MLM999")
            self.assertEqual(record["attributes_cache"]["required"][0]["id"], "BRAND")
            self.assertEqual(status["records"], 1)


if __name__ == "__main__":
    unittest.main()
