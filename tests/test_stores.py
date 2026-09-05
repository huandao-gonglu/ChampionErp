from __future__ import annotations

"""ProductStore / ConfigStore：经 AppContext 的基本 CRUD 与配置读写。"""

from copy import deepcopy
import json
import os
import threading

import pytest

import erp_web.stores.config_store as config_store_module
from erp_web.context import get_context
from erp_web.db import ErpDatabase
from erp_web.product_model import (
    apply_category_target_updates,
    normalize_draft_target_site,
    normalize_product_model,
)
from erp_web.runtime_units.store_credentials import (
    preview_mercadolibre_auth_link,
)
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.services import config_service
from erp_web.stores.config_store import ConfigStore


def _sample_product(title: str = "Store test product", url: str = "https://example.com/store-test") -> dict:
    return {
        "name": title,
        "source": {
            "title": title,
            "source_url": url,
            "source_platform": "1688",
            "images": ["https://img.example.com/1.jpg"],
        },
        "drafts": {
            "mercadolibre": {
                "enabled": True,
                "title": title,
                "description": "Store test description",
                "status": "claimed",
            }
        },
    }


@pytest.mark.parametrize("attributes", [{"电机类型": "无刷电机", "电池容量": "2000–4000mAh"}, {}])
def test_source_attribute_edits_survive_save_reload_and_remove_stale_copies(attributes: dict) -> None:
    from erp_web.runtime_units.category_attribute_ai_fill import _product_context
    from erp_web.runtime_units.product_capabilities import read_product
    from erp_web.schemas.product_capabilities import ProductReadRequest

    products = get_context().products
    product = {
        "name": "便携风扇",
        "source": {"title": "便携风扇", "attributes": {"电机类型": "旧电机", "尺寸": "0", "材质": "ABS"}},
        "attributes": {"电机类型": "旧电机", "尺寸": "0", "材质": "独立核实的材质", "内部备注": "保留"},
    }
    saved = products.save_product(product)
    product_id = saved["product_id"]
    products.save_product_profile({"product_id": product_id, "source": {"attributes": attributes}})
    reloaded = products.load_product_from_index(product_id, "")

    assert reloaded["source"]["attributes"] == attributes
    assert reloaded["attributes"] == {"材质": "独立核实的材质", "内部备注": "保留"}
    facts = read_product(ProductReadRequest(product_id=product_id), product_store=products)
    assert facts.product.source_attributes == attributes
    fill_context = _product_context(reloaded, "mercadolibre")
    assert fill_context["source"]["attributes"] == attributes
    assert "旧电机" not in json.dumps(fill_context, ensure_ascii=False)

    # 再次保存也不能从采集副本恢复已删除的属性。
    products.save_product_profile(reloaded)
    assert products.load_product_from_index(product_id, "")["source"]["attributes"] == attributes


def _save_ready_draft_with_server_state() -> tuple[dict, list[dict]]:
    reviews = [
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes.VOLTAGE",
            "message": "请人工确认电压",
        },
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes.MODEL",
            "message": "请人工确认型号",
        },
        {
            "code": "OLD_PRECHECK",
            "field": "title",
            "message": "旧预检结果",
        },
        "AI 暂无法从商品信息判断，请人工确认。",
    ]
    precheck = {"ok": True, "checked_at": "2026-08-29T00:00:00Z"}
    publish_task = {"job_id": "job-server", "item_id": "CBT123456"}
    target = {
        "platform": "mercadolibre",
        "site": "CBT",
        "language": "en-US",
        "listing_currency": "USD",
        "category_id": "CBT455865",
        "attributes": {"VOLTAGE": "110V", "MODEL": "X1"},
        "sites_to_sell": [
            {"site_id": "MLM", "logistic_type": "remote"},
        ],
        "validation_errors": deepcopy(reviews),
        "category_precheck": deepcopy(precheck),
        "last_precheck": deepcopy(precheck),
        "last_precheck_target": {"platform": "mercadolibre", "site": "CBT"},
        "status": "ready_to_publish",
        "publish_status": "ready",
        "last_publish_task": deepcopy(publish_task),
    }
    product = _sample_product(
        "Protected draft state",
        "https://example.com/protected-draft-state",
    )
    product["drafts"]["mercadolibre"].update(
        {
            "site": "CBT",
            "language": "en-US",
            "title": "Protected draft state",
            "category_id": "CBT455865",
            "attributes": {"VOLTAGE": "110V", "MODEL": "X1"},
            "target_sites": [target],
            "validation_errors": deepcopy(reviews),
            "category_precheck": deepcopy(precheck),
            "last_precheck": deepcopy(precheck),
            "last_precheck_target": {
                "platform": "mercadolibre",
                "site": "CBT",
            },
            "status": "ready_to_publish",
            "publish_status": "ready",
            "last_publish_task": deepcopy(publish_task),
            "publication": {
                "model": "traditional_global_items",
                "parent_item_id": "CBT123456",
                "status": "active",
            },
        }
    )
    product["publish_preview"] = {"mercadolibre": deepcopy(precheck)}
    return get_context().products.save_product(product), reviews


