from __future__ import annotations

from pathlib import Path
import pytest

from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.db import ErpDatabase
from erp_web.facades.product_facade import export_products_payload, import_products_payload
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.product_transfer_service import EXPORT_FORMAT, EXPORT_VERSION


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_context(root: Path, user_id: str) -> AppContext:
    app_dir = root / user_id
    app_dir.mkdir(parents=True, exist_ok=True)
    paths = AppPaths.from_app_dir(app_dir)
    ctx = AppContext(paths, ErpDatabase(paths.db_path))
    ctx.user_id = user_id
    return ctx


def _envelope(products: list[dict], **overrides) -> dict:
    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "product_schema_version": PRODUCT_SCHEMA_VERSION,
        "app_version": "1.2.100",
        "products": products,
        "assets": {},
    }
    payload.update(overrides)
    return payload


def test_export_and_import_products_roundtrip_with_local_images(tmp_path: Path) -> None:
    source_ctx = _make_context(tmp_path, "user-a")
    target_ctx = _make_context(tmp_path, "user-b")

    img_dir = source_ctx.paths.app_dir / "data" / "images" / "prod_origin"
    img_dir.mkdir(parents=True, exist_ok=True)
    img_file = img_dir / "main.png"
    img_file.write_bytes(PNG_1X1)
    rel_img = str(img_file.relative_to(source_ctx.paths.app_dir))

    try:
        with bind_context(source_ctx):
            saved = source_ctx.products.save_product(
                {
                    "product_id": "prod_origin",
                    "name": "不锈钢保温杯",
                    "brand": "Champion",
                    "sku_items": [
                        {
                            "id": "sku-1",
                            "name": "黑色 500ml",
                            "cost_cny": "38.5",
                            "image_asset_id": "img-1",
                            "options": {"颜色": "黑色", "容量": "500ml"},
                        }
                    ],
                    "source": {
                        "title": "不锈钢保温杯",
                        "source_platform": "1688",
                        "source_url": "https://detail.1688.com/offer/123456.html",
                        "collect_status": "success",
                        "images": [rel_img],
                        "image_pool": [
                            {
                                "id": "img-1",
                                "path": rel_img,
                                "local_path": rel_img,
                                "origin": "source",
                                "is_main": True,
                                "status": "ready",
                                "platform_picture_id": "secret-pic-id",
                            }
                        ],
                    },
                },
                preserve_platform_facts=False,
            )
            assert saved["product_id"] == "prod_origin"

            export_body = validate_request_payload(
                {"product_ids": ["prod_origin"]},
                endpoint="/api/products/export",
            )
            exported, status = export_products_payload(export_body)
            assert status == 200
            assert exported["ok"] is True
            assert exported["count"] == 1
            assert exported["filename"] == "不锈钢保温杯.json"
            export_data = exported["exportData"]
            assert export_data["format"] == EXPORT_FORMAT
            assert export_data["version"] == EXPORT_VERSION
            assert export_data["product_schema_version"] == PRODUCT_SCHEMA_VERSION
            assert isinstance(export_data["app_version"], str) and export_data["app_version"]
            assert len(export_data["assets"]) == 1
            exported_item = export_data["products"][0]
            assert exported_item["schema_version"] == PRODUCT_SCHEMA_VERSION
            assert "drafts" not in exported_item
            pool_item = exported_item["source"]["image_pool"][0]
            assert "platform_picture_id" not in pool_item
            assert pool_item["path"] == ""
            assert pool_item["export_asset_key"] in export_data["assets"]

            # 在同一账号重复导入时生成新 ID，不覆盖原商品；不同 app_version 不拦截
            dup_payload = {**export_data, "app_version": "0.9.0"}
            dup_body = validate_request_payload(dup_payload, endpoint="/api/products/import")
            dup_result, dup_status = import_products_payload(dup_body)
            assert dup_status == 200
            assert dup_result["imported"] == 1
            assert dup_result["importedIds"][0] != "prod_origin"
            assert len(source_ctx.products.load_products_index()) == 2

            # 导出多个商品时使用首个商品标题 + 数量命名
            multi_exported, multi_status = export_products_payload(
                validate_request_payload(
                    {"product_ids": ["prod_origin", dup_result["importedIds"][0]]},
                    endpoint="/api/products/export",
                )
            )
            assert multi_status == 200
            assert multi_exported["filename"] == "不锈钢保温杯等2个商品.json"

        # 跨账号导入可完整还原商品与图片到目标账号空间
        with bind_context(target_ctx):
            import_body = validate_request_payload(export_data, endpoint="/api/products/import")
            imported, import_status = import_products_payload(import_body)
            assert import_status == 200
            assert imported["ok"] is True
            assert imported["imported"] == 1
            imported_id = imported["importedIds"][0]
            assert imported_id == "prod_origin"

            restored = target_ctx.products.find_indexed_product(imported_id)
            assert restored["name"] == "不锈钢保温杯"
            assert restored["brand"] == "Champion"
            assert restored["sku_items"][0]["id"] == "sku-1"
            assert restored["sku_items"][0]["name"] == "黑色 500ml"
            assert restored["sku_items"][0]["cost_cny"] == "38.5"
            assert restored["sku_items"][0]["image_asset_id"] == "img-1"
            assert restored["sku_items"][0]["options"] == {"颜色": "黑色", "容量": "500ml"}
            restored_pool = restored["source"]["image_pool"]
            assert len(restored_pool) == 1
            restored_rel = restored_pool[0]["path"]
            restored_file = target_ctx.paths.app_dir / restored_rel
            assert restored_file.exists()
            assert restored_file.read_bytes() == PNG_1X1
    finally:
        source_ctx.close()
        target_ctx.close()


