from __future__ import annotations

from typing import Any

import pytest

from erp_web.runtime_units import draft_publish_context
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


def test_ozon_target_round_trip_preserves_description_category_id() -> None:
    draft = {
        "platform": "ozon",
        "site": "global",
        "target_sites": [
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "currency": "RUB",
                "category_id": "91443",
                "description_category_id": "17039635",
            }
        ],
    }

    target = draft_publish_targets(draft)[0]
    target_draft = draft_for_publish_target(draft, target)
    merged = merge_target_listing_into_draft(draft, target, target_draft)

    assert target["description_category_id"] == "17039635"
    assert target_draft["description_category_id"] == "17039635"
    assert merged["target_sites"][0]["description_category_id"] == "17039635"


def test_publish_target_discards_recursive_precheck_target_history() -> None:
    recursive_history = {
        "platform": "ozon",
        "site": "global",
        "language": "ru-RU",
        "category_id": "91443",
        "description_category_id": "17039635",
        "category_precheck": {"large": "history"},
        "last_precheck_target": {
            "platform": "ozon",
            "site": "global",
            "last_precheck_target": {"platform": "ozon"},
        },
    }
    draft = {
        "platform": "ozon",
        "site": "global",
        "language": "ru-RU",
        "target_sites": [
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "category_id": "91443",
                "description_category_id": "17039635",
                "last_precheck_target": recursive_history,
            }
        ],
    }

    target = draft_publish_targets(draft)[0]
    target_draft = draft_for_publish_target(draft, target)

    assert target["last_precheck_target"] == {
        "platform": "ozon",
        "site": "global",
        "language": "ru-RU",
        "category_id": "91443",
        "description_category_id": "17039635",
    }
    assert target["listing_currency"] == ""
    assert target["currency_resolution"]["mode"] == "unresolved"
    assert "last_precheck_target" not in target["last_precheck_target"]
    assert target_draft["target_sites"] == [target]
    assert "target_site" not in target_draft


def test_publish_target_requires_explicit_identity_when_multiple_targets_exist() -> None:
    draft = {
        "platform": "mercadolibre",
        "site": "MLM",
        "target_sites": [
            {
                "platform": "mercadolibre",
                "site": "MLM",
                "language": "es-MX",
                "currency": "MXN",
            },
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
                "currency": "RUB",
            },
        ],
    }

    target, error, status = draft_publish_context._select_target(draft, "", "")

    assert target == {}
    assert status == 400
    assert error is not None
    assert error["error_code"] == "DRAFT_TARGET_AMBIGUOUS"
    assert [item["platform"] for item in error["allowed_targets"]] == [
        "mercadolibre",
        "ozon",
    ]


def test_publish_target_requires_site_when_platform_has_multiple_sites() -> None:
    draft = {
        "platform": "mercadolibre",
        "site": "MLM",
        "target_sites": [
            {"platform": "mercadolibre", "site": "MLM"},
            {"platform": "mercadolibre", "site": "MLB"},
        ],
    }

    target, error, status = draft_publish_context._select_target(
        draft,
        "mercadolibre",
        "",
    )

    assert target == {}
    assert status == 400
    assert error is not None
    assert error["error_code"] == "DRAFT_TARGET_SITE_AMBIGUOUS"
    assert [item["site"] for item in error["allowed_targets"]] == [
        "MLM",
        "MLB",
    ]


def test_publish_target_can_resolve_unique_site_without_guessing_platform() -> None:
    draft = {
        "target_sites": [
            {"platform": "mercadolibre", "site": "MLM"},
            {"platform": "ozon", "site": "global"},
        ],
    }

    target, error, status = draft_publish_context._select_target(
        draft,
        "",
        "global",
    )

    assert status == 200
    assert error is None
    assert target["platform"] == "ozon"
    assert target["site"] == "global"


def test_publish_target_rejects_unknown_explicit_site_instead_of_defaulting() -> None:
    draft = {
        "target_sites": [
            {"platform": "mercadolibre", "site": "MLM"},
        ],
    }

    target, error, status = draft_publish_context._select_target(
        draft,
        "mercadolibre",
        "UNKNOWN",
    )

    assert target == {}
    assert status == 400
    assert error is not None
    assert error["error_code"] == "TARGET_NOT_IN_DRAFT"


def test_merge_target_listing_uses_explicit_app_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_context = object()
    seen_contexts: list[Any] = []
    target = {"platform": "ozon", "site": "global"}

    def fake_targets(_draft: dict, *, context=None) -> list[dict]:
        seen_contexts.append(context)
        return [target]

    monkeypatch.setattr(
        draft_publish_context,
        "draft_publish_targets",
        fake_targets,
    )

    merged = draft_publish_context.merge_target_listing_into_draft(
        {"platform": "ozon", "site": "global"},
        target,
        {"category_id": "91443"},
        context=explicit_context,
    )

    assert seen_contexts == [explicit_context]
    assert merged["target_sites"][0]["category_id"] == "91443"


@pytest.mark.parametrize(
    "save_function_name",
    ["save_draft_precheck_result", "save_draft_target_listing_result"],
)
def test_publish_result_save_reuses_one_explicit_app_context(
    monkeypatch: pytest.MonkeyPatch,
    save_function_name: str,
) -> None:
    explicit_context = object()
    seen_contexts: list[Any] = []
    publish_context = {
        "draft": {"platform": "ozon", "site": "global"},
        "target": {"platform": "ozon", "site": "global"},
    }

    def fake_merge(draft, _target, _updates, *, context=None):
        seen_contexts.append(context)
        return draft

    def fake_save(draft, _publish_context, *, context=None):
        seen_contexts.append(context)
        return draft

    monkeypatch.setattr(
        draft_publish_context,
        "merge_target_listing_into_draft",
        fake_merge,
    )
    monkeypatch.setattr(
        draft_publish_context,
        "_save_updated_draft",
        fake_save,
    )

    payload = (
        {"ok": True, "errors": [], "warnings": []}
        if save_function_name == "save_draft_precheck_result"
        else {"category_id": "91443"}
    )
    getattr(draft_publish_context, save_function_name)(
        publish_context,
        payload,
        context=explicit_context,
    )

    assert seen_contexts == [explicit_context, explicit_context]