def _tampered_publish_state_payload(draft: dict) -> dict:
    target = deepcopy(draft["target_sites"][0])
    forged_reviews = [
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes.MODEL",
            "message": "客户端伪造的型号消息",
        },
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes.BRAND",
            "message": "客户端新增的伪造 review",
        },
    ]
    forged_state = {
        "status": "published",
        "publish_status": "real_publish_success",
        "validation_errors": deepcopy(forged_reviews),
        "category_precheck": {"ok": False, "forged": True},
        "last_precheck": {"ok": False, "forged": True},
        "last_precheck_target": {"platform": "ozon", "site": "global"},
        "last_publish_task": {"job_id": "job-client-forged"},
    }
    target.update(deepcopy(forged_state))
    return {
        "draft_id": draft["draft_id"],
        **forged_state,
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP-CLIENT-FORGED",
        },
        "target_sites": [target],
    }


def test_product_store_crud_via_context() -> None:
    products = get_context().products

    saved = products.save_product(_sample_product())
    assert saved["product_id"]

    index = products.load_products_index()
    assert [item["product_id"] for item in index] == [saved["product_id"]]

    loaded = products.load_product_from_index(saved["product_id"], "")
    assert loaded["name"] == "Store test product"
    assert loaded["schema_version"] == PRODUCT_SCHEMA_VERSION
    assert loaded["drafts"]["mercadolibre"]["title"] == "Store test product"

    drafts = products.load_drafts_index()
    assert len(drafts) == 1
    draft_id = drafts[0]["draft_id"]
    detail, error, status = products.load_draft_detail_from_index(draft_id)
    assert error is None and status == 200
    assert detail["draft"]["draft_id"] == draft_id

    deleted = products.delete_products_from_index([saved["product_id"]])
    assert deleted["ok"] is True
    assert deleted["deleted"] == 1
    assert get_context().db.list_product_records() == []


def test_save_draft_detail_ignores_client_publish_state_when_content_is_unchanged() -> None:
    saved, reviews = _save_ready_draft_with_server_state()
    existing = saved["drafts"]["mercadolibre"]
    payload = _tampered_publish_state_payload(existing)

    result, error, status = get_context().products.save_draft_detail(payload)

    assert error is None and status == 200
    draft = result["draft"]
    target = draft["target_sites"][0]
    for item, previous in (
        (draft, existing),
        (target, existing["target_sites"][0]),
    ):
        assert item["status"] == previous["status"]
        assert item["publish_status"] == previous["publish_status"]
        assert item["validation_errors"] == reviews
        assert item["category_precheck"] == previous["category_precheck"]
        assert item["last_precheck"] == previous["last_precheck"]
        assert item["last_precheck_target"] == previous["last_precheck_target"]
        assert item["last_publish_task"] == previous["last_publish_task"]
    assert draft["publication"] == existing["publication"]
    assert "mercadolibre" in result["productContext"]["raw"]["publish_preview"]


def test_save_draft_detail_derives_invalidation_and_rejects_forged_state_when_content_changes() -> None:
    saved, reviews = _save_ready_draft_with_server_state()
    existing = saved["drafts"]["mercadolibre"]
    payload = _tampered_publish_state_payload(existing)
    payload["title"] = "Protected draft state edited"
    payload["attributes"] = {"VOLTAGE": "110/220V", "MODEL": "X1"}
    payload["target_sites"][0]["attributes"] = {
        "VOLTAGE": "110/220V",
        "MODEL": "X1",
    }

    result, error, status = get_context().products.save_draft_detail(payload)

    assert error is None and status == 200
    draft = result["draft"]
    target = draft["target_sites"][0]
    expected_reviews = [reviews[1]]
    for item, previous in (
        (draft, existing),
        (target, existing["target_sites"][0]),
    ):
        assert item["status"] == "category_ready"
        assert item["publish_status"] == ""
        assert item["validation_errors"] == expected_reviews
        assert item["category_precheck"] == {}
        assert item["last_precheck"] == {}
        assert item["last_precheck_target"] == {}
        assert item["last_publish_task"] == previous["last_publish_task"]
    assert draft["publication"] == existing["publication"]
    assert "mercadolibre" not in result["productContext"]["raw"]["publish_preview"]


def test_save_draft_detail_does_not_treat_omitted_validation_errors_as_review_confirmation() -> None:
    saved, reviews = _save_ready_draft_with_server_state()
    existing = saved["drafts"]["mercadolibre"]
    target = deepcopy(existing["target_sites"][0])
    target["attributes"] = {"VOLTAGE": "110/220V", "MODEL": "X1"}
    target.pop("validation_errors", None)

    result, error, status = get_context().products.save_draft_detail(
        {
            "draft_id": existing["draft_id"],
            "attributes": {"VOLTAGE": "110/220V", "MODEL": "X1"},
            "target_sites": [target],
        }
    )

    assert error is None and status == 200
    expected_reviews = reviews[:2]
    assert result["draft"]["validation_errors"] == expected_reviews
    assert (
        result["draft"]["target_sites"][0]["validation_errors"]
        == expected_reviews
    )


def test_save_draft_target_missing_language_uses_site_default() -> None:
    saved, _reviews = _save_ready_draft_with_server_state()
    payload = deepcopy(saved["drafts"]["mercadolibre"])
    payload["language"] = "pt-BR"
    payload["target_sites"][0].pop("language", None)

    result, error, status = get_context().products.save_draft_detail(payload)

    assert error is None and status == 200
    assert result["draft"]["target_sites"][0]["language"] == "es"


