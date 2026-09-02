from __future__ import annotations

from typing import Any

import pytest

from erp_web.runtime_units import draft_publish_context
from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    draft_publish_targets,
    merge_target_listing_into_draft,
)
from erp_web.product_model import normalize_platform_draft
from erp_web.services.listing_currency_service import compute_currency_fingerprint


def test_target_listing_round_trip_drops_retired_category_schema() -> None:
    schema = {
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
        "category_attribute_schema": schema,
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

    # 平台规则副本不再随目标往返；类目身份与属性值照常保留。
    assert "category_attribute_schema" not in target
    assert "category_attribute_schema" not in target_draft
    assert target["category_id"] == "MLM123"
    assert merged["target_sites"][0]["attributes"] == {"9048": "Compacto"}


def test_cbt_sales_targets_are_canonical_target_fields_and_project_only_for_publish() -> None:
    draft = normalize_platform_draft(
        {
            "platform": "mercadolibre",
            "site": "CBT",
            # 根字段不是持久化契约，必须被删除。
            "sites_to_sell": [
                {"site_id": "MLB", "logistic_type": "remote"}
            ],
            "marketplace_titles": {"MLB": "根字段不得持久化"},
            "target_sites": [
                {
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "sitesToSell": [
                        {
                            "siteId": " mlm ",
                            "logisticType": " REMOTE ",
                            "ignored": "value",
                        },
                        {"site_id": "MLM", "logistic_type": "remote"},
                    ],
                    "marketplace_titles": {
                        " mlm ": "  Producto localizado  ",
                        "mlb": "Produto localizado",
                        "mlc": "   ",
                    },
                }
            ],
        },
        "mercadolibre",
    )

    assert "sites_to_sell" not in draft
    assert "marketplace_titles" not in draft
    assert draft["target_sites"][0]["sites_to_sell"] == [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    assert "marketplace_titles" not in draft["target_sites"][0]

    target = draft_publish_targets(draft)[0]
    projection = draft_for_publish_target(draft, target)
    merged = merge_target_listing_into_draft(draft, target, projection)

    assert projection["sites_to_sell"] == [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    assert "marketplace_titles" not in projection
    assert "sites_to_sell" not in merged
    assert "marketplace_titles" not in merged
    assert merged["target_sites"][0]["sites_to_sell"] == [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    assert "marketplace_titles" not in merged["target_sites"][0]


@pytest.mark.parametrize("language", ["es", "pt-BR"])
def test_cbt_target_preserves_selected_copy_language(language: str) -> None:
    draft = normalize_platform_draft(
        {
            "platform": "mercadolibre",
            "site": "CBT",
            "language": language,
            "target_sites": [
                {
                    "platform": "mercadolibre",
                    "site": "CBT",
                    "language": language,
                }
            ],
        },
        "mercadolibre",
    )

    assert draft["language"] == language
    assert draft["target_sites"][0]["language"] == language
    assert draft_publish_targets(draft)[0]["language"] == language


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


def test_first_publish_target_preserves_explicit_empty_listing_fields() -> None:
    draft = {
        "platform": "yandex",
        "site": "global",
        "language": "ru-RU",
        "category_id": "ROOT-CATEGORY",
        "description_category_id": "ROOT-DESCRIPTION-CATEGORY",
        "category_path": "根类目",
        "attributes": {"ROOT_ATTRIBUTE": "旧值"},
        "validation_errors": ["旧校验错误"],
        "category_precheck": {"ok": True},
        "publish_status": "ready",
        "status": "ready_to_publish",
        "last_precheck": {"ok": True},
        "last_precheck_target": {
            "platform": "yandex",
            "site": "global",
        },
        "last_publish_task": {"job_id": "root-job"},
        "target_sites": [
            {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
                "category_id": "",
                "description_category_id": "",
                "category_path": "",
                "attributes": {},
                "validation_errors": [],
                "category_precheck": {},
                "publish_status": "",
                "status": "",
                "last_precheck": {},
                "last_precheck_target": {},
                "last_publish_task": {},
            }
        ],
    }

    target = draft_publish_targets(draft)[0]

    assert target["category_id"] == ""
    assert target["description_category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}
    assert target["validation_errors"] == []
    assert target["category_precheck"] == {}
    assert target["publish_status"] == ""
    assert target["status"] == ""
    assert target["last_precheck"] == {}
    assert target["last_precheck_target"] == {}
    assert target["last_publish_task"] == {}


def test_multi_market_publish_targets_do_not_inherit_missing_root_fields() -> None:
    draft = {
        "platform": "yandex",
        "site": "global",
        "language": "ru-RU",
        "category_id": "YANDEX-ROOT",
        "description_category_id": "ROOT-DESCRIPTION-CATEGORY",
        "category_path": "Yandex 根类目",
        "attributes": {"ROOT_ATTRIBUTE": "旧值"},
        "validation_errors": ["旧校验错误"],
        "category_precheck": {"ok": True},
        "publish_status": "ready",
        "status": "ready_to_publish",
        "last_precheck": {"ok": True},
        "last_precheck_target": {
            "platform": "yandex",
            "site": "global",
        },
        "last_publish_task": {"job_id": "root-job"},
        "target_sites": [
            {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
            },
            {
                "platform": "ozon",
                "site": "global",
                "language": "ru-RU",
            },
        ],
    }

    targets = draft_publish_targets(draft)

    assert [target["platform"] for target in targets] == ["yandex", "ozon"]
    for target in targets:
        assert target["category_id"] == ""
        assert target["description_category_id"] == ""
        assert target["category_path"] == ""
        assert target["attributes"] == {}
        assert target["validation_errors"] == []
        assert target["category_precheck"] == {}
        assert target["publish_status"] == ""
        assert target["status"] == ""
        assert target["last_precheck"] == {}
        assert target["last_precheck_target"] == {}
        assert target["last_publish_task"] == {}


def test_existing_target_missing_language_uses_site_default() -> None:
    target = draft_publish_targets(
        {
            "platform": "yandex",
            "site": "global",
            "language": "es-MX",
            "target_sites": [
                {
                    "platform": "yandex",
                    "site": "global",
                }
            ],
        }
    )[0]

    assert target["language"] == "ru-RU"


def test_draft_without_targets_synthesizes_identity_only() -> None:
    draft = {
        "platform": "yandex",
        "site": "global",
        "language": "es-MX",
        "listing_currency": "ROOT-RUB",
        "currency_fingerprint": "root-fingerprint",
        "category_id": "YANDEX-ROOT",
        "description_category_id": "ROOT-DESCRIPTION-CATEGORY",
        "category_path": "根类目",
        "attributes": {"ROOT_ATTRIBUTE": "旧值"},
        "validation_errors": ["旧校验错误"],
        "category_precheck": {"ok": True},
        "publish_status": "ready",
        "status": "ready_to_publish",
        "last_precheck": {"ok": True},
        "last_precheck_target": {
            "platform": "yandex",
            "site": "global",
        },
        "last_publish_task": {"job_id": "root-job"},
        "sites_to_sell": [
            {"site_id": "MLM", "logistic_type": "remote"}
        ],
    }

    target = draft_publish_targets(draft)[0]

    assert target["platform"] == "yandex"
    assert target["site"] == "global"
    assert target["language"] == "ru-RU"
    assert target["listing_currency"] != "ROOT-RUB"
    assert target["currency_fingerprint"] != "root-fingerprint"
    assert target["category_id"] == ""
    assert target["description_category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}
    assert target["validation_errors"] == []
    assert target["category_precheck"] == {}
    assert target["publish_status"] == ""
    assert target["status"] == ""
    assert target["last_precheck"] == {}
    assert target["last_precheck_target"] == {}
    assert target["last_publish_task"] == {}
    assert target["sites_to_sell"] == []


def test_single_publish_target_missing_fields_stays_empty() -> None:
    draft = {
        "platform": "yandex",
        "site": "global",
        "language": "ru-RU",
        "category_id": "YANDEX-ROOT",
        "category_path": "Yandex 根类目",
        "attributes": {"ROOT_ATTRIBUTE": "旧值"},
        "target_sites": [
            {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
            }
        ],
    }

    target = draft_publish_targets(draft)[0]

    assert target["category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}


def test_other_market_publish_target_ignores_root_listing() -> None:
    [target] = draft_publish_targets(
        {
            "platform": "yandex",
            "site": "global",
            "language": "ru-RU",
            "category_id": "60996608",
            "description_category_id": "yandex-description-category",
            "category_path": "Yandex > Home > Storage",
            "attributes": {"YANDEX_BRAND": "Yandex brand"},
            "target_sites": [
                {
                    "platform": "ozon",
                    "site": "global",
                    "language": "ru-RU",
                }
            ],
        }
    )

    assert target["platform"] == "ozon"
    assert target["category_id"] == ""
    assert target["description_category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}


def test_invalid_nonempty_publish_targets_do_not_inherit_root_fields() -> None:
    draft = {
        "platform": "yandex",
        "site": "global",
        "language": "ru-RU",
        "category_id": "YANDEX-ROOT",
        "category_path": "Yandex 根类目",
        "attributes": {"ROOT_ATTRIBUTE": "旧值"},
        "target_sites": ["invalid-target"],
    }

    target = draft_publish_targets(draft)[0]

    assert target["category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}


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
    assert target["currency_fingerprint"] == compute_currency_fingerprint(
        "ozon", "", "", [], "unresolved", ""
    )
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


def test_precheck_does_not_inherit_root_publish_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_updates: dict[str, Any] = {}

    def fake_merge(draft, _target, updates, *, context=None):
        captured_updates.update(updates)
        return draft

    monkeypatch.setattr(
        draft_publish_context,
        "merge_target_listing_into_draft",
        fake_merge,
    )
    monkeypatch.setattr(
        draft_publish_context,
        "_save_updated_draft",
        lambda draft, _publish_context, *, context=None: draft,
    )

    draft_publish_context.save_draft_precheck_result(
        {
            "draft": {"publish_status": "published"},
            "target": {
                "platform": "ozon",
                "site": "global",
                "publish_status": "",
            },
        },
        {"ok": True, "errors": [], "warnings": []},
    )

    assert captured_updates["publish_status"] == "ready"
    assert captured_updates["status"] == "ready_to_publish"


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