def test_import_products_validates_versions_integrity_and_atomic_precheck(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, "user-c")
    valid_product = {
        "schema_version": PRODUCT_SCHEMA_VERSION,
        "name": "外链测试商品",
        "source": {
            "title": "外链测试商品",
            "image_pool": [
                {
                    "id": "bad-local",
                    "path": "/etc/passwd",
                    "preview_url": "/file?path=/etc/passwd",
                },
                {
                    "id": "ok-remote",
                    "url": "https://img.example.com/a.jpg",
                },
            ],
        },
    }
    try:
        with bind_context(ctx):
            # 1. 错误 format 拒绝
            with pytest.raises(RequestValidationError, match="不支持的导入文件格式"):
                import_products_payload(_envelope([valid_product], format="other.format"))

            # 2. 更高导出包版本拒绝
            with pytest.raises(RequestValidationError, match="高于当前系统支持的版本"):
                import_products_payload(_envelope([valid_product], version=EXPORT_VERSION + 1))

            # 3. 不兼容的顶层 product_schema_version 拒绝
            with pytest.raises(RequestValidationError, match="高于当前系统支持的版本"):
                import_products_payload(
                    _envelope([valid_product], product_schema_version=PRODUCT_SCHEMA_VERSION + 1)
                )

            # 4. 第 2 个商品版本错误或内嵌资产缺失时，全量预校验拦截，第 1 个商品也不会写入
            bad_schema_product = {**valid_product, "schema_version": PRODUCT_SCHEMA_VERSION - 1}
            with pytest.raises(RequestValidationError, match="第 2 个商品校验失败"):
                import_products_payload(_envelope([valid_product, bad_schema_product]))
            assert ctx.products.load_products_index() == []

            missing_asset_product = {
                "schema_version": PRODUCT_SCHEMA_VERSION,
                "name": "缺图商品",
                "source": {
                    "title": "缺图商品",
                    "image_pool": [{"id": "img-x", "export_asset_key": "asset_missing.png"}],
                },
            }
            with pytest.raises(RequestValidationError, match="引用的内嵌图片资产缺失"):
                import_products_payload(_envelope([valid_product, missing_asset_product]))
            assert ctx.products.load_products_index() == []

            # 5. 合法包导入成功，且过滤掉外来本地路径
            result, status = import_products_payload(_envelope([valid_product]))
            assert status == 200
            saved = ctx.products.find_indexed_product(result["importedIds"][0])
            pool = saved["source"]["image_pool"]
            assert len(pool) == 1
            assert pool[0]["url"] == "https://img.example.com/a.jpg"
    finally:
        ctx.close()