def test_trusted_publish_state_writer_persists_state_only_updates() -> None:
    saved, _reviews = _save_ready_draft_with_server_state()
    existing = saved["drafts"]["mercadolibre"]
    updates = {
        "validation_errors": [],
        "category_precheck": {},
        "last_precheck": {},
        "last_precheck_target": {},
        "last_publish_task": {},
        "publish_status": "",
        "status": "images_ready",
    }

    result, error, status = get_context().products.save_draft_publish_state(
        existing["draft_id"],
        "mercadolibre",
        "CBT",
        updates,
    )

    assert error is None and status == 200
    for item in (result["draft"], result["draft"]["target_sites"][0]):
        for field, value in updates.items():
            assert item[field] == value
    assert result["draft"]["title"] == existing["title"]
    assert result["draft"]["attributes"] == existing["attributes"]
    assert result["draft"]["publication"] == existing["publication"]


def test_runtime_product_store_functions_delegate_to_context_store() -> None:
    products = get_context().products
    saved = products.save_product(
        _sample_product("Delegate check", "https://example.com/delegate")
    )

    assert get_context().db.load_product_model(saved["product_id"])["name"] == "Delegate check"
    assert [item["product_id"] for item in products.load_products_index()] == [
        saved["product_id"]
    ]

    result = products.delete_products_from_index([saved["product_id"]])
    assert result["deleted"] == 1
    assert products.load_products_index() == []


def test_config_store_app_config_roundtrip_and_whitelist_merge() -> None:
    config_store = get_context().config

    config = config_store.load_app_config()
    assert get_context().paths.app_config_path.exists()

    merged = config_store.merge_app_config_fields(config, {"unknown_top_level_key": {"x": 1}})
    assert "unknown_top_level_key" not in merged

    config["alibaba_cookie"] = "cookie-abc"
    config_store.save_app_config(config)
    assert config_store.load_app_config()["alibaba_cookie"] == "cookie-abc"


def test_load_app_config_rejects_plaintext_secrets_without_mutation() -> None:
    context = get_context()
    store = context.config
    config = store.default_app_config()
    config["alibaba_cookie"] = "legacy-plaintext-cookie"
    original = json.dumps(config, ensure_ascii=False, indent=2)
    context.paths.app_config_path.write_text(
        original,
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="非空明文秘密字段"):
        store.load_app_config()

    assert (
        context.paths.app_config_path.read_text(encoding="utf-8")
        == original
    )
    assert context.db.load_runtime_secrets("app_config") == {}


def test_load_store_config_rejects_file_credentials_without_mutation() -> None:
    context = get_context()
    store = context.config
    original = json.dumps(
        {
            "mercadolibre": {
                "site_id": "MLM",
                "access_token": "legacy-plaintext-token",
            }
        },
        ensure_ascii=False,
        indent=2,
    )
    context.paths.store_config_path.write_text(
        original,
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="已退役的文件凭据"):
        store.load_store_config()

    assert (
        context.paths.store_config_path.read_text(encoding="utf-8")
        == original
    )
    assert context.db.list_store_auth() == {}


def test_load_store_config_allows_empty_secret_placeholders() -> None:
    context = get_context()
    store = context.config
    original = json.dumps(
        {
            "mercadolibre": {
                "site_id": "MLM",
                "access_token": "",
                "app_secret": "",
            }
        },
        ensure_ascii=False,
        indent=2,
    )
    context.paths.store_config_path.write_text(
        original,
        encoding="utf-8",
    )

    loaded = store.load_store_config()

    assert loaded["mercadolibre"]["site_id"] == "MLM"
    assert loaded["mercadolibre"]["access_token"] == ""
    assert (
        context.paths.store_config_path.read_text(encoding="utf-8")
        == original
    )
    assert context.db.list_store_auth() == {}


