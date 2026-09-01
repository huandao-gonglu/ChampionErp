from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from erp_web.context import get_context
from erp_web.product_model import (
    mercadolibre_publication_from_response,
    normalize_mercadolibre_publication,
)
from erp_web.runtime_units.publish_bus import (
    append_publish_bus_terminal_log,
    apply_publish_bus_result_to_draft,
)
from tests.runtime_test_utils import temp_app_context


def _publication() -> dict:
    return {
        "model": "user_products",
        "parent_item_id": "CBT100",
        "parent_user_product_id": "CBTU100",
        "siteless_user_product_id": "U100",
        "siteless_family_id": "FAMILY100",
        "family_name": "Portable fan",
        "confirmed_payload": {},
        "markets": [
            {
                "site_id": "MLM",
                "seller_id": "991",
                "logistic_type": "remote",
                "item_id": "MLM100",
                "user_product_id": "MLMU100",
                "status": "active",
                "price": 18,
                "currency_id": "USD",
            }
        ],
        "updated_at": "2026-08-26T00:00:00Z",
    }


def test_user_products_response_maps_parent_and_market_operations() -> None:
    publication = mercadolibre_publication_from_response(
        {
            "item_id": "CBT100",
            "parent_user_product_id": "CBTU100",
            "siteless_user_product_id": "U100",
            "siteless_family_id": "FAMILY100",
            "site_items": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "seller_id": "991",
                    "status": "active",
                    "currency_id": "USD",
                }
            ],
        },
        family_name="Portable fan",
        requested_sites=[
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "price": 18,
                "listing_type_id": "gold_special",
            }
        ],
        updated_at="2026-08-26T00:00:00Z",
    )

    assert publication == {
        **_publication(),
        "status": "active",
        "markets": [
            {
                **_publication()["markets"][0],
                "listing_type_id": "gold_special",
                "updated_at": "2026-08-26T00:00:00Z",
                "last_operation": {
                    "status": "succeeded",
                    "updated_at": "2026-08-26T00:00:00Z",
                },
            }
        ],
    }


def test_traditional_publication_keeps_parent_identity_without_markets() -> None:
    assert normalize_mercadolibre_publication(
        {
            "model": "traditional_global_items",
            "account_user_id": "3344094721",
            "parent_item_id": "CBT4232215884",
            "markets": [],
        }
    ) == {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "parent_item_id": "CBT4232215884",
        "markets": [],
        "confirmed_payload": {},
    }


def test_publication_without_explicit_model_is_discarded() -> None:
    assert normalize_mercadolibre_publication(
        {
            "siteless_user_product_id": "U100",
            "parent_item_id": "CBT100",
            "markets": [{"site_id": "MLM", "item_id": "MLM100"}],
        }
    ) == {}


def test_traditional_response_root_id_becomes_parent_item_id() -> None:
    publication = mercadolibre_publication_from_response(
        {"id": "CBT4232215884"},
        listing_model="traditional_global_items",
    )

    assert publication["model"] == "traditional_global_items"
    assert publication["parent_item_id"] == "CBT4232215884"


def test_publish_bus_result_writes_publication_and_siteless_identity() -> None:
    publication = _publication()
    draft = {
        "platform": "mercadolibre",
        "site": "CBT",
        "target_sites": [
            {
                "platform": "mercadolibre",
                "site": "CBT",
                "sites_to_sell": [
                    {"site_id": "MLM", "logistic_type": "remote"}
                ],
            }
        ],
    }

    updated = apply_publish_bus_result_to_draft(
        draft,
        {"job_id": "job-1", "updated_at": "2026-08-26T00:00:00Z"},
        "mercadolibre",
        {
            "site": "CBT",
            "status": "success",
            "stage": "publish",
            "attempts": 1,
            "result": {
                "siteless_user_product_id": "U100",
                "siteless_family_id": "FAMILY100",
                "item_id": "CBT100",
                "operation": "created",
                "publication": publication,
                # 成功结果即使带有历史遗留 error_map，也不能污染成功任务 shape。
                "error_map": {
                    "summary": "stale error",
                    "error_code": "STALE_ERROR",
                    "next_action": "retry",
                },
            },
        },
    )

    assert updated["publication"] == publication
    assert updated["last_publish_task"] == {
        "job_id": "job-1",
        "status": "published",
        "platform_status": "success",
        "stage": "publish",
        "error": "",
        "attempts": 1,
        "item_id": "CBT100",
        "siteless_user_product_id": "U100",
        "siteless_family_id": "FAMILY100",
        "external_id": "U100",
        "operation": "created",
        "updated_at": "2026-08-26T00:00:00Z",
    }


