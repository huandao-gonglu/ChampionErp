from __future__ import annotations

from copy import deepcopy

import pytest

from erp_web.schemas.product_capabilities import (
    ProductAttributesUpdateRequest,
    ProductImagesPrepareRequest,
    ProductReadRequest,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.runtime_units.product_capabilities import (
    prepare_product_images,
    read_product,
    update_product_attributes,
)


class _Products:
    def __init__(self) -> None:
        self.product = {
            "product_id": "product-1",
            "name": "Portable fan",
            "brand": "Generic",
            "model": "F-1",
            "stock": "8",
            "materials": ["ABS"],
            "selling_points": ["Reusable"],
            "package_includes": ["Fan", "Cable"],
            "dimensions": "20x15x10cm",
            "weight_kg": "0.5",
            "source": {
                "source_platform": "1688",
                "source_url": "https://example.com/product",
                "image_pool": [
                    {
                        "id": "image-1",
                        "url": "https://img.example/1.jpg",
                        "origin": "source",
                        "status": "ready",
                        "selected": True,
                        "is_main": True,
                        "order": 0,
                        "platforms": ["mercadolibre"],
                    },
                    {
                        "id": "image-2",
                        "url": "https://img.example/2.jpg",
                        "origin": "source",
                        "status": "ready",
                        "selected": True,
                        "order": 1,
                        "platforms": ["mercadolibre"],
                    },
                ],
            },
            "workflow_statuses": {"mercadolibre": "claimed"},
        }
        self.draft = {
            "draft_id": "draft-1",
            "product_id": "product-1",
            "source_product_id": "product-1",
            "platform": "mercadolibre",
            "site": "MLM",
            "title": "Portable fan",
            "description": "Description",
            "status": "ready_to_publish",
            "attributes": {"BRAND": "Generic"},
            "images": [],
            "validation_errors": [{"field": "attributes.MODEL"}],
            "category_precheck": {"ok": True},
            "last_precheck": {"ok": True},
            "last_precheck_target": {
                "platform": "mercadolibre",
                "site": "MLM",
            },
            "last_publish_task": {"job_id": "job-old"},
            "publish_status": "ready",
            "target_sites": [
                {
                    "platform": "mercadolibre",
                    "site": "MLM",
                    "language": "es-MX",
                    "market_currency": "MXN",
                    "listing_currency": "MXN",
                    "attributes": {"BRAND": "Generic"},
                    "validation_errors": [{"field": "attributes.MODEL"}],
                    "category_precheck": {"ok": True},
                    "last_precheck": {"ok": True},
                    "last_precheck_target": {
                        "platform": "mercadolibre",
                        "site": "MLM",
                    },
                    "last_publish_task": {"job_id": "job-old"},
                    "publish_status": "ready",
                    "status": "ready_to_publish",
                }
            ],
        }
        self.save_calls = 0

    def load_product_from_index(
        self,
        product_id: str = "",
        file_path: str = "",
    ) -> dict:
        return deepcopy(self.product)

    def load_draft_detail_from_index(self, draft_id: str):
        if draft_id != self.draft["draft_id"]:
            return {}, {"error": "草稿不存在", "error_code": "DRAFT_NOT_FOUND"}, 404
        return {
            "draft": deepcopy(self.draft),
            "productContext": {"raw": deepcopy(self.product)},
        }, None, 200

    def save_draft_detail(self, draft_payload: dict):
        self.save_calls += 1
        self.draft = deepcopy(draft_payload)
        return {"draft": deepcopy(self.draft)}, None, 200

    def draft_workflow_status(
        self,
        product: dict,
        platform: str = "mercadolibre",
    ) -> str:
        return "claimed"


def test_attributes_update_sets_values_and_skips_identical_retry() -> None:
    products = _Products()
    request = ProductAttributesUpdateRequest(
        draft_id="draft-1",
        platform="mercadolibre",
        site="MLM",
        updates={"MODEL": "F-1"},
    )

    first = update_product_attributes(request, product_store=products)
    second = update_product_attributes(request, product_store=products)

    assert first.changed is True
    assert first.changed_keys == ["MODEL"]
    assert second.changed is False
    assert products.save_calls == 1
    assert products.draft["target_sites"][0]["attributes"] == {
        "BRAND": "Generic",
        "MODEL": "F-1",
    }
    assert products.draft["target_sites"][0]["category_precheck"] == {}
    assert products.draft["target_sites"][0]["last_precheck"] == {}
    assert products.draft["target_sites"][0]["last_precheck_target"] == {}
    assert products.draft["target_sites"][0]["last_publish_task"] == {}
    assert products.draft["target_sites"][0]["validation_errors"] == []
    assert products.draft["target_sites"][0]["publish_status"] == ""
    assert products.draft["target_sites"][0]["status"] == "claimed"


def test_images_prepare_uses_persisted_selected_assets_and_skips_retry() -> None:
    products = _Products()
    request = ProductImagesPrepareRequest(draft_id="draft-1")

    first = prepare_product_images(request, product_store=products)
    second = prepare_product_images(request, product_store=products)

    assert first.changed is True
    assert first.image_asset_ids == ["image-1", "image-2"]
    assert second.changed is False
    assert products.save_calls == 1
    assert products.draft["category_precheck"] == {}
    assert products.draft["last_precheck"] == {}
    assert products.draft["last_precheck_target"] == {}
    assert products.draft["last_publish_task"] == {}
    assert products.draft["validation_errors"] == []
    assert products.draft["publish_status"] == ""
    assert products.draft["status"] == "claimed"


def test_images_prepare_rejects_unpersisted_asset_id() -> None:
    products = _Products()

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_product_images(
            ProductImagesPrepareRequest(
                draft_id="draft-1",
                asset_ids=["missing"],
            ),
            product_store=products,
        )

    assert exc_info.value.code == "PRODUCT_IMAGE_ASSET_NOT_FOUND"
    assert exc_info.value.options == ("image-1", "image-2")
    assert exc_info.value.input_type == "string_list"


def test_images_prepare_marks_required_asset_ids_as_string_list() -> None:
    products = _Products()
    products.product["source"]["image_pool"] = []

    with pytest.raises(CapabilityInputRequired) as exc_info:
        prepare_product_images(
            ProductImagesPrepareRequest(draft_id="draft-1"),
            product_store=products,
        )

    assert exc_info.value.code == "PRODUCT_IMAGES_REQUIRED"
    assert exc_info.value.key == "asset_ids"
    assert exc_info.value.input_type == "string_list"


def test_images_prepare_invalidates_every_target_in_one_save() -> None:
    products = _Products()
    products.draft["target_sites"].append(
        {
            "platform": "ozon",
            "site": "global",
            "language": "ru-RU",
            "market_currency": "RUB",
            "listing_currency": "RUB",
            "attributes": {"BRAND": "Generic"},
            "validation_errors": [{"field": "attributes.MODEL"}],
            "category_precheck": {"ok": True},
            "last_precheck": {"ok": True},
            "last_precheck_target": {"platform": "ozon", "site": "global"},
            "last_publish_task": {"job_id": "job-old-ozon"},
            "publish_status": "ready",
            "status": "ready_to_publish",
        }
    )

    result = prepare_product_images(
        ProductImagesPrepareRequest(draft_id="draft-1"),
        product_store=products,
    )

    assert result.changed is True
    assert products.save_calls == 1
    for target in products.draft["target_sites"]:
        assert target["category_precheck"] == {}
        assert target["last_precheck"] == {}
        assert target["last_precheck_target"] == {}
        assert target["last_publish_task"] == {}
        assert target["validation_errors"] == []
        assert target["publish_status"] == ""
        assert target["status"] == "claimed"


def test_attributes_update_requires_target_when_draft_has_multiple_targets() -> None:
    products = _Products()
    products.draft["target_sites"].append(
        {
            "platform": "ozon",
            "site": "global",
            "language": "ru-RU",
            "market_currency": "RUB",
            "listing_currency": "RUB",
            "attributes": {"BRAND": "Generic"},
        }
    )

    with pytest.raises(CapabilityInputRequired) as exc_info:
        update_product_attributes(
            ProductAttributesUpdateRequest(
                draft_id="draft-1",
                updates={"MODEL": "F-1"},
            ),
            product_store=products,
        )

    assert exc_info.value.code == "DRAFT_TARGET_AMBIGUOUS"
    assert exc_info.value.key == "platform"
    assert exc_info.value.options == ("mercadolibre", "ozon")
    assert exc_info.value.input_type == "select"
    assert products.save_calls == 0


def test_attributes_update_only_checks_selected_target_publish_state() -> None:
    products = _Products()
    products.draft["publish_status"] = "published"
    products.draft["status"] = "published"
    products.draft["target_sites"][0]["publish_status"] = "published"
    products.draft["target_sites"][0]["status"] = "published"
    products.draft["target_sites"].append(
        {
            "platform": "ozon",
            "site": "global",
            "language": "ru-RU",
            "market_currency": "RUB",
            "listing_currency": "RUB",
            "attributes": {"BRAND": "Generic"},
            "category_precheck": {"ok": True},
            "last_precheck": {"ok": True},
            "last_precheck_target": {"platform": "ozon", "site": "global"},
            "last_publish_task": {"job_id": "job-old-ozon"},
            "publish_status": "ready",
            "status": "ready_to_publish",
        }
    )

    result = update_product_attributes(
        ProductAttributesUpdateRequest(
            draft_id="draft-1",
            platform="ozon",
            site="global",
            updates={"MODEL": "F-1"},
        ),
        product_store=products,
    )

    assert result.changed is True
    assert products.save_calls == 1
    primary, selected = products.draft["target_sites"]
    assert primary["publish_status"] == "published"
    assert primary["last_publish_task"] == {"job_id": "job-old"}
    assert selected["attributes"] == {"BRAND": "Generic", "MODEL": "F-1"}
    assert selected["publish_status"] == ""
    assert selected["last_publish_task"] == {}


def test_images_prepare_rejects_when_another_shared_target_is_published() -> None:
    products = _Products()
    products.draft["target_sites"].append(
        {
            "platform": "ozon",
            "site": "global",
            "language": "ru-RU",
            "market_currency": "RUB",
            "listing_currency": "RUB",
            "publish_status": "published",
            "status": "published",
        }
    )

    with pytest.raises(BusinessCapabilityError) as exc_info:
        prepare_product_images(
            ProductImagesPrepareRequest(draft_id="draft-1"),
            product_store=products,
        )

    assert exc_info.value.code == "DRAFT_ALREADY_PUBLISHED"
    assert products.save_calls == 0


@pytest.mark.parametrize("operation", ["attributes", "images"])
def test_product_mutations_reject_published_draft(operation: str) -> None:
    products = _Products()
    products.draft["target_sites"][0]["publish_status"] = "published"
    products.draft["target_sites"][0]["status"] = "published"

    with pytest.raises(BusinessCapabilityError) as exc_info:
        if operation == "attributes":
            update_product_attributes(
                ProductAttributesUpdateRequest(
                    draft_id="draft-1",
                    platform="mercadolibre",
                    site="MLM",
                    updates={"MODEL": "F-1"},
                ),
                product_store=products,
            )
        else:
            prepare_product_images(
                ProductImagesPrepareRequest(draft_id="draft-1"),
                product_store=products,
            )

    assert exc_info.value.code == "DRAFT_ALREADY_PUBLISHED"
    assert products.save_calls == 0


def test_product_read_rejects_store_default_fallback() -> None:
    products = _Products()

    with pytest.raises(BusinessCapabilityError) as exc_info:
        read_product(
            ProductReadRequest(product_id="does-not-exist"),
            product_store=products,
        )

    assert exc_info.value.code == "PRODUCT_NOT_FOUND"


def test_product_read_returns_compact_draft_facts() -> None:
    result = read_product(
        ProductReadRequest(draft_id="draft-1"),
        product_store=_Products(),
    )

    assert result.product.product_id == "product-1"
    assert result.product.source_image_count == 2
    assert result.product.materials == ["ABS"]
    assert result.product.selling_points == ["Reusable"]
    assert result.product.package_includes == ["Fan", "Cable"]
    assert result.product.dimensions == "20x15x10cm"
    assert result.product.weight_kg == "0.5"
    assert result.draft is not None
    assert result.draft.draft_id == "draft-1"
    assert result.draft.attribute_ids == ["BRAND"]
