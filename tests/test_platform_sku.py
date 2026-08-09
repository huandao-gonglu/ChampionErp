from __future__ import annotations

from erp_web.product_model import (
    generated_platform_sku,
    is_placeholder_sku,
    normalize_platform_draft,
)


def test_generated_platform_sku_is_stable_per_draft_and_unique_between_drafts() -> None:
    first = generated_platform_sku(
        "ozon",
        product_id="7f5be25d6e2b4d96",
        draft_id="d111111111111",
    )
    repeated = generated_platform_sku(
        "ozon",
        product_id="7f5be25d6e2b4d96",
        draft_id="d111111111111",
    )
    second = generated_platform_sku(
        "ozon",
        product_id="7f5be25d6e2b4d96",
        draft_id="d222222222222",
    )

    assert first == repeated
    assert first == "OZ-7F5BE25D-D11111111111"
    assert second != first


def test_unpublished_placeholder_sku_is_replaced_but_published_sku_is_preserved() -> None:
    active = normalize_platform_draft(
        {
            "draft_id": "d123456789abc",
            "product_id": "product-1",
            "status": "claimed",
            "sku": "其他",
        },
        "ozon",
        {"product_id": "product-1"},
    )
    published = normalize_platform_draft(
        {
            "draft_id": "d123456789abc",
            "product_id": "product-1",
            "status": "published",
            "publish_status": "published",
            "sku": "其他",
        },
        "ozon",
        {"product_id": "product-1"},
    )

    assert is_placeholder_sku("其他") is True
    assert active["sku"].startswith("OZ-PRODUCT1-")
    assert published["sku"] == "其他"


def test_manual_platform_sku_is_preserved() -> None:
    draft = normalize_platform_draft(
        {
            "draft_id": "d123456789abc",
            "product_id": "product-1",
            "status": "claimed",
            "sku": "MY-SKU-100",
        },
        "yandex",
        {"product_id": "product-1"},
    )

    assert draft["sku"] == "MY-SKU-100"