def test_publish_bus_failure_persists_structured_error_map_to_draft_and_log(
    tmp_path,
) -> None:
    error_map = {
        "summary": "传统 Global Items 市场创建失败",
        "error_code": "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED",
        "retryable": False,
        "field_errors": {
            "sites_to_sell": [
                "MLM/remote：local_rate_limited",
                "MLU/remote：site.not_operable",
            ]
        },
        "next_action": "修复不可发布市场；限流目标稍后重试。",
    }
    result = {
        "ok": False,
        "error": "不应优先使用的顶层错误",
        "error_code": "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED",
        "error_map": error_map,
    }
    item = {
        "site": "CBT",
        "product_id": "product-1",
        "draft_id": "draft-1",
        "status": "failed",
        "stage": "failed",
        "error": "不应优先使用的任务错误",
        "attempts": 1,
        "created_at": "2026-08-26T00:00:00Z",
        "updated_at": "2026-08-26T00:01:00Z",
        "result": result,
    }
    draft = {
        "draft_id": "draft-1",
        "platform": "mercadolibre",
        "site": "CBT",
        "target_sites": [{"platform": "mercadolibre", "site": "CBT"}],
    }

    updated = apply_publish_bus_result_to_draft(
        draft,
        {"job_id": "job-failed", "updated_at": "2026-08-26T00:01:00Z"},
        "mercadolibre",
        item,
    )

    assert updated["validation_errors"] == [
        {
            "code": "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED",
            "field": "sites_to_sell",
            "message": "MLM/remote：local_rate_limited",
            "severity": "error",
            "next_action": "修复不可发布市场；限流目标稍后重试。",
        },
        {
            "code": "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED",
            "field": "sites_to_sell",
            "message": "MLU/remote：site.not_operable",
            "severity": "error",
            "next_action": "修复不可发布市场；限流目标稍后重试。",
        },
    ]
    assert updated["last_publish_task"]["error"] == error_map["summary"]
    assert updated["last_publish_task"]["error_code"] == error_map["error_code"]
    assert updated["last_publish_task"]["field_errors"] == error_map["field_errors"]
    assert updated["last_publish_task"]["next_action"] == error_map["next_action"]
    assert updated["last_publish_task"]["retryable"] is False

    database = Mock()
    database.publish_log_exists.return_value = False
    context = SimpleNamespace(
        db=database,
        paths=SimpleNamespace(output_dir=tmp_path),
    )
    with patch(
        "erp_web.runtime_units.publish_logs_runtime._write_publish_artifacts",
        return_value=("payload.json", "response.json"),
    ):
        append_publish_bus_terminal_log(
            {"product_id": "product-1"},
            draft,
            {"job_id": "job-failed"},
            "mercadolibre",
            item,
            context=context,
        )

    log_entry = database.insert_publish_log_once.call_args.args[0]
    assert log_entry["error_code"] == error_map["error_code"]
    assert log_entry["error_message"] == error_map["summary"]
    assert log_entry["field_errors"] == error_map["field_errors"]
    assert log_entry["next_action"] == error_map["next_action"]


