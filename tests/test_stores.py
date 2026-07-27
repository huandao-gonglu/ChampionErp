from __future__ import annotations

"""ProductStore / ConfigStore：经 AppContext 的基本 CRUD 与配置读写。"""

import json

from erp_web.context import get_context
from erp_web.runtime_units import product_store as product_store_unit
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION
from erp_web.services import config_service


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


def test_runtime_product_store_functions_delegate_to_context_store() -> None:
    saved = product_store_unit.save_product(_sample_product("Delegate check", "https://example.com/delegate"))

    assert get_context().db.load_product_model(saved["product_id"])["name"] == "Delegate check"
    assert [item["product_id"] for item in product_store_unit.load_products_index()] == [saved["product_id"]]

    result = product_store_unit.delete_products_from_index([saved["product_id"]])
    assert result["deleted"] == 1
    assert product_store_unit.load_products_index() == []


def test_config_store_app_config_roundtrip_and_whitelist_merge() -> None:
    config_store = get_context().config

    config = config_store.load_app_config()
    assert get_context().paths.app_config_path.exists()

    merged = config_store.merge_app_config_fields(config, {"unknown_top_level_key": {"x": 1}})
    assert "unknown_top_level_key" not in merged

    config["alibaba_cookie"] = "cookie-abc"
    config_store.save_app_config(config)
    assert config_store.load_app_config()["alibaba_cookie"] == "cookie-abc"


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
        "ml-private-token",
        "ml-private-refresh",
        "pkce-private-verifier",
        "ozon-private-key",
    ):
        assert secret not in serialized
    assert public_app["1688_api"]["app_secret"]
    assert public_store["ozon"]["client_id"] == "ozon-client"

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
