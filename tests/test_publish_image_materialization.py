# -*- coding: utf-8 -*-
"""发布预检前的本地图片物化（Yandex/Ozon local_static provider）回归测试。"""

from __future__ import annotations

from pathlib import Path

from erp_web.runtime_units import publish_workflows
from tests.runtime_test_utils import temp_app_context


def _yandex_product(image_path: Path) -> dict:
    return {
        "product_id": "",
        "name": "便携风扇",
        "brand": "BrandX",
        "model": "ModelY",
        "source": {
            "source_url": "https://detail.1688.com/offer/materialize-test.html",
            "source_platform": "1688",
            "title": "便携风扇",
            "price": "12.34",
            "currency": "CNY",
            "image_pool": [
                {
                    "id": "image-1",
                    "path": str(image_path),
                    "preview_url": str(image_path),
                    "origin": "source",
                    "usage": "main",
                    "platforms": ["yandex"],
                    "is_main": True,
                    "selected": True,
                    "order": 0,
                }
            ],
        },
        "drafts": {
            "yandex": {
                "draft_id": "draft-yandex-materialize",
                "enabled": True,
                "title": "Ручной вентилятор",
                "description": "Описание товара",
                "category_id": "",
                "sku": "SKU-Y1",
                "stock": "10",
                "brand": "BrandX",
                "model": "ModelY",
                "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
                "site": "global",
                "target_sites": [
                    {
                        "platform": "yandex",
                        "site": "global",
                        "language": "ru-RU",
                        "listing_currency": "RUB",
                    }
                ],
                "pricing": {
                    "targets": {
                        "yandex:global": {
                            "listing_currency": "RUB",
                            "applied_price": {"amount": "1299", "currency": "RUB"},
                        }
                    }
                },
                "status": "copy_ready",
            }
        },
    }


def test_precheck_materializes_local_images_before_yandex_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """本地图片先物化再校验；物化结果回写商品图片池。"""

    image = tmp_path / "incoming" / "main.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"champion-yandex-image")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_PROVIDER", "local_static")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_BASE_URL", "https://tunnel.example.test")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_ROOT", str(tmp_path / "public"))

    with temp_app_context(tmp_path / "app") as context:
        saved = context.products.save_product(_yandex_product(image))
        product_id = str(saved["product_id"])
        draft_id = str(saved["drafts"]["yandex"]["draft_id"])

        response, status = publish_workflows.precheck_publish_payload(
            {"draft_id": draft_id, "platform": "yandex", "site": "global"}
        )

        assert status == 200
        codes = [
            str(item.get("code") or "")
            for item in response["platforms"]["yandex"]["errors"]
        ]
        assert "IMAGE_NOT_PUBLIC" not in codes
        assert "IMAGE_MISSING" not in codes

        persisted = context.db.load_product_model(product_id)
        pool = persisted["source"]["image_pool"]
        item = next(entry for entry in pool if entry.get("id") == "image-1")
        assert str(item.get("url") or "").startswith(
            "https://tunnel.example.test/assets/"
        )
        assert str(item.get("storage_key") or "").startswith("assets/")
        assert item.get("delivery_provider") == "local_static"

        materialized = (
            tmp_path / "public" / str(item["storage_key"])
        )
        assert materialized.is_file()


def test_precheck_reports_provider_error_when_local_images_cannot_materialize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """未配置 HTTPS provider 时，预检错误必须指向 provider 配置而不是泛化文案。"""

    image = tmp_path / "incoming" / "main.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"champion-yandex-image")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_PROVIDER", "existing_url")
    monkeypatch.delenv("ERP_IMAGE_HTTPS_BASE_URL", raising=False)

    with temp_app_context(tmp_path / "app") as context:
        saved = context.products.save_product(_yandex_product(image))
        draft_id = str(saved["drafts"]["yandex"]["draft_id"])

        response, status = publish_workflows.precheck_publish_payload(
            {"draft_id": draft_id, "platform": "yandex", "site": "global"}
        )

        assert status == 200
        message = "；".join(
            str(item.get("message") or "")
            for item in response["platforms"]["yandex"]["errors"]
        )
        assert "尚未配置 HTTPS provider" in message
        assert "ERP_IMAGE_HTTPS_PROVIDER=local_static" in message
