from __future__ import annotations

from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    draft_publish_targets,
    merge_target_listing_into_draft,
)


def test_target_listing_round_trip_preserves_category_attribute_schema() -> None:
    schema = {
        "version": 1,
        "platform": "mercadolibre",
        "site": "MLM",
        "category_id": "MLM123",
        "category_path": "Hogar / Ventiladores",
        "source": "mercadolibre_live",
        "fetched_at": "2026-08-04T00:00:00Z",
        "required": [
            {
                "id": "9048",
                "name": "Tipo de cuerpo",
                "required": True,
                "options": [],
            }
        ],
        "optional": [],
    }
    draft = {
        "platform": "mercadolibre",
        "site": "MLM",
        "language": "es-MX",
        "currency": "MXN",
        "target_sites": [
            {
                "platform": "mercadolibre",
                "site": "MLM",
                "language": "es-MX",
                "currency": "MXN",
                "category_id": "MLM123",
                "category_attribute_schema": schema,
                "attributes": {},
            }
        ],
    }

    target = draft_publish_targets(draft)[0]
    target_draft = draft_for_publish_target(draft, target)
    merged = merge_target_listing_into_draft(
        draft,
        target,
        {"attributes": {"9048": "Compacto"}},
    )

    assert target["category_attribute_schema"] == schema
    assert target_draft["category_attribute_schema"] == schema
    assert merged["target_sites"][0]["category_attribute_schema"] == schema
    assert merged["target_sites"][0]["attributes"] == {"9048": "Compacto"}
