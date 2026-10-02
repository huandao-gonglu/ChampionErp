# -*- coding: utf-8 -*-
"""普通发布预检的图片只读边界回归。"""

from __future__ import annotations

from pathlib import Path

from erp_web.runtime_units import publish_workflows
from tests.runtime_test_utils import temp_app_context


def _yandex_product(image_path: Path) -> dict:
    return {
        "product_id": "",
        "sku_items": [{"id": "sku-1", "name": "便携风扇", "cost_cny": "12.34"}],
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
                "sku_items": [{"sku_id": "sku-1", "selected": True, "sku": "SKU-Y1", "stock": "10"}],
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



def test_precheck_does_not_upload_local_images(tmp_path, monkeypatch):
    from tests.image_hosting_test_utils import configure, FakeS3, image_bytes
    image = tmp_path / "main.png"
    image.write_bytes(image_bytes())
    remote = FakeS3(monkeypatch)
    with temp_app_context(tmp_path / "app") as context:
        configure(context)
        saved = context.products.save_product(_yandex_product(image))
        before = context.db.load_product_model(saved["product_id"])
        response, status = publish_workflows.precheck_publish_payload({"draft_id": saved["drafts"]["yandex"]["draft_id"], "platform": "yandex", "site": "global"})
        assert status == 200
        assert "IMAGE_NOT_PREPARED" in [item["code"] for item in response["platforms"]["yandex"]["errors"]]
        assert context.db.load_product_model(saved["product_id"])["source"]["image_pool"] == before["source"]["image_pool"]
        assert not remote.calls


def test_unconfigured_local_source_has_actionable_error(tmp_path):
    from tests.image_hosting_test_utils import image_bytes
    image = tmp_path / "main.png"; image.write_bytes(image_bytes())
    with temp_app_context(tmp_path / "app") as context:
        saved = context.products.save_product(_yandex_product(image))
        response, status = publish_workflows.precheck_publish_payload({"draft_id": saved["drafts"]["yandex"]["draft_id"], "platform": "yandex", "site": "global"})
        assert status == 200
        assert "IMAGE_HOSTING_NOT_CONFIGURED" in [item["code"] for item in response["platforms"]["yandex"]["errors"]]