def test_app_runtime_secret_paths_survive_ai_model_reordering() -> None:
    context = get_context()
    config_store = context.config
    config = config_store.default_app_config()
    config["ai_models"][0]["api_key"] = "text-model-secret"
    config["ai_models"][1]["api_key"] = "image-model-secret"
    expected = {
        str(model["id"]): str(model["api_key"])
        for model in config["ai_models"]
    }
    config_store.save_app_config(config)

    stored_paths = [
        json.loads(path)
        for path in context.db.load_runtime_secrets("app_config")
        if json.loads(path)[0] == "ai_models"
    ]
    assert stored_paths
    assert all(
        not any(isinstance(part, int) for part in path)
        for path in stored_paths
    )
    assert {
        part["$value"]
        for path in stored_paths
        for part in path
        if isinstance(part, dict) and part.get("$field") == "id"
    } == set(expected)

    static_config = json.loads(
        context.paths.app_config_path.read_text(encoding="utf-8")
    )
    static_config["ai_models"] = list(
        reversed(static_config["ai_models"])
    )
    context.paths.app_config_path.write_text(
        json.dumps(static_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    reopened = ConfigStore(
        context.paths,
        ErpDatabase(context.paths.db_path),
    )
    restored = reopened.load_app_config()
    restored_by_id = {
        str(model["id"]): str(model["api_key"])
        for model in restored["ai_models"]
    }
    assert restored_by_id == expected


def test_legacy_index_runtime_secret_paths_are_rejected() -> None:
    context = get_context()
    config_store = context.config
    config = config_store.default_app_config()
    config_store.save_app_config(config)
    legacy_path = json.dumps(
        ["ai_models", 0, "api_key"],
        separators=(",", ":"),
    )
    context.db.replace_runtime_secrets(
        "app_config",
        {legacy_path: "legacy-index-secret"},
    )

    reopened = ConfigStore(
        context.paths,
        ErpDatabase(context.paths.db_path),
    )
    with pytest.raises(RuntimeError, match="列表 index 路径"):
        reopened.load_app_config()

    assert context.db.load_runtime_secrets("app_config") == {
        legacy_path: "legacy-index-secret"
    }


def test_config_store_routes_secrets_to_store_auth_table() -> None:
    config_store = get_context().config
    paths = get_context().paths

    config_store.save_store_config({"mercadolibre": {"access_token": "tok-secret-123", "site_id": "MLM"}})

    file_config = json.loads(paths.store_config_path.read_text(encoding="utf-8"))
    assert "access_token" not in file_config.get("mercadolibre", {})
    assert file_config["mercadolibre"]["site_id"] == "MLM"

    auth = get_context().db.get_store_auth("mercadolibre")
    assert auth["credentials"]["access_token"] == "tok-secret-123"

    loaded = config_store.load_store_config()
    assert loaded["mercadolibre"]["access_token"] == "tok-secret-123"
    assert loaded["mercadolibre"]["site_id"] == "MLM"


def test_clear_store_auth_deletes_secrets_but_preserves_static_settings() -> None:
    config_store = get_context().config
    config_store.save_store_config(
        {
            "mercadolibre": {
                "access_token": "token-to-delete",
                "app_secret": "secret-to-delete",
                "site_id": "MLM",
                "notification_url": "https://notify.example.test/ml",
            }
        }
    )

    cleared = config_store.clear_store_auth("mercadolibre")

    assert get_context().db.get_store_auth("mercadolibre")["credentials"] == {}
    assert cleared["mercadolibre"]["access_token"] == ""
    assert cleared["mercadolibre"]["app_secret"] == ""
    assert cleared["mercadolibre"]["site_id"] == "MLM"
    assert (
        cleared["mercadolibre"]["notification_url"]
        == "https://notify.example.test/ml"
    )


def test_public_config_views_redact_nested_secrets_and_preserve_masked_updates() -> None:
    app_config = {
        "alibaba_cookie": "cookie-private-value",
        "1688_api": {
            "app_key": "1688-app-key",
            "app_secret": "1688-app-secret",
            "access_token": "1688-access-token",
        },
        "yunexpress": {
            "app_id": "yun-app-id",
            "app_secret": "yun-app-secret",
            "source_key": "yun-source-key",
        },
        "ai_models": [
            {
                "id": "private-model",
                "api_key": "sk-private-model-key",
            }
        ],
    }
    store_config = {
        "mercadolibre": {
            "site_id": "MLM",
            "app_id": "ml-public-app-id",
            "client_id": "ml-public-client-id",
            "app_secret": "ml-private-app-secret",
            "access_token": "ml-private-token",
            "refresh_token": "ml-private-refresh",
            "code_verifier": "pkce-private-verifier",
        },
        "ozon": {
            "client_id": "ozon-client",
            "api_key": "ozon-private-key",
        },
    }

    public_app = config_service.public_app_config(
        get_context().paths.app_dir,
        app_config,
    )
    public_store = config_service.public_store_config(store_config)
    serialized = json.dumps(
        {"app": public_app, "store": public_store},
        ensure_ascii=False,
    )
    for secret in (
        "cookie-private-value",
        "1688-app-secret",
        "1688-access-token",
        "yun-app-secret",
        "yun-source-key",
        "sk-private-model-key",
        "ml-private-app-secret",
        "ml-private-token",
        "ml-private-refresh",
        "pkce-private-verifier",
        "ozon-private-key",
    ):
        assert secret not in serialized
    assert public_app["1688_api"]["app_secret"]
    assert public_store["mercadolibre"]["app_id"] == "ml-public-app-id"
    assert (
        public_store["mercadolibre"]["client_id"]
        == "ml-public-client-id"
    )
    assert public_store["ozon"]["client_id"] == "ozon-client"
    auth_url = preview_mercadolibre_auth_link(
        str(public_store["mercadolibre"]["app_id"]),
        "https://example.test/oauth/callback",
    )
    assert "client_id=ml-public-app-id" in auth_url
    assert "ml-private-app-secret" not in auth_url
    assert "ml-private-token" not in auth_url

    config_store = get_context().config
    current = config_store.load_app_config()
    current["1688_api"]["app_secret"] = "keep-this-secret"
    masked = config_service.public_app_config(
        get_context().paths.app_dir,
        current,
    )
    merged = config_store.merge_app_config_fields(
        current,
        {"1688_api": {"app_secret": masked["1688_api"]["app_secret"]}},
    )
    assert merged["1688_api"]["app_secret"] == "keep-this-secret"


def test_config_snapshot_recursively_masks_every_secret_key() -> None:
    secrets = {
        "alibaba_cookie": "cookie-private-value",
        "nested": {
            "vendor_api_key": "vendor-private-key",
            "private_key": "private-key-value",
            "source_key": "source-key-value",
        },
    }

    path = config_service.save_config_snapshot(
        get_context().paths.app_dir,
        secrets,
    )
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(snapshot, ensure_ascii=False)

    for secret in (
        "cookie-private-value",
        "vendor-private-key",
        "private-key-value",
        "source-key-value",
    ):
        assert secret not in serialized
    assert snapshot["alibaba_cookie"]
    assert snapshot["nested"]["vendor_api_key"]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "key",
    [
        "vendorApiKey",
        "clientSecret",
        "access-token",
        "privateKey",
        "sourceKey",
        "sessionCookie",
    ],
)
def test_sensitive_config_key_normalizes_common_naming_styles(
    key: str,
) -> None:
    assert config_service.is_sensitive_config_key(key) is True


def test_app_runtime_secrets_route_nested_naming_styles_to_sqlite() -> None:
    context = get_context()
    store = context.config
    config = store.default_app_config()
    source = config["product_research"]["source_registry"][0]
    source["config_json"].update(
        {
            "vendorApiKey": "vendor-key-private",
            "clientSecret": "client-secret-private",
            "access-token": "access-token-private",
            "privateKey": "private-key-private",
        }
    )

    store.save_app_config(config)

    static_text = context.paths.app_config_path.read_text(encoding="utf-8")
    persisted_secrets = context.db.load_runtime_secrets("app_config")
    for secret in (
        "vendor-key-private",
        "client-secret-private",
        "access-token-private",
        "private-key-private",
    ):
        assert secret not in static_text
        assert secret in persisted_secrets.values()

    restored = store.load_app_config()
    restored_source = next(
        item
        for item in restored["product_research"]["source_registry"]
        if item["id"] == source["id"]
    )
    assert restored_source["config_json"]["vendorApiKey"] == "vendor-key-private"
    assert restored_source["config_json"]["clientSecret"] == "client-secret-private"
    assert restored_source["config_json"]["access-token"] == "access-token-private"
    assert restored_source["config_json"]["privateKey"] == "private-key-private"


def test_product_schema_rejects_future_and_filters_unknown_write_fields() -> None:
    assert PRODUCT_SCHEMA_VERSION == 4
    with pytest.raises(ValueError, match="拒绝降级写入"):
        normalize_product_model(
            {
                "schema_version": PRODUCT_SCHEMA_VERSION + 1,
                "future_only": "must-not-be-downgraded",
            }
        )

    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Canonical",
            "dimensions": "10 x 8 x 3 cm",
            "future_only": "drop-me",
            "wb_subject_id": "legacy-platform-field",
            "source": {
                "image_pool": [
                    {
                        "asset_id": "legacy-image-id",
                        "local_path": "data/images/legacy.jpg",
                        "width_px": 640,
                        "height_px": 480,
                        "platform_uploads": {
                            "mercadolibre": {
                                "picture_id": "123-CBT456",
                                "uploaded_at": "2026-08-26T00:00:00Z",
                            }
                        },
                        "future_image_field": "drop-me",
                    }
                ]
            },
            "drafts": {
                "mercadolibre": {
                    "sale_price": "44.90",
                    "searchTerms": ["legacy keyword"],
                    "packageDimensions": {
                        "lengthCm": "10",
                        "widthCm": "8",
                        "heightCm": "3",
                        "weightKg": "0.5",
                    },
                    "categoryPrecheck": {"ok": True},
                    "future_draft_field": "drop-me",
                    "publication": {
                        "model": "user_products",
                        "siteless_user_product_id": "UP100",
                        "siteless_family_id": "FAMILY100",
                        "parent_item_id": "CBT100",
                        "family_name": "Canonical family",
                        "markets": [
                            {
                                "site_id": "mlm",
                                "seller_id": "991",
                                "logistic_type": "REMOTE",
                                "item_id": "MLM100",
                                "user_product_id": "UP-MLM100",
                                "status": "active",
                                "price": "21.50",
                                "currency_id": "usd",
                            }
                        ],
                    },
                    "targetSites": [
                        {
                            "platform": "mercadolibre",
                            "site": "CBT",
                            "categoryId": "CBT-CANONICAL",
                            "categoryPath": "Home / Test",
                            "sitesToSell": [
                                {
                                    "site_id": "MLM",
                                    "logistic_type": "remote",
                                }
                            ],
                            "publishStatus": "ready",
                            "futureTargetField": "drop-me",
                        }
                    ],
                    "pricing": {
                        "suggested_price": 19.9,
                        "suggestedPrice": 99.9,
                        "exchangeRates": {"mode": "legacy"},
                        "targets": {
                            "mercadolibre:CBT": {
                                "applied_price": 21.5,
                                "appliedPrice": 88.8,
                            }
                        },
                    }
                }
            },
        }
    )

    assert normalized["dimensions"] == "10 x 8 x 3 cm"
    assert "future_only" not in normalized
    assert "wb_subject_id" not in normalized
    image = normalized["source"]["image_pool"][0]
    assert image["id"] == "legacy-image-id"
    assert image["path"] == "data/images/legacy.jpg"
    assert image["width"] == 640
    assert image["height"] == 480
    assert image["platform_uploads"] == {
        "mercadolibre": {
            "picture_id": "123-CBT456",
            "uploaded_at": "2026-08-26T00:00:00Z",
        }
    }
    assert {
        "asset_id",
        "local_path",
        "width_px",
        "height_px",
        "future_image_field",
    }.isdisjoint(image)
    draft = normalized["drafts"]["mercadolibre"]
    assert "price" not in draft
    assert draft["search_terms"] == ["legacy keyword"]
    assert draft["package_dimensions"] == {
        "length_cm": "10",
        "width_cm": "8",
        "height_cm": "3",
        "weight_kg": "0.5",
    }
    assert draft["category_precheck"] == {"ok": True}
    assert "future_draft_field" not in draft
    assert draft["publication"] == {
        "model": "user_products",
        "parent_item_id": "CBT100",
        "siteless_user_product_id": "UP100",
        "siteless_family_id": "FAMILY100",
        "family_name": "Canonical family",
        "confirmed_payload": {},
        "markets": [
            {
                "site_id": "MLM",
                "seller_id": "991",
                "logistic_type": "remote",
                "item_id": "MLM100",
                "user_product_id": "UP-MLM100",
                "status": "active",
                "currency_id": "USD",
                "price": "21.50",
            }
        ],
    }
    target = draft["target_sites"][0]
    assert target["site"] == "CBT"
    assert target["category_id"] == "CBT-CANONICAL"
    assert target["category_path"] == "Home / Test"
    assert target["sites_to_sell"] == [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    assert target["publish_status"] == "ready"
    assert {
        "categoryId",
        "categoryPath",
        "publishStatus",
        "futureTargetField",
    }.isdisjoint(target)
    pricing = draft["pricing"]
    assert "suggested_price" not in pricing
    assert "suggestedPrice" not in pricing
    assert "exchangeRates" not in pricing
    pricing_target = pricing["targets"]["mercadolibre:cbt"]
    assert "applied_price" not in pricing_target
    assert pricing_target["stale_reason"] == "legacy_pricing_contract"
    assert "appliedPrice" not in pricing_target


def test_product_normalization_preserves_explicit_empty_platform_upc() -> None:
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "GTIN exempt product",
            "upc": "725272000243",
            "drafts": {
                "mercadolibre": {
                    "upc": "",
                    "allow_gtin_exemption": True,
                },
                "ozon": {
                    "upc": "4601234567890",
                },
            },
        }
    )

    assert normalized["upc"] == "725272000243"
    assert normalized["drafts"]["mercadolibre"]["upc"] == ""
    assert normalized["drafts"]["mercadolibre"]["allow_gtin_exemption"] is True
    assert normalized["drafts"]["ozon"]["upc"] == "4601234567890"


