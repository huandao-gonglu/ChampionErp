from __future__ import annotations

from copy import deepcopy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from erp_web import db as erp_db
from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.http_handler import Handler
from erp_web.http_route_units import image_routes
from erp_web.runtime_units import (
    category_providers,
    category_refresh,
    category_searchers,
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
from tests.runtime_test_utils import seed_store_currency, temp_app_context
from tests.test_erp_db import sample_product


def pricing_targets(platform: str, site: str, currency: str, amount: str, currency_fingerprint: str = "") -> dict:
    basis = {"listing_currency": currency, "currency_fingerprint": currency_fingerprint}
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


def mercadolibre_cbt_target() -> dict:
    return {
        "platform": "mercadolibre",
        "site": "CBT",
        "language": "en-US",
        "listing_currency": "USD",
        "sites_to_sell": [
            {"site_id": "MLM", "logistic_type": "remote"}
        ],
    }


def _ml_profile_via_wire(token: str) -> dict:
    """统一授权服务桥接：users/me 仍走被 patch 的 request_json，
    保留 wire 级调用序列断言。"""

    data = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
    data = data if isinstance(data, dict) else {}
    return {
        "user_id": str(data.get("id") or "").strip(),
        "nickname": str(data.get("nickname") or "").strip(),
        "site_id": str(data.get("site_id") or "").strip(),
    }


def _save_mercadolibre_user_product(
    siteless_id: str = "U123",
    *,
    status: str = "active",
    with_second_market: bool = False,
) -> dict:
    product = sample_product(
        f"User Product {siteless_id}",
        f"https://example.com/{siteless_id.lower()}",
    )
    draft = product["drafts"]["mercadolibre"]
    draft.update(
        {
            "site": "CBT",
            "language": "en-US",
            "category_id": "CBT123",
            "target_sites": [
                {
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "language": "en-US",
                    "listing_currency": "USD",
                    "sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"},
                        *(
                            [{"site_id": "MLB", "logistic_type": "remote"}]
                            if with_second_market
                            else []
                        ),
                    ],
                }
            ],
            "publication": {
                "model": "user_products",
                "account_user_id": "99",
                "parent_item_id": "CBT100",
                "parent_user_product_id": f"CBT{siteless_id}",
                "siteless_user_product_id": siteless_id,
                "family_name": f"Family {siteless_id}",
                "status": status,
                "markets": [
                    {
                        "site_id": "MLM",
                        "seller_id": "1001",
                        "logistic_type": "remote",
                        "item_id": "MLM100",
                        "user_product_id": f"MLM{siteless_id}",
                        "status": status,
                        "price": 19.99,
                        "currency_id": "USD",
                    },
                    *(
                        [
                            {
                                "site_id": "MLB",
                                "seller_id": "1002",
                                "logistic_type": "remote",
                                "item_id": "MLB100",
                                "user_product_id": f"MLB{siteless_id}",
                                "status": status,
                                "price": 20.99,
                                "currency_id": "USD",
                            }
                        ]
                        if with_second_market
                        else []
                    ),
                ],
                "updated_at": "2026-08-26T10:00:00",
            },
        }
    )
    return get_context().products.save_product(product)


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

    def test_duplicate_draft_copies_content_and_resets_publish_identity(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "Draft duplicate",
                "https://example.com/draft-duplicate",
            )
            product["publish_preview"] = {
                "mercadolibre": {"ok": True, "checked_at": "2026-08-28T08:00:00Z"}
            }
            saved = get_context().products.save_product(product)
            original_id = saved["drafts"]["mercadolibre"]["draft_id"]
            original = get_context().db.load_draft_model(original_id)
            precheck = {
                "ok": True,
                "checked_at": "2026-08-28T08:00:00Z",
            }
            publish_task = {
                "job_id": "publish-original",
                "item_id": "CBT123456",
            }
            validation_errors = [
                {
                    "code": "NEED_REVIEW_ATTRIBUTES",
                    "field": "attributes.BRAND",
                    "message": "请确认品牌",
                }
            ]
            persisted_validation_errors = [
                *validation_errors,
                {
                    "code": "PRICE_REQUIRED",
                    "field": "price",
                    "message": "旧预检价格错误",
                },
            ]
            original_target = dict(original["target_sites"][0])
            original_target.update(
                {
                    "category_id": "CBT-DUPLICATE",
                    "category_path": "Home / Duplicate",
                    "attributes": {"BRAND": "BrandX", "MODEL": "D1"},
                    "sites_to_sell": [
                        {
                            "site_id": "MLM",
                            "logistic_type": "remote",
                            "price": "29.90",
                            "status": "paused",
                        }
                    ],
                    "validation_errors": persisted_validation_errors,
                    "category_precheck": precheck,
                    "last_precheck": precheck,
                    "last_precheck_target": {
                        "platform": "mercadolibre",
                        "site": "CBT",
                    },
                    "publish_status": "real_publish_success",
                    "status": "published",
                    "last_publish_task": publish_task,
                }
            )
            original.update(
                {
                    "title": "Duplicated editable title",
                    "description": "Duplicated editable description",
                    "category_id": "CBT-DUPLICATE",
                    "category_path": "Home / Duplicate",
                    "attributes": {"BRAND": "BrandX", "MODEL": "D1"},
                    "images": [
                        {"asset_id": "img_1", "role": "main", "order": 0}
                    ],
                    "copy_generated_at": "2026-08-28T07:00:00Z",
                    "sku": "ML-ORIGINAL-SKU",
                    "upc": "012345678905",
                    "copy_operation_key": "copy-operation-original",
                    "validation_errors": persisted_validation_errors,
                    "category_precheck": precheck,
                    "last_precheck": precheck,
                    "last_precheck_target": original_target,
                    "publish_status": "real_publish_success",
                    "status": "published",
                    "last_publish_task": publish_task,
                    "publication": {
                        "model": "user_products",
                        "siteless_user_product_id": "UP-ORIGINAL",
                    },
                    "target_sites": [original_target],
                }
            )
            get_context().db.upsert_draft_model(
                saved["product_id"],
                "mercadolibre",
                original,
            )
            with get_context().db._connect() as conn:
                conn.execute(
                    """
                    UPDATE platform_drafts
                    SET created_at = ?, updated_at = ?
                    WHERE draft_id = ?
                    """,
                    (
                        "2024-01-01T00:00:00Z",
                        "2024-01-02T00:00:00Z",
                        original_id,
                    ),
                )
                conn.commit()
            original_before = get_context().db.load_draft_model(original_id)

            with patch.object(
                erp_db,
                "utc_now",
                return_value="2026-08-30T09:00:00Z",
            ):
                result, error, status = (
                    get_context().products.duplicate_draft_from_index(
                        original_id
                    )
                )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            duplicated = result["draft"]
            duplicated_target = duplicated["target_sites"][0]
            self.assertNotEqual(duplicated["draft_id"], original_id)
            self.assertEqual(duplicated["product_id"], original_before["product_id"])
            self.assertEqual(
                duplicated["source_product_id"],
                original_before["source_product_id"],
            )
            self.assertEqual(duplicated["platform"], original_before["platform"])
            for field in (
                "title",
                "description",
                "images",
                "category_id",
                "category_path",
                "attributes",
                "pricing",
            ):
                self.assertEqual(duplicated[field], original_before[field])
            self.assertEqual(duplicated["validation_errors"], validation_errors)
            for field in (
                "platform",
                "site",
                "language",
                "category_id",
                "category_path",
                "attributes",
            ):
                self.assertEqual(
                    duplicated_target[field],
                    original_before["target_sites"][0][field],
                )
            self.assertEqual(
                duplicated_target["validation_errors"],
                validation_errors,
            )
            self.assertEqual(
                duplicated_target["sites_to_sell"],
                [
                    {
                        "site_id": "MLM",
                        "logistic_type": "remote",
                        "price": "29.90",
                    }
                ],
            )
            self.assertNotEqual(duplicated["sku"], original_before["sku"])
            self.assertTrue(duplicated["sku"].startswith("ML-"))
            self.assertEqual(duplicated["upc"], "")
            self.assertEqual(duplicated["copy_operation_key"], "")
            self.assertEqual(duplicated["created_at"], "2026-08-30T09:00:00Z")
            self.assertEqual(duplicated["updated_at"], "2026-08-30T09:00:00Z")
            self.assertEqual(duplicated["status"], "images_ready")
            for field in ("publish_status",):
                self.assertEqual(duplicated[field], "")
                self.assertEqual(duplicated_target[field], "")
            for field in (
                "category_precheck",
                "last_precheck",
                "last_precheck_target",
                "last_publish_task",
            ):
                self.assertEqual(duplicated[field], {})
                self.assertEqual(duplicated_target[field], {})
            self.assertEqual(duplicated["publication"], {})
            self.assertEqual(duplicated_target["status"], "")
            self.assertEqual(
                get_context().db.load_draft_model(original_id),
                original_before,
            )
            self.assertTrue(
                result["productContext"]["raw"]["publish_preview"][
                    "mercadolibre"
                ]["ok"]
            )
            self.assertEqual(
                [item["draft_id"] for item in result["draftsIndex"]],
                [duplicated["draft_id"]],
            )
            self.assertEqual(result["message"], "草稿已复制。")

        self.with_temp_app(run)

    def test_duplicate_draft_reports_missing_identity_and_unknown_draft(self) -> None:
        def run(app_dir: Path) -> None:
            _result, missing_error, missing_status = (
                get_context().products.duplicate_draft_from_index("")
            )
            _result, unknown_error, unknown_status = (
                get_context().products.duplicate_draft_from_index(
                    "draft-does-not-exist"
                )
            )

            self.assertEqual(missing_status, 400)
            self.assertEqual(missing_error["error"], "draft_id 不能为空")
            self.assertEqual(unknown_status, 404)
            self.assertEqual(unknown_error["error"], "草稿不存在")

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
                    "status": "published",
                    "publish_status": "real_publish_success",
                    "last_precheck": {"ok": True, "forged": True},
                    "last_publish_task": {"job_id": "client-forged"},
                    "publication": {
                        "model": "user_products",
                        "siteless_user_product_id": "UP-CLIENT-FORGED",
                    },
                }
            }
            updated = get_context().products.save_product_profile(profile)

            reloaded_draft = get_context().db.load_draft_model(draft["draft_id"])
            self.assertEqual(updated["name"], "Profile boundary updated")
            self.assertEqual(reloaded_draft["title"], "Global title")
            self.assertEqual(reloaded_draft["description"], "Global description")
            self.assertEqual(reloaded_draft["status"], draft["status"])
            self.assertEqual(
                reloaded_draft["publish_status"],
                draft["publish_status"],
            )
            self.assertEqual(
                reloaded_draft.get("last_precheck", {}),
                draft.get("last_precheck", {}),
            )
            self.assertEqual(
                reloaded_draft.get("last_publish_task", {}),
                draft.get("last_publish_task", {}),
            )
            self.assertEqual(
                reloaded_draft.get("publication", {}),
                draft.get("publication", {}),
            )

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
                    "category_id": "yandex-category-1",
                    "description_category_id": "yandex-description-category-1",
                    "category_path": "Yandex > Home > Storage",
                    "attributes": {"BRAND": "Test Brand"},
                    "target_sites": [
                        {
                            "platform": "yandex",
                            "site": "global",
                            "category_id": "yandex-category-1",
                            "description_category_id": (
                                "yandex-description-category-1"
                            ),
                            "category_path": "Yandex > Home > Storage",
                            "attributes": {"BRAND": "Test Brand"},
                        },
                        {
                            "platform": "ozon",
                            "site": "global",
                            "category_id": "",
                            "description_category_id": "",
                            "category_path": "",
                            "attributes": {},
                        },
                    ],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(result["draft"]["title"], "Yandex independent title")
            self.assertEqual(result["draft"]["platforms"], ["yandex", "ozon"])
            self.assertEqual(
                result["draft"]["target_sites"][0]["category_id"],
                "yandex-category-1",
            )
            self.assertEqual(
                result["draft"]["target_sites"][0]["attributes"],
                {"BRAND": "Test Brand"},
            )
            ozon_target = result["draft"]["target_sites"][1]
            self.assertEqual(ozon_target["platform"], "ozon")
            for field in (
                "category_id",
                "description_category_id",
                "category_path",
            ):
                self.assertEqual(ozon_target[field], "")
            self.assertEqual(ozon_target["attributes"], {})
            # 已退役的类目 Schema 字段必须被保存入口显式拒绝。
            _rejected, rejected_error, rejected_status = (
                get_context().products.save_draft_detail(
                    {
                        "draft_id": yandex_draft_id,
                        "target_sites": [
                            {
                                "platform": "yandex",
                                "site": "global",
                                "category_attribute_schema": {"required": []},
                            }
                        ],
                    }
                )
            )
            self.assertEqual(rejected_status, 400)
            self.assertEqual(
                rejected_error["error_code"], "RETIRED_CATEGORY_SCHEMA_FIELD"
            )
            reloaded_yandex = get_context().db.load_draft_model(yandex_draft_id)
            self.assertEqual(reloaded_yandex["title"], "Yandex independent title")
            self.assertEqual(reloaded_yandex["platforms"], ["yandex", "ozon"])
            reloaded_ozon_target = reloaded_yandex["target_sites"][1]
            for field in (
                "category_id",
                "description_category_id",
                "category_path",
            ):
                self.assertEqual(reloaded_ozon_target[field], "")
            self.assertEqual(reloaded_ozon_target["attributes"], {})
            updated_record = next(item for item in get_context().db.list_draft_records(scope="all") if item["draft_id"] == yandex_draft_id)
            self.assertEqual(updated_record["platforms"], ["yandex", "ozon"])
            self.assertEqual(get_context().db.load_draft_model(ozon_draft_id)["title"], "Ozon original")

        self.with_temp_app(run)

    def test_save_draft_detail_scopes_target_owned_changes_to_one_market(
        self,
    ) -> None:
        def run(app_dir: Path) -> None:
            yandex_precheck = {
                "ok": True,
                "market": "yandex",
                "checked_at": "2026-08-31T08:00:00Z",
            }
            ozon_precheck = {
                "ok": True,
                "market": "ozon",
                "checked_at": "2026-08-31T08:01:00Z",
            }
            yandex_target = {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
                "listing_currency": "RUB",
                "category_id": "60996608",
                "description_category_id": "yandex-description-category",
                "category_path": "Yandex > Home > Storage",
                "attributes": {"BRAND": "Brand Y"},
                "category_precheck": yandex_precheck,
                "last_precheck": yandex_precheck,
                "last_precheck_target": {
                    "platform": "yandex",
                    "site": "global",
                },
                "publish_status": "ready",
                "status": "ready_to_publish",
            }
            ozon_target = {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "listing_currency": "RUB",
                "category_id": "95199",
                "description_category_id": "17028674",
                "category_path": "Ozon > Home > Storage",
                "attributes": {"85": "Brand O"},
                "category_precheck": ozon_precheck,
                "last_precheck": ozon_precheck,
                "last_precheck_target": {
                    "platform": "ozon",
                    "site": "global",
                },
                "publish_status": "ready",
                "status": "ready_to_publish",
            }
            product = sample_product(
                "Scoped target invalidation",
                "https://example.com/scoped-target-invalidation",
            )
            product["drafts"]["yandex"] = {
                "enabled": True,
                "platforms": ["yandex", "ozon"],
                "site": "global",
                "language": "ru-RU",
                "title": "Shared marketplace title",
                "description": "Shared marketplace description",
                "category_id": "60996608",
                "description_category_id": "yandex-description-category",
                "category_path": "Yandex > Home > Storage",
                "attributes": {"BRAND": "Brand Y"},
                "target_sites": [yandex_target, ozon_target],
                "category_precheck": yandex_precheck,
                "last_precheck": yandex_precheck,
                "last_precheck_target": yandex_target,
                "publish_status": "ready",
                "status": "ready_to_publish",
            }
            product["publish_preview"] = {
                "yandex": yandex_precheck,
                "ozon": ozon_precheck,
            }
            saved = get_context().products.save_product(product)
            original = deepcopy(saved["drafts"]["yandex"])
            changed_targets = deepcopy(original["target_sites"])
            changed_targets[1].update(
                {
                    "category_id": "95200",
                    "description_category_id": "17028675",
                    "category_path": "Ozon > Home > Organizers",
                    "attributes": {"85": "Brand O2"},
                }
            )

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": original["draft_id"],
                    # 编辑器会把当前 Ozon 投影同步到根；这些字段不能让兄弟市场失效。
                    "category_id": "95200",
                    "description_category_id": "17028675",
                    "category_path": "Ozon > Home > Organizers",
                    "attributes": {"85": "Brand O2"},
                    "target_sites": changed_targets,
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            updated = result["draft"]
            targets_by_platform = {
                target["platform"]: target
                for target in updated["target_sites"]
            }
            saved_yandex = targets_by_platform["yandex"]
            self.assertEqual(saved_yandex["status"], "ready_to_publish")
            self.assertEqual(saved_yandex["publish_status"], "ready")
            self.assertEqual(
                saved_yandex["category_precheck"],
                yandex_precheck,
            )
            self.assertEqual(saved_yandex["last_precheck"], yandex_precheck)
            saved_ozon = targets_by_platform["ozon"]
            self.assertEqual(saved_ozon["status"], "category_ready")
            self.assertEqual(saved_ozon["publish_status"], "")
            self.assertEqual(saved_ozon["category_precheck"], {})
            self.assertEqual(saved_ozon["last_precheck"], {})
            for field in (
                "category_id",
                "description_category_id",
                "category_path",
                "attributes",
            ):
                self.assertEqual(updated[field], saved_yandex[field])
            self.assertEqual(updated["platform"], "yandex")
            reloaded = get_context().db.load_draft_model(
                original["draft_id"]
            )
            for field in (
                "category_id",
                "description_category_id",
                "category_path",
                "attributes",
            ):
                self.assertEqual(reloaded[field], saved_yandex[field])
            self.assertIn(
                "yandex",
                result["productContext"]["raw"]["publish_preview"],
            )
            self.assertNotIn(
                "ozon",
                result["productContext"]["raw"]["publish_preview"],
            )

            # 恢复相同 ready 快照后，真正共享的 title 变化仍应让全部目标失效。
            get_context().db.upsert_draft_model(
                saved["product_id"],
                "yandex",
                original,
            )
            restored = get_context().db.load_draft_model(original["draft_id"])
            shared, shared_error, shared_status = (
                get_context().products.save_draft_detail(
                    {
                        "draft_id": restored["draft_id"],
                        "title": "Updated shared marketplace title",
                        "target_sites": restored["target_sites"],
                    }
                )
            )

            self.assertIsNone(shared_error)
            self.assertEqual(shared_status, 200)
            for target in shared["draft"]["target_sites"]:
                self.assertEqual(target["status"], "category_ready")
                self.assertEqual(target["publish_status"], "")
                self.assertEqual(target["category_precheck"], {})
                self.assertEqual(target["last_precheck"], {})

        self.with_temp_app(run)

    def test_save_draft_detail_invalidates_changed_cbt_sales_targets(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "CBT target invalidation",
                "https://example.com/cbt-target-invalidation",
            )
            publish_task = {
                "item_id": "CBT123456",
                "permalink": "https://global-selling.example/CBT123456",
            }
            stale_precheck = {
                "ok": True,
                "checked_at": "2026-08-24T08:00:00Z",
            }
            target = {
                "platform": "mercadolibre",
                "site": "CBT",
                "language": "es",
                "listing_currency": "USD",
                "sites_to_sell": [
                    {"site_id": "MLC", "logistic_type": "remote"},
                    {"site_id": "MLM", "logistic_type": "remote"},
                ],
                "category_id": "CBT1000",
                "attributes": {"BRAND": "BrandX"},
                "validation_errors": [{"code": "OLD_WARNING"}],
                "last_precheck": stale_precheck,
                "last_precheck_target": {
                    "sites_to_sell": [
                        {"site_id": "MLC", "logistic_type": "remote"},
                        {"site_id": "MLM", "logistic_type": "remote"},
                    ]
                },
                "publish_status": "real_publish_success",
                "status": "published",
                "last_publish_task": publish_task,
            }
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "site": "CBT",
                "language": "es",
                "title": "Global item",
                "description": "Global item description",
                "category_id": "CBT1000",
                "attributes": {"BRAND": "BrandX"},
                "stock": "5",
                "pricing": {
                    **pricing_targets(
                        "mercadolibre",
                        "CBT",
                        "USD",
                        "29.99",
                    ),
                    "updated_at": "2026-08-24T08:00:00Z",
                },
                "target_sites": [target],
                "validation_errors": [{"code": "OLD_WARNING"}],
                "last_precheck": stale_precheck,
                "last_precheck_target": target,
                "publish_status": "real_publish_success",
                "status": "published",
                "last_publish_task": publish_task,
            }
            product["publish_preview"] = {
                "mercadolibre": stale_precheck,
            }
            saved = get_context().products.save_product(product)
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]

            # 顺序、大小写和字段别名变化不构成销售目标变化。
            equivalent_target = dict(
                saved["drafts"]["mercadolibre"]["target_sites"][0]
            )
            equivalent_target["sitesToSell"] = [
                {"siteId": "mlm", "logisticType": "REMOTE"},
                {"siteId": "mlc", "logisticType": "remote"},
            ]
            equivalent_target.pop("sites_to_sell", None)
            equivalent, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": draft_id,
                    "target_sites": [equivalent_target],
                }
            )
            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(
                equivalent["draft"]["publish_status"],
                "real_publish_success",
            )
            self.assertIn(
                "mercadolibre",
                equivalent["productContext"]["raw"]["publish_preview"],
            )

            # 核价应用会把每个 operation 的派生 price/net_proceeds 写回草稿；
            # 金额变化不是销售 operation 变化，不能反过来清掉刚保存的核价。
            pricing_before = equivalent["draft"]["pricing"]
            amount_changed_target = dict(
                equivalent["draft"]["target_sites"][0]
            )
            amount_changed_target["sitesToSell"] = [
                {
                    "siteId": "mlm",
                    "logisticType": "REMOTE",
                    "netProceeds": "19.00",
                },
                {
                    "siteId": "mlc",
                    "logisticType": "remote",
                    "price": "31.00",
                },
            ]
            amount_changed_target.pop("sites_to_sell", None)
            amount_changed, error, status = (
                get_context().products.save_draft_detail(
                    {
                        "draft_id": draft_id,
                        "target_sites": [amount_changed_target],
                    }
                )
            )
            self.assertIsNone(error)
            self.assertEqual(status, 200)
            self.assertEqual(amount_changed["draft"]["pricing"], pricing_before)

            changed_target = dict(
                amount_changed["draft"]["target_sites"][0]
            )
            changed_target["sites_to_sell"] = [
                {"site_id": "MCO", "logistic_type": "remote"}
            ]
            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": draft_id,
                    # 模拟不可信写入方继续回传旧的 ready/success 字段。
                    "status": "published",
                    "publish_status": "real_publish_success",
                    "validation_errors": [{"code": "OLD_WARNING"}],
                    "last_precheck": stale_precheck,
                    "last_precheck_target": target,
                    "target_sites": [changed_target],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            draft = result["draft"]
            saved_target = draft["target_sites"][0]
            self.assertEqual(draft["status"], "category_ready")
            self.assertEqual(draft["publish_status"], "")
            self.assertEqual(draft["validation_errors"], [])
            self.assertEqual(draft["last_precheck"], {})
            self.assertEqual(draft["last_precheck_target"], {})
            self.assertEqual(draft["last_publish_task"], publish_task)
            self.assertEqual(draft["pricing"]["targets"], {})
            self.assertEqual(draft["pricing"]["updated_at"], "")
            self.assertEqual(saved_target["status"], "category_ready")
            self.assertEqual(saved_target["publish_status"], "")
            self.assertEqual(saved_target["validation_errors"], [])
            self.assertEqual(saved_target["last_precheck"], {})
            self.assertEqual(saved_target["last_precheck_target"], {})
            self.assertEqual(saved_target["last_publish_task"], publish_task)
            self.assertNotIn(
                "mercadolibre",
                result["productContext"]["raw"]["publish_preview"],
            )
            product_index = next(
                item
                for item in result["productsIndex"]
                if item["product_id"] == saved["product_id"]
            )
            self.assertEqual(product_index["workflow_status"], "category_ready")
            self.assertEqual(product_index["precheck_status"], "pending")
            self.assertEqual(product_index["publish_status"], "not_ready")

        self.with_temp_app(run)

    def test_save_draft_detail_invalidates_precheck_for_publish_content_edits(
        self,
    ) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "Mercado content invalidation",
                "https://example.com/mercado-content-invalidation",
            )
            publish_task = {
                "item_id": "CBT123456",
                "permalink": "https://global-selling.example/CBT123456",
            }
            stale_precheck = {
                "ok": True,
                "checked_at": "2026-08-28T08:00:00Z",
            }
            pricing = {
                **pricing_targets(
                    "mercadolibre",
                    "CBT",
                    "USD",
                    "29.99",
                ),
                "updated_at": "2026-08-28T08:00:00Z",
            }
            target = {
                "platform": "mercadolibre",
                "site": "CBT",
                "language": "es",
                "listing_currency": "USD",
                "sites_to_sell": [
                    {"site_id": "MLM", "logistic_type": "remote"},
                ],
                "category_id": "CBT1000",
                "attributes": {"BRAND": "BrandX", "MODEL": "X1"},
                "validation_errors": [{"code": "OLD_WARNING"}],
                "category_precheck": stale_precheck,
                "last_precheck": stale_precheck,
                "last_precheck_target": {"platform": "mercadolibre", "site": "CBT"},
                "publish_status": "ready",
                "status": "ready_to_publish",
                "last_publish_task": publish_task,
            }
            draft = product["drafts"]["mercadolibre"]
            draft.update(
                {
                    "enabled": True,
                    "site": "CBT",
                    "language": "es",
                    "global_title": "Portable fan X1",
                    "title": "Ventilador portátil X1",
                    "description": "Descripción",
                    "category_id": "CBT1000",
                    "attributes": {"BRAND": "BrandX", "MODEL": "X1"},
                    "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
                    "package_dimensions": {
                        "length_cm": "10",
                        "width_cm": "8",
                        "height_cm": "12",
                        "weight_kg": "0.2",
                    },
                    "sale_terms": {"warranty": "30 days"},
                    "stock": "5",
                    "pricing": pricing,
                    "target_sites": [target],
                    "validation_errors": [{"code": "OLD_WARNING"}],
                    "category_precheck": stale_precheck,
                    "last_precheck": stale_precheck,
                    "last_precheck_target": target,
                    "publish_status": "ready",
                    "status": "ready_to_publish",
                    "last_publish_task": publish_task,
                }
            )
            product["publish_preview"] = {"mercadolibre": stale_precheck}
            saved = get_context().products.save_product(product)
            saved_draft = saved["drafts"]["mercadolibre"]
            saved_target = dict(saved_draft["target_sites"][0])
            saved_target["attributes"] = {
                "BRAND": "BrandX",
                "MODEL": "X2",
                "VOLTAGE": "110/220V",
            }

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": saved_draft["draft_id"],
                    "global_title": "Portable fan X2",
                    "title": "Ventilador portátil X2",
                    "attributes": saved_target["attributes"],
                    "images": [
                        {"asset_id": "img-1", "role": "main", "order": 0},
                        {"asset_id": "img-2", "role": "gallery", "order": 1},
                    ],
                    "package_dimensions": {
                        "length_cm": "11",
                        "width_cm": "8",
                        "height_cm": "12",
                        "weight_kg": "0.2",
                    },
                    "sale_terms": {"warranty": "90 days"},
                    # 模拟前端把旧成功状态一并回传；保存边界不能信任它们。
                    "validation_errors": [{"code": "OLD_WARNING"}],
                    "category_precheck": stale_precheck,
                    "last_precheck": stale_precheck,
                    "last_precheck_target": target,
                    "publish_status": "ready",
                    "status": "ready_to_publish",
                    "target_sites": [saved_target],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            updated = result["draft"]
            updated_target = updated["target_sites"][0]
            for item in (updated, updated_target):
                self.assertEqual(item["status"], "category_ready")
                self.assertEqual(item["publish_status"], "")
                self.assertEqual(item["validation_errors"], [])
                self.assertEqual(item["category_precheck"], {})
                self.assertEqual(item["last_precheck"], {})
                self.assertEqual(item["last_precheck_target"], {})
                self.assertEqual(item["last_publish_task"], publish_task)
            self.assertEqual(updated["pricing"], saved_draft["pricing"])
            self.assertEqual(updated["attributes"]["MODEL"], "X2")
            self.assertEqual(updated_target["attributes"]["MODEL"], "X2")
            self.assertNotIn(
                "mercadolibre",
                result["productContext"]["raw"]["publish_preview"],
            )

        self.with_temp_app(run)

    def test_save_draft_detail_invalidates_removed_publish_target(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "Removed target invalidation",
                "https://example.com/removed-target-invalidation",
            )
            stale_precheck = {
                "ok": True,
                "checked_at": "2026-08-28T08:00:00Z",
            }
            mercado_target = {
                "platform": "mercadolibre",
                "site": "CBT",
                "language": "es",
                "listing_currency": "USD",
                "category_id": "CBT1000",
                "attributes": {"VOLTAGE": "110/220V"},
                "sites_to_sell": [
                    {"site_id": "MLM", "logistic_type": "remote"},
                ],
                "status": "ready_to_publish",
                "publish_status": "ready",
                "last_precheck": stale_precheck,
            }
            ozon_target = {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "listing_currency": "RUB",
                "category_id": "170000_970000",
                "attributes": {"85": "BrandX"},
                "status": "ready_to_publish",
                "publish_status": "ready",
                "last_precheck": stale_precheck,
            }
            draft = product["drafts"]["mercadolibre"]
            draft.update(
                {
                    "platforms": ["mercadolibre", "ozon"],
                    "site": "CBT",
                    "category_id": "CBT1000",
                    "attributes": {"VOLTAGE": "110/220V"},
                    "target_sites": [mercado_target, ozon_target],
                    "status": "ready_to_publish",
                    "publish_status": "ready",
                    "last_precheck": stale_precheck,
                }
            )
            product["publish_preview"] = {
                "mercadolibre": stale_precheck,
                "ozon": stale_precheck,
            }
            saved = get_context().products.save_product(product)
            saved_draft = saved["drafts"]["mercadolibre"]

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": saved_draft["draft_id"],
                    "target_sites": [saved_draft["target_sites"][0]],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            updated = result["draft"]
            self.assertEqual(updated["platforms"], ["mercadolibre"])
            self.assertEqual(updated["status"], "category_ready")
            self.assertEqual(updated["publish_status"], "")
            self.assertEqual(updated["last_precheck"], {})
            self.assertEqual(updated["target_sites"][0]["status"], "category_ready")
            self.assertEqual(updated["target_sites"][0]["publish_status"], "")
            self.assertNotIn(
                "mercadolibre",
                result["productContext"]["raw"]["publish_preview"],
            )
            self.assertNotIn(
                "ozon",
                result["productContext"]["raw"]["publish_preview"],
            )

        self.with_temp_app(run)

    def test_save_draft_detail_preserves_pending_attribute_reviews(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "Review preservation",
                "https://example.com/review-preservation",
            )
            reviews = [
                "VOLTAGE",
                {
                    "code": "NEED_REVIEW_ATTRIBUTES",
                    "field": "attributes.MODEL",
                    "message": "请人工确认",
                },
                {"code": "OLD_PRECHECK", "field": "title"},
            ]
            target = {
                "platform": "mercadolibre",
                "site": "CBT",
                "language": "es",
                "listing_currency": "USD",
                "category_id": "CBT1000",
                "attributes": {"VOLTAGE": "110/220V"},
                "sites_to_sell": [
                    {"site_id": "MLM", "logistic_type": "remote"},
                ],
                "validation_errors": deepcopy(reviews),
                "status": "ready_to_publish",
                "publish_status": "ready",
                "last_precheck": {"ok": True},
            }
            draft = product["drafts"]["mercadolibre"]
            draft.update(
                {
                    "site": "CBT",
                    "title": "Old title",
                    "category_id": "CBT1000",
                    "attributes": {"VOLTAGE": "110/220V"},
                    "target_sites": [target],
                    "validation_errors": deepcopy(reviews),
                    "status": "ready_to_publish",
                    "publish_status": "ready",
                    "last_precheck": {"ok": True},
                }
            )
            saved = get_context().products.save_product(product)

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": saved["drafts"]["mercadolibre"]["draft_id"],
                    "title": "New title",
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            expected_reviews = reviews[:2]
            self.assertEqual(result["draft"]["validation_errors"], expected_reviews)
            self.assertEqual(
                result["draft"]["target_sites"][0]["validation_errors"],
                expected_reviews,
            )
            self.assertEqual(result["draft"]["last_precheck"], {})

        self.with_temp_app(run)

    def test_save_draft_detail_discards_removed_cbt_marketplace_titles_field(
        self,
    ) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product(
                "CBT title invalidation",
                "https://example.com/cbt-title-invalidation",
            )
            publish_task = {
                "item_id": "CBT123456",
                "permalink": "https://global-selling.example/CBT123456",
            }
            stale_precheck = {
                "ok": True,
                "checked_at": "2026-08-27T08:00:00Z",
            }
            target = {
                "platform": "mercadolibre",
                "site": "CBT",
                "language": "es",
                "listing_currency": "USD",
                "sites_to_sell": [
                    {"site_id": "MLM", "logistic_type": "remote"},
                ],
                "marketplace_titles": {"MLM": "Producto original"},
                "category_id": "CBT1000",
                "attributes": {"BRAND": "BrandX"},
                "validation_errors": [{"code": "OLD_WARNING"}],
                "last_precheck": stale_precheck,
                "last_precheck_target": {
                    "platform": "mercadolibre",
                    "site": "CBT",
                },
                "publish_status": "real_publish_success",
                "status": "published",
                "last_publish_task": publish_task,
            }
            product["drafts"]["mercadolibre"] = {
                "enabled": True,
                "site": "CBT",
                "language": "es",
                "title": "Global item",
                "description": "Global item description",
                "category_id": "CBT1000",
                "attributes": {"BRAND": "BrandX"},
                "stock": "5",
                "pricing": {
                    **pricing_targets(
                        "mercadolibre",
                        "CBT",
                        "USD",
                        "29.99",
                    ),
                    "updated_at": "2026-08-27T08:00:00Z",
                },
                "target_sites": [target],
                "validation_errors": [{"code": "OLD_WARNING"}],
                "last_precheck": stale_precheck,
                "last_precheck_target": target,
                "publish_status": "real_publish_success",
                "status": "published",
                "last_publish_task": publish_task,
            }
            product["publish_preview"] = {
                "mercadolibre": stale_precheck,
            }
            saved = get_context().products.save_product(product)
            saved_draft = saved["drafts"]["mercadolibre"]
            draft_id = saved_draft["draft_id"]
            pricing_before = saved_draft["pricing"]
            changed_target = dict(saved_draft["target_sites"][0])
            changed_target["marketplace_titles"] = {
                "MLM": "Producto actualizado",
            }

            result, error, status = get_context().products.save_draft_detail(
                {
                    "draft_id": draft_id,
                    "target_sites": [changed_target],
                }
            )

            self.assertIsNone(error)
            self.assertEqual(status, 200)
            draft = result["draft"]
            saved_target = draft["target_sites"][0]
            self.assertNotIn("marketplace_titles", saved_target)
            self.assertEqual(draft["publish_status"], "real_publish_success")
            self.assertEqual(saved_target["publish_status"], "real_publish_success")
            self.assertEqual(draft["last_publish_task"], publish_task)
            self.assertEqual(saved_target["last_publish_task"], publish_task)
            self.assertEqual(draft["pricing"], pricing_before)
            self.assertIn(
                "mercadolibre",
                result["productContext"]["raw"]["publish_preview"],
            )

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
            "target_sites": [mercadolibre_cbt_target()],
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
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX"},
                "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
                "stock": "5",
            }
        )
        product["drafts"]["mercadolibre"]["target_sites"][0].update(
            {
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX"},
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
            summary = publish_helpers._required_attribute_summary(
                product, "mercadolibre", attrs["category"]
            )

            self.assertEqual(results[0]["category_id"], "MLM999")
            self.assertEqual(results[0]["path"], "Necklaces")
            self.assertEqual(attrs["required"][0]["id"], "BRAND")
            self.assertEqual(attrs["source"], "mercadolibre_live")
            self.assertEqual(summary["required_count"], 1)
            # Mercado BRAND 的唯一事实源是草稿根字段；sample_product 已有品牌，
            # 即使 attributes 不保存重复 BRAND，也应视为已填写。
            self.assertEqual(summary["filled_count"], 1)
            self.assertTrue(
                all(
                    (not call.args[1:] or call.args[1] is None)
                    and call.kwargs == {}
                    for call in http_json_mock.call_args_list
                )
            )

        self.with_temp_app(run)

    def test_cbt_category_search_uses_saved_store_token_as_bearer(self) -> None:
        def run(app_dir: Path) -> None:
            del app_dir
            get_context().config.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "123",
                        "app_secret": "secret",
                        "redirect_uri": "https://example.com/callback",
                        "site_id": "CBT",
                        "access_token": "saved-token",
                        "refresh_token": "saved-refresh-token",
                    }
                }
            )
            requests: list[tuple[str, dict[str, object]]] = []

            def fake_request_json(url: str, **kwargs: object) -> object:
                requests.append((url, kwargs))
                return [
                    {
                        "domain_id": "CBT-WOODWORKING_TOOLS",
                        "domain_name": "Woodworking Tools",
                        "category_id": "CBT407134",
                        "category_name": "Other",
                    }
                ]

            with patch.object(
                category_providers,
                "get_mercadolibre_access_token",
                return_value="saved-token",
            ), patch.object(
                category_refresh.http_client,
                "request_json",
                side_effect=fake_request_json,
            ):
                results = category_store.search_categories_live(
                    "mercadolibre",
                    query="woodworking tool",
                    site="CBT",
                    limit=5,
                    timeout_seconds=3,
                )

            self.assertEqual(results[0]["category_id"], "CBT407134")
            self.assertEqual(
                requests[0][0],
                "https://api.mercadolibre.com/marketplace/domain_discovery/search?q=woodworking%20tool&limit=5",
            )
            self.assertEqual(
                requests[0][1]["headers"]["Authorization"],
                "Bearer saved-token",
            )

        self.with_temp_app(run)

    def test_cbt_category_search_classifies_missing_saved_token(self) -> None:
        def run(app_dir: Path) -> None:
            del app_dir
            get_context().config.save_store_config(
                {
                    "mercadolibre": {
                        "app_id": "123",
                        "app_secret": "secret",
                        "redirect_uri": "https://example.com/callback",
                        "site_id": "CBT",
                    }
                }
            )

            with patch.object(
                category_refresh.http_client,
                "request_json",
                side_effect=AssertionError("缺少 Token 时不应发送请求"),
            ), self.assertRaises(category_searchers.CategorySearchError) as raised:
                category_store.search_categories_live(
                    "mercadolibre",
                    query="woodworking tool",
                    site="CBT",
                    limit=5,
                )

            self.assertEqual(raised.exception.code, "CATEGORY_CREDENTIALS_MISSING")
            self.assertFalse(raised.exception.retryable)

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
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
                "target_sites": [{
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "listing_currency": "USD",
                    "category_id": "CBT123",
                    "attributes": {
                        "BRAND": "BrandX",
                        "MODEL": "ModelY",
                    },
                    "sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                }],
                "stock": "5",
                "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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

    def test_mercadolibre_regional_first_level_draft_is_rejected(self) -> None:
        product = sample_product("Draft payload source title", "https://example.com/draft-payload")
        product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "category_id": "MLM455865",
            "site": "MLM",
            "target_sites": [{"platform": "mercadolibre", "site": "MLM", "listing_currency": "MXN"}],
        }

        with self.assertRaisesRegex(ValueError, "只允许 CBT/Siteless 一级草稿"):
            get_context().products.save_product(product)

    def test_mercadolibre_publish_payload_uses_draft_target_site_over_store_config(self) -> None:
        # 发布币种唯一事实源：显式创建 ready 店铺配置（CBT 店铺 USD）。
        store_fingerprint = seed_store_currency(
            "mercadolibre",
            "USD",
            identity={
                "user_id": "99",
                "account_site_id": "CBT",
                "marketplace_bindings": [
                    {
                        "site_id": "MLM",
                        "logistic_type": "remote",
                        "pricing_model": "price",
                        "user_product": True,
                    }
                ],
                "user_product_seller": True,
            },
        )
        product = sample_product("Draft CBT payload", "https://example.com/draft-cbt-payload")
        product["source"]["image_pool"][0].update(
            {
                "url": "https://example.com/draft-cbt-main.jpg",
                "preview_url": "https://example.com/draft-cbt-main.jpg",
                "platforms": ["mercadolibre"],
                "is_main": True,
                "selected": True,
                "status": "ready",
                "platform_picture_id": "PIC-CBT-1",
            }
        )
        product["drafts"]["mercadolibre"] = {
            "enabled": True,
            "site": "CBT",
            "title": "Draft CBT title",
            "description": "Draft CBT description",
            "brand": "DraftBrand",
            "model": "DraftModel",
            "images": [{"asset_id": "img_1", "role": "main", "order": 0}],
            "target_sites": [
                {
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "listing_currency": "USD",
                    "category_id": "CBT457856",
                    "attributes": {
                        "BRAND": "DraftBrand",
                        "MODEL": "DraftModel",
                    },
                    "sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                }
            ],
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "18.00", store_fingerprint),
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
            "mercadolibre": {
                "site_id": "CBT",
                "account_site_id": "CBT",
                "category_id": "WRONG-CATEGORY",
                "access_token": "token",
                "listing_model": "user_products",
                "user_product_seller": True,
                "marketplace_bindings": [
                    {
                        "site_id": "MLM",
                        "logistic_type": "remote",
                        "pricing_model": "price",
                        "user_product": True,
                    }
                ],
            },
            "listing": {
                "currency_id": "USD",
                "price": "0",
                "mercadolibre_price": "0",
                "stock": "0",
                "sku": "CONFIG-SKU",
            },
        }

        from erp_web.runtime_units.publish_context import PreparedPublishContext
        from tests.publish_category_support import definition_from_record

        category_definition = definition_from_record(
            {
                "platform": "mercadolibre",
                "site": "CBT",
                "category_id": "CBT457856",
                "attributes": {
                    "required": [
                        {"id": "BRAND", "required": True},
                        {"id": "MODEL", "required": True},
                        {
                            "id": "PACKAGE_LENGTH",
                            "required": True,
                            "value_type": "number_unit",
                            "unit_options": ["cm"],
                        },
                        {
                            "id": "PACKAGE_WIDTH",
                            "required": True,
                            "value_type": "number_unit",
                            "unit_options": ["cm"],
                        },
                        {
                            "id": "PACKAGE_HEIGHT",
                            "required": True,
                            "value_type": "number_unit",
                            "unit_options": ["cm"],
                        },
                        {
                            "id": "PACKAGE_WEIGHT",
                            "required": True,
                            "value_type": "number_unit",
                            "unit_options": ["g"],
                        },
                    ],
                    "optional": [],
                },
            }
        )

        payload = publish_adapter.require_publishing_adapter(
            "mercadolibre"
        ).build_payload(
            PreparedPublishContext(
                product=product,
                draft=product["drafts"]["mercadolibre"],
                target=product["drafts"]["mercadolibre"]["target_sites"][0],
                category_definition=category_definition,
                platform="mercadolibre",
            ),
            config,
        )
        attributes = {
            item["id"]: item.get("value_name") or item.get("values")
            for item in payload["attributes"]
        }

        self.assertEqual(payload["family_name"], "Draft CBT title")
        self.assertNotIn("title", payload)
        self.assertNotIn("variations", payload)
        self.assertEqual(payload["category_id"], "CBT457856")
        self.assertEqual(payload["currency_id"], "USD")
        self.assertEqual(payload["price"], 18.0)
        self.assertEqual(payload["pictures"], [{"id": "PIC-CBT-1"}])
        self.assertEqual(payload["sites_to_sell"][0]["site_id"], "MLM")
        self.assertEqual(attributes["PACKAGE_LENGTH"], "11 cm")
        self.assertNotIn("SELLER_PACKAGE_LENGTH", attributes)

    def test_claiming_published_product_creates_a_new_active_draft(self) -> None:
        def run(app_dir: Path) -> None:
            product = sample_product("Published item", "https://example.com/published-item")
            product["drafts"]["mercadolibre"].update(
                {
                    "enabled": True,
                    "title": "Published title",
                    "description": "Published description",
                    "category_id": "CBT123",
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

    def test_mercadolibre_user_products_uses_local_publication_index(self) -> None:
        def run(app_dir: Path) -> None:
            saved = _save_mercadolibre_user_product("U123")
            with patch.object(publisher, "request_json") as request_json:
                result = publish_mercadolibre.mercadolibre_user_products("active")

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["items"]), 1)
            item = result["items"][0]
            self.assertEqual(item["product_id"], saved["product_id"])
            self.assertEqual(item["siteless_user_product_id"], "U123")
            self.assertEqual(item["markets"][0]["item_id"], "MLM100")
            self.assertEqual(result["pagination"]["page"], 1)
            self.assertEqual(result["pagination"]["total"], 1)
            request_json.assert_not_called()

        self.with_temp_app(run)

    def test_mercadolibre_user_products_refreshes_known_mapping(self) -> None:
        def run(app_dir: Path) -> None:
            siteless_id = "U3972144818"
            get_context().config.save_store_config(
                {"mercadolibre": {"user_id": "99", "site_id": "CBT"}}
            )
            saved = _save_mercadolibre_user_product(siteless_id)
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
            persisted_before_refresh = get_context().db.load_draft_model(
                draft_id
            )
            persisted_before_refresh["publication"]["markets"][0].update(
                {
                    "net_proceeds": "17.25",
                    "free_shipping": False,
                    "sale_terms": [
                        {
                            "id": "WARRANTY_TYPE",
                            "value_name": "No warranty",
                        }
                    ],
                    "last_operation": {
                        "status": "succeeded",
                        "updated_at": "2026-08-26T10:00:00Z",
                    },
                }
            )
            get_context().db.upsert_draft_model(
                str(saved["product_id"]),
                "mercadolibre",
                persisted_before_refresh,
            )
            calls: list[tuple[str, str]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url))
                return [
                    {
                        "item_id": "CBT4232488286",
                        "owner_id": 99,
                        "site_id": "CBT",
                        "user_product_id": "CBTU3972144818",
                        "siteless_user_product_id": "CBTU3972144818",
                        "site_items": [
                            {
                                "site_id": "MLM",
                                "item_id": "MLM3185408780",
                                "logistic_type": "remote",
                            }
                        ],
                    }
                ]

            with (
                patch.object(
                    publish_mercadolibre,
                    "get_mercadolibre_access_token",
                    return_value="token",
                ),
                patch.object(publisher, "request_json", side_effect=fake_request),
            ):
                result = publish_mercadolibre.mercadolibre_user_products(
                    "all",
                    refresh=True,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["refresh_scope"],
                "identity_mapping_only",
            )
            self.assertEqual(
                calls,
                [
                    (
                        "GET",
                        "https://api.mercadolibre.com/marketplace/user-products/U3972144818/mapping",
                    )
                ],
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["item_id"],
                "MLM3185408780",
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["status"],
                "active",
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["price"],
                19.99,
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["net_proceeds"],
                "17.25",
            )
            self.assertIs(
                result["items"][0]["markets"][0]["free_shipping"],
                False,
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["sale_terms"],
                [
                    {
                        "id": "WARRANTY_TYPE",
                        "value_name": "No warranty",
                    }
                ],
            )
            self.assertEqual(
                result["items"][0]["markets"][0]["last_operation"][
                    "status"
                ],
                "succeeded",
            )
            self.assertEqual(
                result["items"][0]["parent_item_id"],
                "CBT4232488286",
            )
            self.assertEqual(
                result["items"][0]["parent_user_product_id"],
                "CBTU3972144818",
            )
            persisted = get_context().db.load_draft_model(draft_id)
            self.assertEqual(
                persisted["publication"]["markets"][0]["item_id"],
                "MLM3185408780",
            )

        self.with_temp_app(run)

    def test_mercadolibre_mapping_refresh_rejects_untrusted_identity_shapes(self) -> None:
        valid = {
            "item_id": "CBT200",
            "owner_id": 99,
            "site_id": "CBT",
            "user_product_id": "CBTU123",
            "siteless_user_product_id": "U123",
            "site_items": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM200",
                    "logistic_type": "remote",
                }
            ],
        }
        cases = [
            (
                "not_array",
                dict(valid),
                "MERCADOLIBRE_MAPPING_RESPONSE_INVALID",
            ),
            (
                "empty",
                [],
                "MERCADOLIBRE_MAPPING_CARDINALITY_INVALID",
            ),
            (
                "multiple",
                [dict(valid), dict(valid)],
                "MERCADOLIBRE_MAPPING_CARDINALITY_INVALID",
            ),
            (
                "missing_remote_id",
                [{key: value for key, value in valid.items() if key != "siteless_user_product_id"}],
                "MERCADOLIBRE_MAPPING_SITELESS_ID_MISSING",
            ),
            (
                "wrong_remote_id",
                [{**valid, "siteless_user_product_id": "U999"}],
                "MERCADOLIBRE_MAPPING_SITELESS_ID_MISMATCH",
            ),
            (
                "missing_owner",
                [{key: value for key, value in valid.items() if key != "owner_id"}],
                "MERCADOLIBRE_MAPPING_OWNER_ID_MISSING",
            ),
            (
                "wrong_owner",
                [{**valid, "owner_id": 100}],
                "MERCADOLIBRE_MAPPING_OWNER_ID_MISMATCH",
            ),
        ]

        for name, response, expected_code in cases:
            with self.subTest(name=name):
                def run(app_dir: Path) -> None:
                    get_context().config.save_store_config(
                        {"mercadolibre": {"user_id": "99", "site_id": "CBT"}}
                    )
                    saved = _save_mercadolibre_user_product("U123")
                    draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
                    with (
                        patch.object(
                            publish_mercadolibre,
                            "get_mercadolibre_access_token",
                            return_value="token",
                        ),
                        patch.object(
                            publisher,
                            "request_json",
                            return_value=response,
                        ),
                    ):
                        result = publish_mercadolibre.mercadolibre_user_products(
                            "all",
                            refresh=True,
                        )

                    self.assertTrue(result["ok"])
                    self.assertEqual(result["refresh_scope"], "identity_mapping_only")
                    self.assertEqual(len(result["refresh_errors"]), 1)
                    self.assertIn(
                        expected_code,
                        result["refresh_errors"][0]["error"],
                    )
                    persisted = get_context().db.load_draft_model(draft_id)
                    publication = persisted["publication"]
                    self.assertEqual(publication["parent_item_id"], "CBT100")
                    self.assertEqual(
                        publication["parent_user_product_id"],
                        "CBTU123",
                    )
                    self.assertEqual(
                        publication["markets"][0]["item_id"],
                        "MLM100",
                    )

                self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_rejects_account_mismatch(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {"mercadolibre": {"user_id": "other-account"}}
            )
            saved = _save_mercadolibre_user_product("U123")
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]

            with (
                patch.object(
                    publish_mercadolibre,
                    "get_mercadolibre_access_token",
                    return_value="token",
                ),
                patch.object(publisher, "request_json") as request_json,
            ):
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "U123"
                )

            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error_code"],
                "MERCADOLIBRE_PUBLICATION_ACCOUNT_MISMATCH",
            )
            request_json.assert_not_called()
            persisted = get_context().db.load_draft_model(draft_id)
            self.assertEqual(persisted["publication"]["status"], "active")

        self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_rejects_partial_206_errors(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {"mercadolibre": {"user_id": "99"}}
            )
            saved = _save_mercadolibre_user_product("U123")
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]

            with (
                patch.object(
                    publish_mercadolibre,
                    "get_mercadolibre_access_token",
                    return_value="token",
                ),
                patch.object(
                    publisher,
                    "request_json",
                    return_value={
                        "id": "U123",
                        "errors": [
                            {
                                "code": "partial_update",
                                "message": "one marketplace could not be paused",
                            }
                        ],
                    },
                ) as request_json,
            ):
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "U123"
                )

            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error_code"],
                "MERCADOLIBRE_USER_PRODUCT_PAUSE_FAILED",
            )
            request_json.assert_called_once()
            persisted = get_context().db.load_draft_model(draft_id)
            self.assertEqual(persisted["publication"]["status"], "active")
            self.assertEqual(
                persisted["publication"]["markets"][0]["status"],
                "active",
            )
            self.assertTrue(
                persisted["publication"]["markets"][0]["error"]
            )
            self.assertEqual(
                persisted["publication"]["markets"][0]["last_operation"]["status"],
                "failed",
            )

        self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_persists_partial_market_facts(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {"mercadolibre": {"user_id": "99"}}
            )
            saved = _save_mercadolibre_user_product(
                "U123",
                with_second_market=True,
            )
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
            response = {
                "id": "CBTU123",
                "errors": [
                    {
                        "code": "partial_update",
                        "message": "one marketplace could not be paused",
                    }
                ],
                "listing_sites": [
                    {
                        "id": "MLM100",
                        "success": True,
                        "errors": None,
                    },
                    {
                        "id": "MLB100",
                        "success": False,
                        "errors": [
                            {
                                "code": "market_update_failed",
                                "message": "MLB could not be paused",
                            }
                        ],
                    },
                ],
            }

            with (
                patch.object(
                    publish_mercadolibre,
                    "get_mercadolibre_access_token",
                    return_value="token",
                ),
                patch.object(
                    publisher,
                    "request_json",
                    return_value=response,
                ),
            ):
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "U123"
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["partial"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["confirmed_market_count"], 1)
            self.assertEqual(result["unconfirmed_market_count"], 1)
            persisted = get_context().db.load_draft_model(draft_id)
            publication = persisted["publication"]
            self.assertEqual(publication["status"], "partial")
            markets = {
                market["site_id"]: market
                for market in publication["markets"]
            }
            self.assertEqual(markets["MLM"]["status"], "paused")
            self.assertNotIn("error", markets["MLM"])
            self.assertEqual(
                markets["MLM"]["last_operation"]["status"],
                "succeeded",
            )
            self.assertEqual(markets["MLB"]["status"], "active")
            self.assertTrue(markets["MLB"]["error"])
            self.assertEqual(
                markets["MLB"]["last_operation"]["status"],
                "failed",
            )

        self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_rejects_nested_partial_errors(self) -> None:
        for response_field in ("listing_sites", "site_items", "variants"):
            with self.subTest(response_field=response_field):
                def run(app_dir: Path) -> None:
                    get_context().config.save_store_config(
                        {"mercadolibre": {"user_id": "99"}}
                    )
                    saved = _save_mercadolibre_user_product("U123")
                    draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
                    response = {
                        "id": "U123",
                        response_field: [
                            {
                                "id": "MLM100",
                                "success": False,
                                "errors": [
                                    {
                                        "code": "partial_update",
                                        "message": "market update failed",
                                    }
                                ],
                            }
                        ],
                    }

                    with (
                        patch.object(
                            publish_mercadolibre,
                            "get_mercadolibre_access_token",
                            return_value="token",
                        ),
                        patch.object(
                            publisher,
                            "request_json",
                            return_value=response,
                        ),
                    ):
                        result = (
                            publish_mercadolibre.mercadolibre_pause_user_product(
                                "U123"
                            )
                        )

                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["error_code"],
                        "MERCADOLIBRE_USER_PRODUCT_PAUSE_FAILED",
                    )
                    persisted = get_context().db.load_draft_model(draft_id)
                    self.assertEqual(
                        persisted["publication"]["status"],
                        "active",
                    )
                    self.assertEqual(
                        persisted["publication"]["markets"][0]["status"],
                        "active",
                    )
                    self.assertTrue(
                        persisted["publication"]["markets"][0]["error"]
                    )
                    self.assertEqual(
                        persisted["publication"]["markets"][0]["last_operation"]["status"],
                        "failed",
                    )

                self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_network_and_5xx_are_outcome_unknown(self) -> None:
        failures = [
            PublishAdapterError(
                "MERCADOLIBRE_NETWORK",
                "connection reset",
                retryable=True,
            ),
            PublishAdapterError(
                "MERCADOLIBRE_SERVER_ERROR",
                "service unavailable",
                retryable=True,
                details={"http_status": 503},
            ),
        ]
        for failure in failures:
            with self.subTest(code=failure.code):
                def run(app_dir: Path) -> None:
                    get_context().config.save_store_config(
                        {"mercadolibre": {"user_id": "99"}}
                    )
                    saved = _save_mercadolibre_user_product("U123")
                    draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
                    with (
                        patch.object(
                            publish_mercadolibre,
                            "get_mercadolibre_access_token",
                            return_value="token",
                        ),
                        patch.object(
                            publisher,
                            "request_json",
                            side_effect=failure,
                        ),
                    ):
                        result = (
                            publish_mercadolibre.mercadolibre_pause_user_product(
                                "U123"
                            )
                        )

                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["error_code"],
                        "USER_PRODUCT_PAUSE_OUTCOME_UNKNOWN",
                    )
                    self.assertTrue(result["outcome_unknown"])
                    self.assertFalse(result["retryable"])
                    self.assertTrue(result["details"]["outcome_unknown"])
                    persisted = get_context().db.load_draft_model(draft_id)
                    self.assertEqual(
                        persisted["publication"]["status"],
                        "active",
                    )

                self.with_temp_app(run)

    def test_mercadolibre_pause_blocks_active_or_unknown_publish_job(self) -> None:
        for job_status in ("running", "outcome_unknown"):
            with self.subTest(job_status=job_status):
                def run(app_dir: Path) -> None:
                    get_context().config.save_store_config(
                        {"mercadolibre": {"user_id": "99"}}
                    )
                    saved = _save_mercadolibre_user_product("U123")
                    draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
                    get_context().db.create_publish_job(
                        {
                            "job_id": f"job-{job_status}",
                            "idempotency_key": f"pause-lock:{job_status}",
                            "draft_id": draft_id,
                            "status": job_status,
                            "terminal_results_persisted": (
                                job_status == "outcome_unknown"
                            ),
                            "product": {"product_id": saved["product_id"]},
                            "platforms": {
                                "mercadolibre": {
                                    "draft_id": draft_id,
                                    "status": job_status,
                                    "stage": "publishing",
                                }
                            },
                        }
                    )

                    with (
                        patch.object(
                            publish_mercadolibre,
                            "get_mercadolibre_access_token",
                            return_value="token",
                        ),
                        patch.object(publisher, "request_json") as request_json,
                    ):
                        result = (
                            publish_mercadolibre.mercadolibre_pause_user_product(
                                "U123"
                            )
                        )

                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["error_code"],
                        "MERCADOLIBRE_USER_PRODUCT_PUBLISH_ACTIVE",
                    )
                    self.assertEqual(
                        result["active_job_status"],
                        job_status,
                    )
                    request_json.assert_not_called()

                self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_rejects_unknown_id(self) -> None:
        def run(app_dir: Path) -> None:
            with patch.object(publisher, "request_json") as request_json:
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "U999"
                )

            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error_code"],
                "MERCADOLIBRE_USER_PRODUCT_NOT_FOUND",
            )
            request_json.assert_not_called()

        self.with_temp_app(run)

    def test_mercadolibre_pause_does_not_treat_up_prefix_as_u_identity(self) -> None:
        def run(app_dir: Path) -> None:
            _save_mercadolibre_user_product("U123")
            with patch.object(publisher, "request_json") as request_json:
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "UP123"
                )

            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error_code"],
                "MERCADOLIBRE_SITELESS_USER_PRODUCT_ID_INVALID",
            )
            request_json.assert_not_called()

        self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_uses_siteless_endpoint(self) -> None:
        def run(app_dir: Path) -> None:
            get_context().config.save_store_config(
                {"mercadolibre": {"user_id": "99"}}
            )
            saved = _save_mercadolibre_user_product("U123")
            draft_id = saved["drafts"]["mercadolibre"]["draft_id"]
            calls: list[tuple[str, str, dict | list | None]] = []

            def fake_request(method: str, url: str, token: str = "", payload: dict | list | None = None, extra_headers: dict | None = None):
                calls.append((method, url, payload))
                return {"siteless_user_product_id": "CBTU123", "status": "paused"}

            with (
                patch.object(
                    publish_mercadolibre,
                    "get_mercadolibre_access_token",
                    return_value="token",
                ),
                patch.object(publisher, "request_json", side_effect=fake_request),
            ):
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "CBTU123"
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "paused")
            self.assertEqual(
                calls,
                [
                    (
                        "PUT",
                        "https://api.mercadolibre.com/global/user-products/U123",
                        {"status": "paused"},
                    )
                ],
            )
            persisted = get_context().db.load_draft_model(draft_id)
            self.assertEqual(persisted["publication"]["status"], "paused")
            self.assertEqual(
                persisted["publication"]["markets"][0]["status"],
                "paused",
            )

        self.with_temp_app(run)

    def test_mercadolibre_pause_user_product_is_idempotent_when_local_status_paused(self) -> None:
        def run(app_dir: Path) -> None:
            _save_mercadolibre_user_product("U123", status="paused")
            with patch.object(publisher, "request_json") as request_json:
                result = publish_mercadolibre.mercadolibre_pause_user_product(
                    "U123"
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "paused")
            request_json.assert_not_called()

        self.with_temp_app(run)

    def test_mercadolibre_user_products_filters_local_status(self) -> None:
        def run(app_dir: Path) -> None:
            _save_mercadolibre_user_product("U123", status="active")
            _save_mercadolibre_user_product("U124", status="partial")
            paused = publish_mercadolibre.mercadolibre_user_products("paused")
            active = publish_mercadolibre.mercadolibre_user_products("active")
            partial = publish_mercadolibre.mercadolibre_user_products(
                "partial"
            )

            self.assertTrue(paused["ok"])
            self.assertEqual(paused["items"], [])
            self.assertEqual(active["items"][0]["siteless_user_product_id"], "U123")
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(
                partial["items"][0]["siteless_user_product_id"],
                "U124",
            )

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
            "category_id": "CBT123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "target_sites": [{
                **mercadolibre_cbt_target(),
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            }],
            "stock": "5",
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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
            "category_id": "CBT123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "target_sites": [{
                **mercadolibre_cbt_target(),
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            }],
            "stock": "5",
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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
            "category_id": "CBT123",
            "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            "target_sites": [{
                **mercadolibre_cbt_target(),
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
            }],
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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
            "category_id": "CBT123",
            "attributes": {},
            "target_sites": [{
                **mercadolibre_cbt_target(),
                "category_id": "CBT123",
                "attributes": {},
            }],
            "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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
                "category_id": "CBT123",
                "attributes": {"BRAND": "BrandX", "MODEL": "ModelY"},
                "target_sites": [mercadolibre_cbt_target()],
                "stock": "5",
                "pricing": pricing_targets("mercadolibre", "CBT", "USD", "19.99"),
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
                        "site": str(saved["drafts"]["mercadolibre"].get("site") or "CBT"),
                        "product_id": str(saved["product_id"]),
                        "status": "success",
                        "stage": "finished",
                        "error": "",
                        "attempts": 1,
                        "created_at": "2026-05-30 12:00:00",
                        "updated_at": "2026-05-30 12:01:00",
                        "result": {
                            "ok": True,
                            "siteless_user_product_id": "U9001",
                            "publication": {
                                "model": "user_products",
                                "siteless_user_product_id": "U9001",
                                "status": "active",
                                "markets": [
                                    {
                                        "site_id": "MLM",
                                        "logistic_type": "remote",
                                        "item_id": "MLMITEM1",
                                        "status": "active",
                                    }
                                ],
                            },
                        },
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
            self.assertEqual(
                draft["publication"]["siteless_user_product_id"],
                "U9001",
            )

            logs = publish_bus.load_publish_logs()
            matching = [item for item in logs if item.get("job_id") == "job-persist-1"]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["status"], "published")

        self.with_temp_app(run)

    def test_mercadolibre_image_upload_failure_compacts_duplicate_errors(self) -> None:
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

            with patch.object(
                publisher,
                "upload_mercadolibre_picture",
                side_effect=RuntimeError(ml_error),
            ):
                result = publish_mercadolibre.ensure_mercadolibre_pictures_uploaded(
                    product,
                    "token",
                )

            self.assertFalse(result["ok"])
            errors = result["errors"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["code"], "IMAGE_UPLOAD_FAILED")
            self.assertIn("不兼容 Mercado Libre 图片引擎", errors[0]["message"])
            self.assertIn("共 2 次", errors[0]["message"])
            self.assertNotIn('"cause"', errors[0]["message"])

        self.with_temp_app(run)

    def test_mercadolibre_user_product_site_error_is_publish_failure(self) -> None:
        def run(app_dir: Path) -> None:
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

            self.assertFalse(
                publish_mercadolibre._mercadolibre_publish_result_ok(api_result)
            )
            mapped = publish_mercadolibre._mercadolibre_publish_result_error_map(
                api_result
            )
            self.assertIn("RECOMMENDED_AGE_GROUP", mapped["summary"])
            self.assertIn("site_item_errors", mapped)

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

    def test_exchange_mercadolibre_code_persists_verified_user_products_model(self) -> None:
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
            ), patch.object(
                publisher,
                "fetch_mercadolibre_user_profile",
                return_value={
                    "user_id": "seller-1",
                    "nickname": "Demo Shop",
                    "site_id": "CBT",
                    "tags": ["user_product_seller"],
                },
            ), patch.object(
                publisher,
                "fetch_mercadolibre_marketplace_user",
                return_value={
                    "user_id": "seller-1",
                    "site_id": "CBT",
                    "marketplace_bindings": [
                        {
                            "seller_id": "seller-mlm",
                            "site_id": "MLM",
                            "logistic_type": "remote",
                            "pricing_model": "price",
                            "user_product": True,
                        }
                    ],
                },
            ):
                result = store_credentials.exchange_mercadolibre_code_from_body({"code_or_url": "https://example.com/callback?code=TG-1"})

            self.assertEqual(result["status"], "测试成功")
            self.assertTrue(result["publish_ready"])
            self.assertEqual(result["next_action"], "已可用于发布")
            saved = get_context().config.load_store_config()["mercadolibre"]
            self.assertEqual(saved["access_token"], "token-123")
            self.assertEqual(saved["listing_model"], "user_products")
            self.assertEqual(saved["listing_currency"], "USD")
            self.assertNotIn("code_verifier", saved)
            checklist = get_context().config.mercadolibre_auth_checklist()
            self.assertTrue(checklist["token_ready"])
            self.assertNotIn(
                "code_verifier",
                {field["key"] for field in checklist["fields"]},
            )
            self.assertNotIn("code_verifier", checklist["copy_text"])

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

        traditional_ready = get_context().config.mercadolibre_auth_checklist(
            {
                "app_id": "123",
                "app_secret": "secret",
                "redirect_uri": "https://example.com/callback",
                "access_token": "access",
                "refresh_token": "refresh",
                "site_id": "CBT",
                "listing_model": "traditional_global_items",
                "user_product_seller": False,
            }
        )
        self.assertTrue(traditional_ready["token_ready"])
        self.assertIn("传统 CBT Global Items", traditional_ready["next_action"])
        self.assertNotIn("尚未开通 User Products", traditional_ready["next_action"])

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
