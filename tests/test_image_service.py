from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from conftest import assert_no_old_path
from services import image_service


def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (8, 6), color)
    image.save(path, format="PNG")


def test_image_root_is_created_under_data_images(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    root = image_service.images_root(app_dir)
    assert root.exists()
    assert root.is_dir()
    assert root.resolve().is_relative_to((app_dir / "data" / "images").resolve())
    assert_no_old_path(root, old_path_markers)


def test_upload_delete_replace_main_and_sku_image(app_dir: Path, tmp_path: Path, old_path_markers: tuple[str, ...]) -> None:
    source = tmp_path / "test_image.png"
    replacement = tmp_path / "replacement.png"
    _make_png(source, (255, 0, 0))
    _make_png(replacement, (0, 0, 255))

    uploaded = image_service.upload_images(app_dir, [{"path": str(source), "platforms": ["mercadolibre"], "is_main": True}], "pytest-stage3a")
    assert len(uploaded) == 1
    item = uploaded[0]
    saved_path = app_dir / item["path"]
    assert saved_path.exists()
    assert saved_path.resolve().is_relative_to((app_dir / "data" / "images").resolve())
    assert item["preview_url"].startswith("/file?path=")
    assert item["status"] == "ready"
    assert item["width"] == 8
    assert item["height"] == 6

    pool = image_service.add_images([], uploaded, app_dir)
    pool = image_service.set_main_image(pool, item["id"], app_dir)
    assert pool[0]["is_main"] is True

    pool = image_service.set_sku_image(pool, item["id"], "SKU-BLUE", app_dir)
    assert pool[0]["is_sku"] is True
    assert pool[0]["sku"] == "SKU-BLUE"

    pool = image_service.replace_image(pool, item["id"], {"path": str(replacement), "product_id": "pytest-stage3a"}, app_dir)
    replaced_path = app_dir / pool[0]["path"]
    assert replaced_path.exists()
    assert pool[0]["is_main"] is True
    assert pool[0]["is_sku"] is True

    pool = image_service.delete_images(pool, [pool[0]["id"]], app_dir)
    assert pool == []
    assert_no_old_path(uploaded, old_path_markers)


def test_upload_data_url_returns_media_asset_shape(app_dir: Path, old_path_markers: tuple[str, ...]) -> None:
    raw = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode("ascii")
    items = image_service.upload_images(
        app_dir,
        [{"filename": "tiny.png", "data_url": f"data:image/png;base64,{raw}", "platforms": ["mercadolibre"]}],
        "pytest-stage3a-dataurl",
    )
    assert items
    item = items[0]
    assert {"id", "asset_id", "path", "preview_url", "platforms", "status"}.issubset(item.keys())
    assert (app_dir / item["path"]).exists()
    assert item["status"] == "ready"
    assert_no_old_path(item, old_path_markers)