@pytest.mark.parametrize(
    "ozon_category",
    [
        {},
        {
            "category_id": "",
            "description_category_id": "",
            "category_path": "",
        },
    ],
    ids=("missing", "explicit-empty"),
)
def test_multi_market_categories_do_not_fallback_from_root(
    ozon_category: dict[str, str],
) -> None:
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Yandex and Ozon category isolation",
            "drafts": {
                "yandex": {
                    # target_sites 非空后，根字段不再拥有任何目标的类目身份。
                    "category_id": "60996608",
                    "description_category_id": "yandex-description-category",
                    "category_path": "Yandex > Home > Storage",
                    "target_sites": [
                        {
                            "platform": "yandex",
                            "site": "global",
                            "category_id": "yandex-target-category",
                            "description_category_id": (
                                "yandex-target-description-category"
                            ),
                            "category_path": "Yandex target > Storage",
                        },
                        {
                            "platform": "ozon",
                            "site": "global",
                            **ozon_category,
                        },
                    ],
                }
            },
        }
    )

    primary, sibling = normalized["drafts"]["yandex"]["target_sites"]
    assert primary["category_id"] == "yandex-target-category"
    assert (
        primary["description_category_id"]
        == "yandex-target-description-category"
    )
    assert primary["category_path"] == "Yandex target > Storage"
    assert sibling["platform"] == "ozon"
    assert sibling["category_id"] == ""
    assert sibling["description_category_id"] == ""
    assert sibling["category_path"] == ""