def test_publish_bus_outcome_unknown_forces_reconciliation_before_retry(
    tmp_path,
) -> None:
    item = {
        "site": "CBT",
        "status": "outcome_unknown",
        "stage": "outcome_unknown",
        "error": "创建响应缺少父身份",
        "attempts": 1,
        "result": {
            "ok": False,
            "error_code": "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            "error_map": {
                "summary": "创建响应缺少父身份",
                "error_code": "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                "retryable": True,
                "field_errors": {},
                "next_action": "立即重新发布",
            },
        },
    }

    updated = apply_publish_bus_result_to_draft(
        {
            "platform": "mercadolibre",
            "site": "CBT",
            "target_sites": [{"platform": "mercadolibre", "site": "CBT"}],
        },
        {"job_id": "job-unknown", "updated_at": "2026-08-26T00:01:00Z"},
        "mercadolibre",
        item,
    )

    validation = updated["validation_errors"][0]
    assert validation["code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert "确认前不要重新发布" in validation["next_action"]
    task = updated["last_publish_task"]
    assert task["next_action"] == validation["next_action"]
    assert task["retryable"] is False

    database = Mock()
    database.publish_log_exists.return_value = False
    context = SimpleNamespace(
        db=database,
        paths=SimpleNamespace(output_dir=tmp_path),
    )
    with patch(
        "erp_web.runtime_units.publish_logs_runtime._write_publish_artifacts",
        return_value=("payload.json", "response.json"),
    ):
        append_publish_bus_terminal_log(
            {"product_id": "product-1"},
            {"draft_id": "draft-1"},
            {"job_id": "job-unknown"},
            "mercadolibre",
            {**item, "product_id": "product-1", "draft_id": "draft-1"},
            context=context,
        )
    log_entry = database.insert_publish_log_once.call_args.args[0]
    assert log_entry["next_action"] == validation["next_action"]
    assert "立即重新发布" not in log_entry["next_action"]


def test_publish_bus_malformed_error_map_falls_back_to_one_publish_error() -> None:
    updated = apply_publish_bus_result_to_draft(
        {
            "platform": "mercadolibre",
            "site": "CBT",
            "target_sites": [{"platform": "mercadolibre", "site": "CBT"}],
        },
        {"job_id": "job-fallback", "updated_at": "2026-08-26T00:01:00Z"},
        "mercadolibre",
        {
            "site": "CBT",
            "status": "failed",
            "stage": "failed",
            "error": "远端拒绝发布",
            "attempts": 1,
            "result": {
                "error": "远端拒绝发布",
                "error_code": "MERCADOLIBRE_REQUEST_INVALID",
                "error_map": {
                    "summary": {"unexpected": "container"},
                    "error_code": [],
                    "field_errors": {"publish": {"unexpected": "container"}},
                    "next_action": [],
                },
            },
        },
    )

    assert updated["validation_errors"] == [
        {
            "code": "MERCADOLIBRE_REQUEST_INVALID",
            "field": "publish",
            "message": "远端拒绝发布",
            "severity": "error",
            "next_action": "按字段提示修复后重试",
        }
    ]


def test_generic_draft_save_cannot_overwrite_persisted_publication(tmp_path) -> None:
    with temp_app_context(tmp_path):
        saved = get_context().products.save_product(
            {
                "name": "Portable fan",
                "drafts": {
                    "mercadolibre": {
                        "platform": "mercadolibre",
                        "site": "CBT",
                        "title": "Portable fan",
                        "publication": _publication(),
                        "target_sites": [
                            {
                                "platform": "mercadolibre",
                                "site": "CBT",
                                "sites_to_sell": [
                                    {
                                        "site_id": "MLM",
                                        "logistic_type": "remote",
                                    }
                                ],
                            }
                        ],
                    }
                },
            }
        )
        draft = saved["drafts"]["mercadolibre"]

        payload, error, status = get_context().products.save_draft_detail(
            {
                "draft_id": draft["draft_id"],
                "platform": "mercadolibre",
                "site": "CBT",
                "title": "Updated local title",
                "publication": {
                    "siteless_user_product_id": "FORGED",
                    "markets": [],
                },
                "target_sites": draft["target_sites"],
            }
        )

        assert error is None
        assert status == 200
        assert payload["draft"]["title"] == "Updated local title"
        assert payload["draft"]["publication"] == _publication()
