from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.db import ErpDatabase
from erp_web.facades.product_facade import export_products_archive, import_products_archive
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.product_transfer_service import EXPORT_FORMAT, EXPORT_VERSION


def _make_png(color: tuple[int, int, int, int] = (20, 120, 220, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


PNG_1X1 = _make_png()


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


def _pack_zip(manifest: dict, asset_files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for key, raw in (asset_files or {}).items():
            zf.writestr(f"assets/{key}", raw, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def _read_zip(raw_zip: bytes) -> tuple[dict, dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(raw_zip), mode="r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assets = {
            name[len("assets/"):]: zf.read(name)
            for name in zf.namelist()
            if name.startswith("assets/") and not name.endswith("/")
        }
    return manifest, assets


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
            zip_bytes, filename, count, missing_ids = export_products_archive(export_body)
            assert count == 1
            assert missing_ids == []
            assert filename == "不锈钢保温杯.zip"
            manifest, asset_blobs = _read_zip(zip_bytes)
            assert manifest["format"] == EXPORT_FORMAT
            assert manifest["version"] == EXPORT_VERSION
            assert manifest["product_schema_version"] == PRODUCT_SCHEMA_VERSION
            assert isinstance(manifest["app_version"], str) and manifest["app_version"]
            assert len(manifest["assets"]) == 1
            assert len(asset_blobs) == 1
            asset_key = next(iter(asset_blobs))
            assert asset_blobs[asset_key] == PNG_1X1
            assert "data_url" not in manifest["assets"][asset_key]
            exported_item = manifest["products"][0]
            assert exported_item["schema_version"] == PRODUCT_SCHEMA_VERSION
            assert "drafts" not in exported_item
            pool_item = exported_item["source"]["image_pool"][0]
            assert "platform_picture_id" not in pool_item
            assert pool_item["path"] == ""
            assert pool_item["export_asset_key"] == asset_key

            # 在同一账号重复导入时生成新 ID，不覆盖原商品；不同 app_version 不拦截
            dup_zip = _pack_zip({**manifest, "app_version": "0.9.0"}, asset_blobs)
            validate_request_payload({}, endpoint="/api/products/import")
            dup_result, dup_status = import_products_archive(dup_zip)
            assert dup_status == 200
            assert dup_result["imported"] == 1
            assert dup_result["importedIds"][0] != "prod_origin"
            assert len(source_ctx.products.load_products_index()) == 2

            # 导出多个商品时使用首个商品标题 + 数量命名
            _, multi_filename, multi_count, _ = export_products_archive(
                validate_request_payload(
                    {"product_ids": ["prod_origin", dup_result["importedIds"][0]]},
                    endpoint="/api/products/export",
                )
            )
            assert multi_count == 2
            assert multi_filename == "不锈钢保温杯等2个商品.zip"

        # 跨账号导入可完整还原商品与图片到目标账号空间
        with bind_context(target_ctx):
            imported, import_status = import_products_archive(zip_bytes)
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
            # 0. 非 ZIP 文件拒绝
            with pytest.raises(RequestValidationError, match="不支持的导入文件格式"):
                import_products_archive(b"not-a-zip")

            # 1. 错误 format 拒绝
            with pytest.raises(RequestValidationError, match="不支持的导入文件格式"):
                import_products_archive(_pack_zip(_envelope([valid_product], format="other.format")))

            # 2. 更高导出包版本拒绝
            with pytest.raises(RequestValidationError, match="高于当前系统支持的版本"):
                import_products_archive(_pack_zip(_envelope([valid_product], version=EXPORT_VERSION + 1)))

            # 3. 不兼容的顶层 product_schema_version 拒绝
            with pytest.raises(RequestValidationError, match="高于当前系统支持的版本"):
                import_products_archive(
                    _pack_zip(_envelope([valid_product], product_schema_version=PRODUCT_SCHEMA_VERSION + 1))
                )

            # 4. 第 2 个商品版本错误或内嵌资产缺失时，全量预校验拦截，第 1 个商品也不会写入
            bad_schema_product = {**valid_product, "schema_version": PRODUCT_SCHEMA_VERSION - 1}
            with pytest.raises(RequestValidationError, match="第 2 个商品校验失败"):
                import_products_archive(_pack_zip(_envelope([valid_product, bad_schema_product])))
            assert ctx.products.load_products_index() == []

            missing_asset_product = {
                "schema_version": PRODUCT_SCHEMA_VERSION,
                "name": "缺图商品",
                "source": {
                    "title": "缺图商品",
                    "image_pool": [{"id": "img-x", "export_asset_key": "asset_0123456789abcdef.png"}],
                },
            }
            with pytest.raises(RequestValidationError, match="引用的内嵌图片资产缺失"):
                import_products_archive(_pack_zip(_envelope([valid_product, missing_asset_product])))
            assert ctx.products.load_products_index() == []

            # 5. 合法包导入成功，且过滤掉外来本地路径
            result, status = import_products_archive(_pack_zip(_envelope([valid_product])))
            assert status == 200
            saved = ctx.products.find_indexed_product(result["importedIds"][0])
            pool = saved["source"]["image_pool"]
            assert len(pool) == 1
            assert pool[0]["url"] == "https://img.example.com/a.jpg"
    finally:
        ctx.close()


def test_import_preserves_distinct_pool_assets_sharing_identical_file_bytes(tmp_path: Path) -> None:
    """主图与 SKU 图 URL 不同但图片二进制内容相同时，导出共享内嵌资产，导入仍保留各自资产 ID 与文件。"""
    from erp_web.product_model.sku_image_model import sku_image_asset

    source_ctx = _make_context(tmp_path, "user-same-src")
    target_ctx = _make_context(tmp_path, "user-same-dst")

    img_dir = source_ctx.paths.app_dir / "data" / "images" / "prod_same_bytes"
    img_dir.mkdir(parents=True, exist_ok=True)
    main_file = img_dir / "remote_1.png"
    sku_file = img_dir / "remote_6.png"
    main_file.write_bytes(PNG_1X1)
    sku_file.write_bytes(PNG_1X1)
    rel_main = str(main_file.relative_to(source_ctx.paths.app_dir))
    rel_sku = str(sku_file.relative_to(source_ctx.paths.app_dir))

    try:
        with bind_context(source_ctx):
            source_ctx.products.save_product(
                {
                    "product_id": "prod_same_bytes",
                    "name": "纯手工草鞋",
                    "sku_items": [
                        {
                            "id": "sku-yellow-26",
                            "name": "黄色 / 26",
                            "cost_cny": "12.52",
                            "image_asset_id": "src_sku_same",
                            "options": {"颜色": "黄色", "尺码": "26"},
                        },
                        {
                            "id": "sku-yellow-27",
                            "name": "黄色 / 27",
                            "cost_cny": "12.52",
                            "image_asset_id": "src_sku_same",
                            "options": {"颜色": "黄色", "尺码": "27"},
                        },
                    ],
                    "source": {
                        "title": "纯手工草鞋",
                        "source_platform": "1688",
                        "source_url": "https://detail.1688.com/offer/1028620347857.html",
                        "collect_status": "success",
                        "images": [rel_main, rel_sku],
                        "image_pool": [
                            {
                                "id": "src_main_same",
                                "path": rel_main,
                                "local_path": rel_main,
                                "origin": "manual",
                                "usage": "main",
                                "is_main": True,
                                "selected": True,
                                "status": "ready",
                            },
                            {
                                "id": "src_sku_same",
                                "path": rel_sku,
                                "local_path": rel_sku,
                                "origin": "manual",
                                "usage": "other",
                                "is_main": False,
                                "is_sku": True,
                                "selected": False,
                                "status": "ready",
                            },
                        ],
                    },
                },
                preserve_platform_facts=False,
            )
            zip_bytes, filename, count, _ = export_products_archive(
                validate_request_payload({"product_ids": ["prod_same_bytes"]}, endpoint="/api/products/export")
            )
            assert count == 1
            assert filename == "纯手工草鞋.zip"
            manifest, asset_blobs = _read_zip(zip_bytes)
            # 导出包仍按内容哈希去重内嵌资产以控制体积
            assert len(manifest["assets"]) == 1
            assert len(asset_blobs) == 1
            exported_pool = manifest["products"][0]["source"]["image_pool"]
            assert len(exported_pool) == 2
            assert exported_pool[0]["export_asset_key"] == exported_pool[1]["export_asset_key"]

        with bind_context(target_ctx):
            imported, import_status = import_products_archive(zip_bytes)
            assert import_status == 200
            restored = target_ctx.products.find_indexed_product(imported["importedIds"][0])
            restored_pool = restored["source"]["image_pool"]
            assert [item["id"] for item in restored_pool] == ["src_main_same", "src_sku_same"]
            assert restored_pool[0]["path"] != restored_pool[1]["path"]
            assert restored_pool[1].get("is_sku") is True
            for item in restored_pool:
                file_path = target_ctx.paths.app_dir / item["path"]
                assert file_path.exists()
                assert file_path.read_bytes() == PNG_1X1
            # 所有 SKU 的 image_asset_id 都能在图片池中正常解析
            for sku in restored["sku_items"]:
                assert sku["image_asset_id"] == "src_sku_same"
                resolved = sku_image_asset(restored, sku)
                assert resolved is not None and resolved["id"] == "src_sku_same"
            # 导入目录下只生成 2 个文件，不因 source.images 额外产生孤儿文件
            imported_files = list((target_ctx.paths.app_dir / "data" / "images" / restored["product_id"]).iterdir())
            assert len(imported_files) == 2
    finally:
        source_ctx.close()
        target_ctx.close()


def test_export_skips_embedding_source_images_with_remote_url_and_isolates_download_tickets(tmp_path: Path) -> None:
    """采集箱导出时，1688 已有公开外链的原图直接导出 URL 不塞进 ZIP；下载票据严格按账号空间隔离。"""
    from erp_web.facades.product_facade import export_products_ticket, resolve_export_download

    source_ctx = _make_context(tmp_path, "user-remote-src")
    other_ctx = _make_context(tmp_path, "user-remote-other")

    img_dir = source_ctx.paths.app_dir / "data" / "images" / "prod_remote"
    img_dir.mkdir(parents=True, exist_ok=True)
    remote_cached = img_dir / "cached_1688.png"
    manual_only = img_dir / "manual_edit.png"
    remote_cached.write_bytes(PNG_1X1)
    manual_only.write_bytes(_make_png((200, 40, 60, 255)))
    rel_remote = str(remote_cached.relative_to(source_ctx.paths.app_dir))
    rel_manual = str(manual_only.relative_to(source_ctx.paths.app_dir))

    try:
        with bind_context(source_ctx):
            source_ctx.products.save_product(
                {
                    "product_id": "prod_remote",
                    "name": "外链瘦身商品",
                    "source": {
                        "title": "外链瘦身商品",
                        "source_platform": "1688",
                        "images": [rel_remote, rel_manual],
                        "image_pool": [
                            {
                                "id": "img-1688",
                                "path": rel_remote,
                                "local_path": rel_remote,
                                "url": "https://cbu01.alicdn.com/img/ibank/1688_big.jpg",
                                "origin": "1688",
                                "is_main": True,
                            },
                            {
                                "id": "img-manual",
                                "path": rel_manual,
                                "local_path": rel_manual,
                                "origin": "manual",
                            },
                        ],
                    },
                },
                preserve_platform_facts=False,
            )
            ticket, status = export_products_ticket({"product_ids": ["prod_remote"]})
            assert status == 200
            assert ticket["ok"] is True
            assert ticket["filename"] == "外链瘦身商品.zip"
            assert ticket["sizeBytes"] > 0
            assert ticket["downloadUrl"].startswith("/api/exports/download?token=")
            token = ticket["downloadUrl"].split("token=", 1)[1]

            entry = resolve_export_download(token)
            assert entry is not None and entry.path.is_file()
            manifest, asset_blobs = _read_zip(entry.path.read_bytes())
            # 1688 带外链原图不内嵌，仅内嵌无外链的 manual 本地图
            assert len(asset_blobs) == 1
            pool_by_id = {item["id"]: item for item in manifest["products"][0]["source"]["image_pool"]}
            assert "export_asset_key" not in pool_by_id["img-1688"]
            assert pool_by_id["img-1688"]["url"] == "https://cbu01.alicdn.com/img/ibank/1688_big.jpg"
            assert pool_by_id["img-manual"]["export_asset_key"] in asset_blobs
            assert manifest["products"][0]["source"]["images"][0] == "https://cbu01.alicdn.com/img/ibank/1688_big.jpg"

        # 跨账号无法解析该导出票据
        with bind_context(other_ctx):
            assert resolve_export_download(token) is None
    finally:
        source_ctx.close()
        other_ctx.close()