@pytest.mark.parametrize(
    "category_fields",
    [
        {
            "category_id": "",
            "description_category_id": "",
            "category_path": "",
        },
        {
            "categoryId": "",
            "descriptionCategoryId": "",
            "categoryPath": "",
        },
    ],
    ids=("canonical", "aliases"),
)
def test_explicit_empty_category_ignores_identity_defaults_content(
    category_fields: dict[str, str],
) -> None:
    target = normalize_draft_target_site(
        {
            "platform": "yandex",
            "site": "global",
            **category_fields,
        },
        "yandex",
        {
            "category_id": "60996608",
            "description_category_id": "root-description-category",
            "category_path": "Root > Yandex > Category",
        },
    )

    assert target["category_id"] == ""
    assert target["description_category_id"] == ""
    assert target["category_path"] == ""


def test_target_defaults_supply_only_identity_and_language() -> None:
    target = normalize_draft_target_site(
        {},
        "",
        {
            "platform": "yandex",
            "site": "global",
            "language": "ru-RU",
            "listing_currency": "RUB",
            "currency_fingerprint": "root-fingerprint",
            "category_id": "60996608",
            "description_category_id": "root-description-category",
            "category_path": "Root > Yandex > Category",
            "attributes": {"ROOT_ATTRIBUTE": "旧值"},
            "validation_errors": ["root-error"],
            "category_precheck": {"ok": True},
            "publish_status": "ready",
            "status": "ready_to_publish",
            "last_precheck": {"ok": True},
            "last_precheck_target": {"platform": "yandex"},
            "last_publish_task": {"job_id": "root-job"},
        },
    )

    assert target["platform"] == "yandex"
    assert target["site"] == "global"
    assert target["language"] == "ru-RU"
    assert target["listing_currency"] == ""
    assert target["currency_fingerprint"] == ""
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


