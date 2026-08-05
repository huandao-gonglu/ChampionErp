from __future__ import annotations

import hashlib
from pathlib import Path

from erp_web.context import AppPaths
from erp_web.services.image_delivery_service import (
    ImageDeliveryService,
    ImageDeliverySettings,
)


def _product(image_path: Path) -> dict:
    return {
        "product_id": "product-image-delivery",
        "source": {
            "image_pool": [
                {
                    "id": "image-main",
                    "path": str(image_path),
                    "selected": True,
                    "platforms": ["ozon"],
                    "is_main": True,
                }
            ]
        },
        "drafts": {
            "ozon": {
                "images": [
                    {"asset_id": "image-main", "role": "main", "order": 0}
                ]
            }
        },
    }


def _service(
    tmp_path: Path,
    *,
    provider: str,
    base_url: str = "",
) -> ImageDeliveryService:
    paths = AppPaths.from_app_dir(tmp_path)
    settings = ImageDeliverySettings(
        provider=provider,
        public_base_url=base_url,
        public_root=paths.images_dir / "public",
    )
    return ImageDeliveryService(paths, settings_provider=lambda _paths: settings)


def test_local_static_provider_copies_image_and_returns_https_url(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "main.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"champion-image")
    service = _service(
        tmp_path,
        provider="local_static",
        base_url="https://temporary.trycloudflare.com",
    )

    prepared = service.prepare_product(_product(source), "ozon")

    item = prepared["source"]["image_pool"][0]
    digest = hashlib.sha256(b"champion-image").hexdigest()
    expected_key = f"assets/{digest[:2]}/{digest}.jpg"
    assert item["path"] == str(source)
    assert item["storage_key"] == expected_key
    assert item["content_sha256"] == digest
    assert item["delivery_provider"] == "local_static"
    assert item["url"] == f"https://temporary.trycloudflare.com/{expected_key}"
    assert (tmp_path / "data" / "images" / "public" / expected_key).read_bytes() == b"champion-image"


def test_managed_url_is_recomputed_when_https_base_url_changes(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "main.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same-image")
    first = _service(
        tmp_path,
        provider="local_static",
        base_url="https://first.trycloudflare.com",
    ).prepare_product(_product(source), "ozon")

    second = _service(
        tmp_path,
        provider="local_static",
        base_url="https://img.example.com",
    ).prepare_product(first, "ozon")

    first_item = first["source"]["image_pool"][0]
    second_item = second["source"]["image_pool"][0]
    assert second_item["storage_key"] == first_item["storage_key"]
    assert second_item["url"].startswith("https://img.example.com/assets/")
    assert "first.trycloudflare.com" not in second_item["url"]


def test_existing_public_url_does_not_depend_on_configured_provider(tmp_path: Path) -> None:
    product = _product(tmp_path / "missing.jpg")
    item = product["source"]["image_pool"][0]
    item["url"] = "https://supplier.example.com/image.jpg"
    service = _service(tmp_path, provider="existing_url")

    prepared = service.prepare_product(product, "ozon")

    prepared_item = prepared["source"]["image_pool"][0]
    assert prepared_item["url"] == "https://supplier.example.com/image.jpg"
    assert not prepared_item.get("storage_key")
    assert not prepared_item.get("delivery_error")


def test_local_image_without_https_provider_is_left_for_precheck_to_reject(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "main.webp"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"local-image")
    service = _service(tmp_path, provider="existing_url")

    prepared = service.prepare_product(_product(source), "ozon")

    item = prepared["source"]["image_pool"][0]
    assert item["url"] == ""
    assert "尚未配置 HTTPS provider" in item["delivery_error"]

