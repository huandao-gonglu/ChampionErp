from __future__ import annotations

from email.message import Message
import io
import json
from pathlib import Path
import urllib.parse
import zipfile

from PIL import Image
import pytest

from erp_web import http_routes
from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.db import ErpDatabase
from erp_web.facades.product_facade import (
    export_drafts_archive,
    import_drafts_archive,
)
from erp_web.http_request import safe_binary_body, safe_json_body
from erp_web.runtime_units.publish_validation import (
    validate_ozon_draft,
    validate_yandex_draft,
)
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.draft_transfer_service import (
    DRAFT_EXPORT_FORMAT,
    DRAFT_EXPORT_VERSION,
)


def _make_png(color: tuple[int, int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


PNG_1X1 = _make_png((255, 0, 0, 255))
PNG_1X1_ALT = _make_png((0, 128, 255, 255))


def _make_context(root: Path, user_id: str) -> AppContext:
    app_dir = root / user_id
    app_dir.mkdir(parents=True, exist_ok=True)
    paths = AppPaths.from_app_dir(app_dir)
    ctx = AppContext(paths, ErpDatabase(paths.db_path))
    ctx.user_id = user_id
    return ctx


def _headers(**values: str) -> Message:
    msg = Message()
    for key, value in values.items():
        msg[key.replace("_", "-")] = value
    return msg


class _DummyHandler:
    def __init__(self, path: str, body: bytes = b"", *, content_type: str = "application/json") -> None:
        self.command = "POST"
        self.path = path
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = _headers(
            Host="127.0.0.1:5050",
            Content_Length=str(len(body)),
            Content_Type=content_type,
        )
        self.json_responses: list[tuple[dict, int]] = []
        self.binary_responses: list[tuple[bytes, str, str, dict[str, str], int]] = []

    def read_body(self) -> dict:
        return safe_json_body(self)

    def read_binary_body(self, *, allowed_content_types: frozenset[str], max_bytes: int | None = None) -> bytes:
        kwargs = {"allowed_content_types": allowed_content_types}
        if max_bytes is not None:
            kwargs["max_bytes"] = max_bytes
        return safe_binary_body(self, **kwargs)

    def send_json(self, data: dict, status: int = 200) -> None:
        self.json_responses.append((data, status))

    def send_binary(
        self,
        raw: bytes,
        *,
        content_type: str,
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self.binary_responses.append((raw, content_type, filename, dict(extra_headers or {}), status))

    def send_response(self, status: int) -> None:
        self.status = status

    def end_headers(self) -> None:
        pass


def _seed_rich_draft(ctx: AppContext) -> tuple[str, str]:
    img_dir = ctx.paths.app_dir / "data" / "images" / "prod_origin"
    img_dir.mkdir(parents=True, exist_ok=True)
    main_file = img_dir / "main.png"
    sku_file = img_dir / "sku_edited.png"
    unused_file = img_dir / "unused_collect.png"
    main_file.write_bytes(PNG_1X1)
    sku_file.write_bytes(PNG_1X1_ALT)
    unused_file.write_bytes(PNG_1X1)

    rel_main = str(main_file.relative_to(ctx.paths.app_dir))
    rel_sku = str(sku_file.relative_to(ctx.paths.app_dir))
    rel_unused = str(unused_file.relative_to(ctx.paths.app_dir))

    product = ctx.products.save_product(
        {
            "product_id": "prod_origin",
            "name": "便携保温杯",
            "brand": "Champion",
            "model": "C-500",
            "upc": "123456789012",
            "collect_logs": [{"step": "collect", "ok": True}],
            "sku_items": [
                {
                    "id": "sku-1",
                    "master_sku": "M-CUP-BLK",
                    "name": "黑色 500ml",
                    "cost_cny": "35.5",
                    "supplier_stock": "200",
                    "barcode": "8801234567890",
                    "image_asset_id": "img-main",
                    "package_dimensions": {
                        "length_cm": "22",
                        "width_cm": "8",
                        "height_cm": "8",
                        "weight_kg": "0.35",
                    },
                    "options": {"颜色": "黑色", "容量": "500ml"},
                }
            ],
            "source": {
                "title": "便携保温杯",
                "source_platform": "1688",
                "source_url": "https://detail.1688.com/offer/998877.html",
                "collect_status": "success",
                "collect_logs": [{"msg": "raw collect log"}],
                "collect_diagnostics": {"raw": True},
                "images": [rel_main, rel_sku, rel_unused],
                "image_pool": [
                    {
                        "id": "img-main",
                        "path": rel_main,
                        "local_path": rel_main,
                        "url": "https://s3.example.com/bucket/img-main.png",
                        "origin": "source",
                        "is_main": True,
                        "status": "ready",
                        "platform_picture_id": "ml-pic-1",
                        "hosting_profile_id": "prof-1",
                        "storage_key": "k/img-main.png",
                        "delivery_fingerprint": "fp-1",
                        "watermark_fingerprint": "wm-1",
                    },
                    {
                        "id": "img-sku-edited",
                        "path": rel_sku,
                        "local_path": rel_sku,
                        "origin": "ai_edited",
                        "source_asset_id": "img-main",
                        "is_sku": True,
                        "status": "ready",
                        "hosting_profile_id": "prof-1",
                        "storage_key": "k/img-sku.png",
                    },
                    {
                        "id": "img-unused",
                        "path": rel_unused,
                        "local_path": rel_unused,
                        "origin": "source",
                        "status": "ready",
                    },
                ],
            },
        },
        preserve_platform_facts=False,
    )

    draft_id = ctx.db.upsert_draft_model(
        product["product_id"],
        "yandex",
        {
            "draft_id": "draft_origin_1",
            "product_id": product["product_id"],
            "source_product_id": product["product_id"],
            "platform": "yandex",
            "platforms": ["yandex"],
            "site": "global",
            "language": "ru",
            "title": "Термос 500 мл черный",
            "description": "Надежный термос из нержавеющей стали.",
            "category_id": "90666",
            "category_path": "Дом / Посуда / Термосы",
            "publish_status": "published",
            "status": "published",
            "last_publish_task": {"job_id": "job-old", "offer_id": "YDX-CUP-BLK"},
            "last_precheck": {"ok": True},
            "category_precheck": {"ok": True},
            "attributes": {
                "brand": "Champion",
                "GTIN": "8801234567890",
                "EMPTY_GTIN_REASON": "The product does not have a registered code",
            },
            "target_sites": [
                {
                    "platform": "yandex",
                    "site": "global",
                    "language": "ru",
                    "listing_currency": "RUB",
                    "currency_fingerprint": "fp-store-a",
                    "category_id": "90666",
                    "category_path": "Дом / Посуда / Термосы",
                    "attributes": {
                        "brand": "Champion",
                        "GTIN": "8801234567890",
                        "EMPTY_GTIN_REASON": "The product does not have a registered code",
                    },
                    "publish_status": "published",
                    "status": "published",
                    "last_publish_task": {"job_id": "job-old"},
                }
            ],
            "pricing": {
                "common": {"domestic_freight_cny": 5, "packaging_cost_cny": 2},
                "targets": {
                    "yandex:global": {
                        "pricing_mode": "margin",
                        "target_margin_percent": 32,
                        "shipping_quote_mode": "auto",
                        "shipping_selection": {
                            "warehouse_id": "wh-old-1",
                            "route_id": "route-old-1",
                        },
                    }
                },
            },
            "shipping": {"mode": "FBS"},
            "images": [{"asset_id": "img-main", "role": "main", "order": 0}],
            "sku_items": [
                {
                    "sku_id": "sku-1",
                    "sku": "YDX-CUP-BLK",
                    "selected": True,
                    "stock": "15",
                    "overrides": {
                        "image_asset_id": "img-sku-edited",
                    },
                    "publications": {"yandex:global": {"remote_id": "rem-1"}},
                    "pricing": {
                        "applied": True,
                        "targets": {
                            "yandex:global": {
                                "applied_price": {"amount": "1990", "currency": "RUB"},
                                "listing_currency": "RUB",
                                "currency_fingerprint": "fp-store-a",
                                "calculation_basis": {
                                    "listing_currency": "RUB",
                                    "currency_fingerprint": "fp-store-a",
                                    "shipping_selection": {
                                        "warehouse_id": "wh-old-1",
                                        "route_id": "route-old-1",
                                    },
                                },
                            }
                        },
                    },
                }
            ],
        },
    )
    return product["product_id"], draft_id


def test_draft_zip_export_and_import_roundtrip_across_accounts(tmp_path: Path) -> None:
    source_ctx = _make_context(tmp_path, "user-a")
    target_ctx = _make_context(tmp_path, "user-b")

    try:
        with bind_context(source_ctx):
            _product_id, draft_id = _seed_rich_draft(source_ctx)

            export_body = validate_request_payload(
                {"draft_ids": [draft_id]},
                endpoint="/api/drafts/export",
            )
            zip_bytes, filename, count, missing_ids = export_drafts_archive(export_body)
            assert count == 1
            assert missing_ids == []
            assert filename == "Термос 500 мл черный.zip"

            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                names = set(zf.namelist())
                assert "manifest.json" in names
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                assert manifest["format"] == DRAFT_EXPORT_FORMAT
                assert manifest["version"] == DRAFT_EXPORT_VERSION
                assert manifest["product_schema_version"] == PRODUCT_SCHEMA_VERSION
                assert len(manifest["items"]) == 1

                # 只导出草稿/SKU 实际引用的 2 张本地图，采集废图 img-unused 不入包
                item = manifest["items"][0]
                exported_product = item["product"]
                exported_draft = item["drafts"][0]
                pool_ids = {img["id"] for img in exported_product["source"]["image_pool"]}
                assert pool_ids == {"img-main", "img-sku-edited"}
                assert len(manifest["assets"]) == 2
                for asset_key in manifest["assets"]:
                    assert f"assets/{asset_key}" in names

                # 不导出采集与 UPC/条码、不导出托管与发布态
                assert exported_product["in_library"] is False
                assert "collect_logs" not in exported_product
                assert "collect_logs" not in exported_product["source"]
                assert exported_product["upc"] == ""
                assert exported_product["sku_items"][0]["barcode"] == ""
                assert exported_product["sku_items"][0]["master_sku"] == "M-CUP-BLK"

                for img in exported_product["source"]["image_pool"]:
                    assert "platform_picture_id" not in img
                    assert "hosting_profile_id" not in img
                    assert "storage_key" not in img
                    assert "delivery_fingerprint" not in img
                    assert "watermark_fingerprint" not in img
                    assert img["path"] == ""

                assert exported_draft["publish_status"] == ""
                assert exported_draft["last_publish_task"] == {}
                assert exported_draft["last_precheck"] == {}
                assert "GTIN" not in exported_draft["attributes"]
                assert exported_draft["attributes"]["EMPTY_GTIN_REASON"] == "The product does not have a registered code"
                assert "GTIN" not in exported_draft["target_sites"][0]["attributes"]
                sku_row = exported_draft["sku_items"][0]
                assert sku_row["sku"] == "YDX-CUP-BLK"
                assert sku_row["publications"] == {}
                assert sku_row["overrides"] == {"image_asset_id": "img-sku-edited"}
                # 保留运费、物流、核价配置
                assert exported_draft["pricing"]["targets"]["yandex:global"]["shipping_selection"] == {
                    "warehouse_id": "wh-old-1",
                    "route_id": "route-old-1",
                }
                assert exported_draft["shipping"] == {"mode": "FBS"}

        # 跨账号导入：不进入采集箱，完整还原草稿、核价、SKU 及本地图片
        with bind_context(target_ctx):
            imported, status = import_drafts_archive(zip_bytes)
            assert status == 200
            assert imported["ok"] is True
            assert imported["imported"] == 1
            assert len(imported["importedDraftIds"]) == 1
            new_draft_id = imported["importedDraftIds"][0]
            assert new_draft_id != draft_id

            # 采集箱保持干净
            assert target_ctx.products.load_products_index() == []
            drafts_index = target_ctx.products.load_drafts_index(scope="all")
            assert len(drafts_index) == 1
            assert drafts_index[0]["draft_id"] == new_draft_id

            restored_draft = target_ctx.products.draft_record(new_draft_id)
            assert restored_draft["title"] == "Термос 500 мл черный"
            assert restored_draft["publish_status"] == ""
            assert restored_draft["last_publish_task"] == {}
            assert restored_draft["sku_items"][0]["sku"] == "YDX-CUP-BLK"
            assert restored_draft["pricing"]["targets"]["yandex:global"]["shipping_selection"]["warehouse_id"] == "wh-old-1"

            host_product = target_ctx.products.find_indexed_product(restored_draft["product_id"])
            assert host_product["in_library"] is False
            assert host_product["upc"] == ""
            assert host_product["sku_items"][0]["barcode"] == ""
            assert host_product["sku_items"][0]["master_sku"] == "M-CUP-BLK"
            pool_by_id = {img["id"]: img for img in host_product["source"]["image_pool"]}
            assert set(pool_by_id) == {"img-main", "img-sku-edited"}
            main_disk = target_ctx.paths.app_dir / pool_by_id["img-main"]["path"]
            sku_disk = target_ctx.paths.app_dir / pool_by_id["img-sku-edited"]["path"]
            assert main_disk.read_bytes() == PNG_1X1
            assert sku_disk.read_bytes() == PNG_1X1_ALT

            # 导入后的草稿（宿主 in_library=False）可以再次导出
            re_zip, _re_name, re_count, _ = export_drafts_archive({"draft_ids": [new_draft_id]})
            assert re_count == 1
            assert len(re_zip) > 0
    finally:
        source_ctx.close()
        target_ctx.close()


def test_draft_zip_import_rejects_unsafe_and_invalid_archives(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, "user-sec")
    try:
        with bind_context(ctx):
            # 1. 非法 ZIP
            with pytest.raises(RequestValidationError, match="ZIP"):
                import_drafts_archive(b"not-a-zip")

            # 2. Zip Slip 路径穿越
            slip_buf = io.BytesIO()
            with zipfile.ZipFile(slip_buf, "w") as zf:
                zf.writestr("../escape.png", PNG_1X1)
                zf.writestr("manifest.json", "{}")
            with pytest.raises(RequestValidationError, match="不安全的文件路径"):
                import_drafts_archive(slip_buf.getvalue())

            # 3. 伪造图片内容（校验阶段拦截，零落盘）
            fake_buf = io.BytesIO()
            bad_manifest = {
                "format": DRAFT_EXPORT_FORMAT,
                "version": DRAFT_EXPORT_VERSION,
                "product_schema_version": PRODUCT_SCHEMA_VERSION,
                "items": [
                    {
                        "product": {
                            "schema_version": PRODUCT_SCHEMA_VERSION,
                            "name": "测试商品",
                            "source": {
                                "image_pool": [
                                    {"id": "img-1", "export_asset_key": "asset_0123456789abcdef.png"}
                                ]
                            },
                        },
                        "drafts": [{"platform": "ozon", "site": "RU", "title": "Test"}],
                    }
                ],
                "assets": {
                    "asset_0123456789abcdef.png": {
                        "path": "assets/asset_0123456789abcdef.png",
                        "filename": "fake.png",
                        "content_type": "image/png",
                    }
                },
            }
            with zipfile.ZipFile(fake_buf, "w") as zf:
                zf.writestr("manifest.json", json.dumps(bad_manifest))
                zf.writestr("assets/asset_0123456789abcdef.png", b"not-a-real-png-file")
            with pytest.raises(RequestValidationError, match="图片资产"):
                import_drafts_archive(fake_buf.getvalue())

            assert ctx.products.load_drafts_index(scope="all") == []
            images_dir = ctx.paths.app_dir / "data" / "images"
            assert not images_dir.exists() or list(images_dir.iterdir()) == []
    finally:
        ctx.close()


def test_http_drafts_export_and_import_routes(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, "user-http")
    try:
        with bind_context(ctx):
            _pid, draft_id = _seed_rich_draft(ctx)

            export_handler = _DummyHandler(
                "/api/drafts/export",
                json.dumps({"draft_ids": [draft_id]}).encode("utf-8"),
            )
            http_routes.handle_post(export_handler)  # type: ignore[arg-type]
            assert len(export_handler.binary_responses) == 1
            zip_bytes, content_type, filename, extra_headers, status = export_handler.binary_responses[0]
            assert status == 200
            assert content_type == "application/zip"
            assert filename.endswith(".zip")
            assert extra_headers["X-Export-Count"] == "1"
            assert urllib.parse.unquote(extra_headers["X-Export-Filename"]) == filename

            # 非法 Content-Type 被 415 拒绝
            bad_ct_handler = _DummyHandler("/api/drafts/import", zip_bytes, content_type="text/plain")
            http_routes.handle_post(bad_ct_handler)  # type: ignore[arg-type]
            assert bad_ct_handler.json_responses[0][1] == 415

            # 合法 ZIP 导入成功且生成新草稿
            import_handler = _DummyHandler("/api/drafts/import", zip_bytes, content_type="application/zip")
            http_routes.handle_post(import_handler)  # type: ignore[arg-type]
            payload, import_status = import_handler.json_responses[0]
            assert import_status == 200
            assert payload["ok"] is True
            assert payload["imported"] == 1
            assert len(ctx.products.load_drafts_index(scope="all")) == 2
    finally:
        ctx.close()


def test_precheck_detects_local_sku_conflict_and_invalid_yandex_shipping_selection(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, "user-precheck")
    try:
        with bind_context(ctx):
            _pid, first_draft_id = _seed_rich_draft(ctx)
            zip_bytes, _fname, _cnt, _ = export_drafts_archive({"draft_ids": [first_draft_id]})
            imported, _status = import_drafts_archive(zip_bytes)
            imported_draft_id = imported["importedDraftIds"][0]

            draft = ctx.products.draft_record(imported_draft_id)
            product = ctx.products.find_indexed_product(draft["product_id"])
            product["drafts"] = {"yandex": draft}

            # 验证 Yandex 预检同时抓到：
            # 1) shipping_selection 指向不可用的旧仓库/路线 -> YANDEX_SHIPPING_SELECTION_INVALID error
            # 2) 与已有草稿共用同一卖家 SKU -> LOCAL_SKU_CONFLICT warning
            result = validate_yandex_draft(product, {"yandex": {}}, category_record=None)
            error_codes = {item["code"] for item in result["errors"]}
            warning_codes = {item["code"] for item in result["warnings"]}
            assert "YANDEX_SHIPPING_SELECTION_INVALID" in error_codes
            assert "LOCAL_SKU_CONFLICT" in warning_codes

            # 验证 Ozon 本地在线商品快照 SKU 冲突也会发出 LOCAL_SKU_CONFLICT warning
            with ctx.db._connect() as conn:
                conn.execute(
                    "INSERT INTO online_listings (id, platform, account_id, remote_id, version, snapshot_json, synced_at, desired_sale_state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "ozon:acc1:1001",
                        "ozon",
                        "acc1",
                        "1001",
                        "v1",
                        json.dumps({"seller_sku": "OZ-DUP-001"}),
                        "2026-11-27T00:00:00Z",
                        "",
                    ),
                )
                conn.commit()
            ozon_product = {
                "product_id": "prod_oz_1",
                "name": "测试 Ozon",
                "sku_items": [{"id": "s1", "master_sku": "OZ-DUP-001"}],
                "drafts": {
                    "ozon": {
                        "draft_id": "draft_oz_new",
                        "platform": "ozon",
                        "site": "RU",
                        "title": "Test",
                        "description": "Desc",
                        "sku": "OZ-DUP-001",
                        "sku_items": [{"sku_id": "s1", "sku": "OZ-DUP-001", "selected": True}],
                    }
                },
            }
            ozon_result = validate_ozon_draft(ozon_product, {"ozon": {}}, category_record=None)
            assert "LOCAL_SKU_CONFLICT" in {item["code"] for item in ozon_result["warnings"]}
    finally:
        ctx.close()


def test_draft_zip_groups_multiple_drafts_of_same_product(tmp_path: Path) -> None:
    source_ctx = _make_context(tmp_path, "user-group-src")
    target_ctx = _make_context(tmp_path, "user-group-dst")
    try:
        with bind_context(source_ctx):
            product_id, yandex_draft_id = _seed_rich_draft(source_ctx)
            ozon_draft_id = source_ctx.db.upsert_draft_model(
                product_id,
                "ozon",
                {
                    "draft_id": "draft_origin_ozon",
                    "product_id": product_id,
                    "source_product_id": product_id,
                    "platform": "ozon",
                    "platforms": ["ozon"],
                    "site": "RU",
                    "language": "ru",
                    "title": "Термос Ozon 500 мл",
                    "description": "Описание для Ozon",
                    "images": [{"asset_id": "img-main", "role": "main", "order": 0}],
                    "sku_items": [
                        {"sku_id": "sku-1", "sku": "OZ-CUP-BLK", "selected": True, "stock": "10"}
                    ],
                },
            )
            zip_bytes, filename, count, _ = export_drafts_archive(
                {"draft_ids": [yandex_draft_id, ozon_draft_id]}
            )
            assert count == 2
            assert filename == "Термос 500 мл черный等2份草稿.zip"
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                # 同宿主商品的两份草稿归并到同一个 item，图片不重复打包
                assert len(manifest["items"]) == 1
                assert len(manifest["items"][0]["drafts"]) == 2
                assert len(manifest["assets"]) == 2

        with bind_context(target_ctx):
            imported, status = import_drafts_archive(zip_bytes)
            assert status == 200
            assert imported["imported"] == 2
            d1 = target_ctx.products.draft_record(imported["importedDraftIds"][0])
            d2 = target_ctx.products.draft_record(imported["importedDraftIds"][1])
            assert d1["product_id"] == d2["product_id"]
            assert target_ctx.products.load_products_index() == []
    finally:
        source_ctx.close()
        target_ctx.close()