def test_draft_root_listing_fields_project_primary_target() -> None:
    yandex_precheck = {"ok": True, "platform": "yandex"}
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Primary target projection",
            "drafts": {
                "yandex": {
                    "platform": "yandex",
                    "site": "global",
                    # 模拟编辑器当前选中 Ozon 时写入的根字段。
                    "category_id": "95196",
                    "description_category_id": "17028674",
                    "category_path": "Ozon > Dog houses",
                    "attributes": {"9048": "011"},
                    "validation_errors": ["85"],
                    "target_sites": [
                        {
                            "platform": "yandex",
                            "site": "global",
                            "category_id": "60996608",
                            "description_category_id": "",
                            "category_path": "Yandex > Pet houses",
                            "attributes": {"700001": "Alpha"},
                            "validation_errors": [],
                            "category_precheck": yandex_precheck,
                            "publish_status": "ready",
                            "status": "ready_to_publish",
                            "last_precheck": yandex_precheck,
                            "last_precheck_target": {
                                "platform": "yandex",
                                "site": "global",
                            },
                        },
                        {
                            "platform": "ozon",
                            "site": "global",
                            "category_id": "95196",
                            "description_category_id": "17028674",
                            "category_path": "Ozon > Dog houses",
                            "attributes": {"9048": "011"},
                            "validation_errors": ["85"],
                        },
                    ],
                }
            },
        }
    )

    draft = normalized["drafts"]["yandex"]
    primary = draft["target_sites"][0]
    for field in (
        "category_id",
        "description_category_id",
        "category_path",
        "attributes",
    ):
        assert draft[field] == primary[field]
    assert draft["category_id"] == "60996608"
    assert draft["attributes"] == {"700001": "Alpha"}


def test_root_listing_fields_do_not_seed_synthesized_target() -> None:
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Root fields are not target facts",
            "drafts": {
                "yandex": {
                    "platform": "yandex",
                    "site": "global",
                    # 故意使用错误根语言，合成目标必须改用 Yandex 站点默认。
                    "language": "es-MX",
                    "listing_currency": "ROOT-RUB",
                    "currency_fingerprint": "root-fingerprint",
                    "category_id": "60996608",
                    "description_category_id": "root-description-category",
                    "category_path": "Root > Yandex > Category",
                    "attributes": {"ROOT_ATTRIBUTE": "旧值"},
                    "validation_errors": ["root-error"],
                    "category_precheck": {"ok": True},
                    "publish_status": "ready",
                    "status": "ready_to_publish",
                    "last_precheck": {"ok": True},
                    "last_precheck_target": {"platform": "yandex"},
                    "last_publish_task": {"job_id": "root-job"},
                    "sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                }
            },
        }
    )

    target = normalized["drafts"]["yandex"]["target_sites"][0]
    assert target["platform"] == "yandex"
    assert target["site"] == "global"
    assert target["language"] == "ru-RU"
    assert target["listing_currency"] == ""
    assert target["currency_fingerprint"] == ""
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


def test_single_target_missing_listing_fields_stays_empty() -> None:
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Single target missing fields",
            "drafts": {
                "yandex": {
                    "category_id": "60996608",
                    "category_path": "Legacy > Yandex > Category",
                    "attributes": {"ROOT_ATTRIBUTE": "旧值"},
                    "target_sites": [
                        {
                            "platform": "yandex",
                            "site": "global",
                        }
                    ],
                }
            },
        }
    )

    target = normalized["drafts"]["yandex"]["target_sites"][0]
    assert target["category_id"] == ""
    assert target["category_path"] == ""
    assert target["attributes"] == {}


def test_draft_rejects_primary_target_from_another_platform() -> None:
    with pytest.raises(ValueError, match="primary target 与草稿平台不一致"):
        normalize_product_model(
            {
                "schema_version": PRODUCT_SCHEMA_VERSION,
                "name": "Invalid primary target identity",
                "drafts": {
                    "yandex": {
                        "platform": "yandex",
                        "site": "global",
                        "target_sites": [
                            {
                                "platform": "ozon",
                                "site": "global",
                            }
                        ],
                    }
                },
            }
        )


def test_category_target_update_rejects_nonexistent_explicit_site() -> None:
    draft = {
        "target_sites": [
            {
                "platform": "yandex",
                "site": "global",
                "attributes": {"700001": "Alpha"},
            }
        ]
    }

    with pytest.raises(ValueError, match="类目写入目标不唯一"):
        apply_category_target_updates(
            draft,
            "yandex",
            {"attributes": {"700001": "Beta"}},
            site="ru",
        )

    assert draft["target_sites"][0]["attributes"] == {"700001": "Alpha"}


def test_invalid_nonempty_target_sites_do_not_inherit_root_category() -> None:
    normalized = normalize_product_model(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "name": "Invalid target list",
            "drafts": {
                "yandex": {
                    "category_id": "60996608",
                    "category_path": "Root > Yandex > Category",
                    "target_sites": ["invalid-target"],
                }
            },
        }
    )

    target = normalized["drafts"]["yandex"]["target_sites"][0]
    assert target["category_id"] == ""
    assert target["category_path"] == ""


def test_product_schema_rejects_local_mercadolibre_draft_target() -> None:
    with pytest.raises(ValueError, match="只允许 CBT/Siteless 一级草稿"):
        normalize_product_model(
            {
                "schema_version": PRODUCT_SCHEMA_VERSION,
                "name": "Local target is retired",
                "drafts": {
                    "mercadolibre": {
                        "target_sites": [
                            {
                                "platform": "mercadolibre",
                                "site": "MLM",
                                "category_id": "MLM123",
                            }
                        ]
                    }
                },
            }
        )


def test_app_config_secret_update_rolls_back_when_file_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = get_context().config
    initial = store.default_app_config()
    initial["alibaba_cookie"] = "old-cookie-secret"
    store.save_app_config(initial)
    previous_secrets = get_context().db.load_runtime_secrets(
        "app_config"
    )
    previous_file = (
        get_context().paths.app_config_path.read_bytes()
    )
    updated = store.load_app_config()
    updated["alibaba_cookie"] = "new-cookie-secret"

    def fail_write(*_args, **_kwargs):
        raise OSError("injected config write failure")

    monkeypatch.setattr(config_store_module, "write_json", fail_write)
    with pytest.raises(OSError, match="injected config write failure"):
        store.save_app_config(updated)

    assert (
        get_context().db.load_runtime_secrets("app_config")
        == previous_secrets
    )
    assert (
        get_context().paths.app_config_path.read_bytes()
        == previous_file
    )


