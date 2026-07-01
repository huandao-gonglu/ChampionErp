from __future__ import annotations

from pathlib import Path

from erp_web.product_model import merge_source_partial_result


def test_pool_display_item_converts_existing_local_preview_to_file_url(app_dir: Path) -> None:
    from erp_web.runtime import _pool_display_item

    image_path = app_dir / "data" / "images" / "pytest-path-fix" / "existing.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    raw_path = str(image_path)
    item = _pool_display_item({"id": "existing", "path": raw_path, "preview_url": raw_path, "status": "ready"})

    assert item["preview_url"].startswith("/file?path=")
    assert item["url"].startswith("/file?path=")
    assert raw_path not in item["preview_url"]
    assert item["status"] == "ready"


def test_pool_display_item_marks_missing_local_preview_without_raw_file_url() -> None:
    from erp_web.runtime import _pool_display_item

    raw_path = r"D:\champion-Erp\output\source_images\missing-pytest.jpg"
    item = _pool_display_item({"id": "missing", "path": raw_path, "preview_url": raw_path, "status": "ready"})

    assert item["preview_url"] == ""
    assert item["url"] == ""
    assert item["status"] == "missing_file"
    assert "文件不存在" in item["note"]


def test_products_index_main_image_never_exposes_raw_local_path() -> None:
    from erp_web.runtime import sanitize_products_index

    items = sanitize_products_index([{"product_id": "old", "main_image": r"C:\legacy\bad-image.jpg"}])

    assert items == [{"product_id": "old", "main_image": ""}]


def test_failed_collect_without_images_clears_stale_collect_pool_but_keeps_local_upload() -> None:
    product = {
        "source": {
            "source_url": "https://detail.1688.com/offer/old.html",
            "source_platform": "1688",
            "title": "Old product",
            "images": [r"D:\champion-Erp\output\source_images\old.jpg"],
            "image_pool": [
                {
                    "id": "old-source",
                    "path": r"D:\champion-Erp\output\source_images\old.jpg",
                    "preview_url": r"D:\champion-Erp\output\source_images\old.jpg",
                    "origin": "1688",
                    "status": "ready",
                },
                {
                    "id": "manual-keep",
                    "path": "data/images/uploads/manual.jpg",
                    "preview_url": "/file?path=D%3A%5Cchampion-Erp%5Cdata%5Cimages%5Cuploads%5Cmanual.jpg",
                    "origin": "local_upload",
                    "status": "ready",
                },
            ],
        },
        "source_images": [r"D:\champion-Erp\output\source_images\old.jpg"],
        "source_image_urls": [r"D:\champion-Erp\output\source_images\old.jpg"],
        "sku_items": [{"id": "0", "image": r"D:\champion-Erp\output\source_images\old.jpg"}],
        "drafts": {
            "mercadolibre": {"enabled": True, "images": [r"D:\champion-Erp\output\source_images\old.jpg"]},
            "wildberries": {"enabled": True, "images": [r"D:\champion-Erp\output\source_images\old.jpg"]},
            "ozon": {"enabled": True, "images": [r"D:\champion-Erp\output\source_images\old.jpg"]},
        },
    }

    merged = merge_source_partial_result(
        product,
        {"source_url": "https://detail.1688.com/offer/new.html", "source_platform": "1688", "collect_status": "partial"},
        {"success": False, "images_found_count": 0, "error_code": "1688_IMAGE_NOT_FOUND"},
    )

    pool = merged["source"]["image_pool"]
    assert [item["id"] for item in pool] == ["manual-keep"]
    assert merged["source"]["images"] == []
    assert merged["source_images"] == []
    assert merged["source_image_urls"] == []
    assert merged["sku_items"][0]["image"] == ""
    assert merged["drafts"]["mercadolibre"]["images"] == []
    assert merged["drafts"]["wildberries"]["images"] == []
    assert merged["drafts"]["ozon"]["images"] == []