def test_store_auth_update_rolls_back_when_static_file_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = get_context().config
    store.save_store_config(
        {
            "mercadolibre": {
                "access_token": "old-token-secret",
                "site_id": "MLM",
            }
        }
    )
    previous_auth = get_context().db.list_store_auth()
    previous_file = (
        get_context().paths.store_config_path.read_bytes()
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("injected store write failure")

    monkeypatch.setattr(
        config_store_module.publisher,
        "save_store_config",
        fail_write,
    )
    with pytest.raises(OSError, match="injected store write failure"):
        store.save_store_config(
            {
                "mercadolibre": {
                    "access_token": "new-token-secret",
                    "site_id": "MLA",
                }
            }
        )

    assert get_context().db.list_store_auth() == previous_auth
    assert (
        get_context().paths.store_config_path.read_bytes()
        == previous_file
    )


def test_app_config_failed_rollback_cannot_overwrite_concurrent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    store = context.config
    initial = store.default_app_config()
    initial["alibaba_cookie"] = "initial-cookie-secret"
    store.save_app_config(initial)

    failing = store.load_app_config()
    failing["auto_ai_recognition"] = "failure-write"
    failing["alibaba_cookie"] = "failing-cookie-secret"
    succeeding = store.load_app_config()
    succeeding["auto_ai_recognition"] = "successful-write"
    succeeding["alibaba_cookie"] = "successful-cookie-secret"

    failure_at_file = threading.Event()
    release_failure = threading.Event()
    success_reached_db = threading.Event()
    errors: list[BaseException] = []
    original_write = config_store_module.write_json
    original_replace = context.db.replace_runtime_secrets

    def controlled_write(path, payload):
        if payload.get("auto_ai_recognition") == "failure-write":
            failure_at_file.set()
            assert release_failure.wait(timeout=2)
            raise OSError("injected interleaved app config failure")
        return original_write(path, payload)

    def tracked_replace(namespace, secrets):
        if "successful-cookie-secret" in secrets.values():
            success_reached_db.set()
        return original_replace(namespace, secrets)

    monkeypatch.setattr(config_store_module, "write_json", controlled_write)
    monkeypatch.setattr(context.db, "replace_runtime_secrets", tracked_replace)

    def run_failure() -> None:
        try:
            store.save_app_config(failing)
        except BaseException as exc:
            errors.append(exc)

    failure_thread = threading.Thread(target=run_failure)
    success_thread = threading.Thread(
        target=store.save_app_config,
        args=(succeeding,),
    )
    failure_thread.start()
    assert failure_at_file.wait(timeout=2)
    success_thread.start()
    assert success_reached_db.wait(timeout=0.15) is False
    release_failure.set()
    failure_thread.join(timeout=2)
    success_thread.join(timeout=2)

    assert failure_thread.is_alive() is False
    assert success_thread.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert success_reached_db.is_set()
    assert store.load_app_config()["alibaba_cookie"] == "successful-cookie-secret"


def test_store_config_failed_rollback_cannot_overwrite_concurrent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    store = context.config
    store.save_store_config(
        {
            "mercadolibre": {
                "access_token": "initial-store-token",
                "site_id": "MLM",
            }
        }
    )
    failing = {
        "mercadolibre": {
            "access_token": "failing-store-token",
            "site_id": "FAIL",
        }
    }
    succeeding = {
        "mercadolibre": {
            "access_token": "successful-store-token",
            "site_id": "SUCCESS",
        }
    }

    failure_at_file = threading.Event()
    release_failure = threading.Event()
    success_reached_db = threading.Event()
    errors: list[BaseException] = []
    original_save = config_store_module.publisher.save_store_config
    original_update = context.db.update_store_auth

    def controlled_save(path, payload):
        section = payload.get("mercadolibre", {})
        if section.get("site_id") == "FAIL":
            failure_at_file.set()
            assert release_failure.wait(timeout=2)
            raise OSError("injected interleaved store config failure")
        return original_save(path, payload)

    def tracked_update(platform, **kwargs):
        credentials = kwargs.get("credentials") or {}
        if credentials.get("access_token") == "successful-store-token":
            success_reached_db.set()
        return original_update(platform, **kwargs)

    monkeypatch.setattr(
        config_store_module.publisher,
        "save_store_config",
        controlled_save,
    )
    monkeypatch.setattr(context.db, "update_store_auth", tracked_update)

    def run_failure() -> None:
        try:
            store.save_store_config(failing)
        except BaseException as exc:
            errors.append(exc)

    failure_thread = threading.Thread(target=run_failure)
    success_thread = threading.Thread(
        target=store.save_store_config,
        args=(succeeding,),
    )
    failure_thread.start()
    assert failure_at_file.wait(timeout=2)
    success_thread.start()
    assert success_reached_db.wait(timeout=0.15) is False
    release_failure.set()
    failure_thread.join(timeout=2)
    success_thread.join(timeout=2)

    assert failure_thread.is_alive() is False
    assert success_thread.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert success_reached_db.is_set()
    restored = store.load_store_config()["mercadolibre"]
    assert restored["access_token"] == "successful-store-token"
    assert restored["site_id"] == "SUCCESS"
