# -*- coding: utf-8 -*-
from __future__ import annotations

"""店铺发布币种事实源切换迁移测试（迁移方案 §13）。"""

import json
from pathlib import Path

from erp_web.db import ErpDatabase
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.stores.store_currency_migration import migrate_store_currency_source


def _db(tmp_path: Path) -> ErpDatabase:
    return ErpDatabase(tmp_path / "erp.sqlite3")


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "store_config.json"


def _write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def test_ozon_contract_currency_migrates_to_locked_ready(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "ozon",
        credentials={"client_id": "client-1", "api_key": "key"},
        auth_status="测试成功",
        auth_detail={
            "contract_currency": "CNY",
            "listing_currency": "",
            "currency_mode": "account_locked",
            "currency_source": "account_api",
            "currency_verified_at": "2026-08-01T00:00:00Z",
            "allowed_currencies": ["CNY"],
        },
    )

    assert migrate_store_currency_source(db, _config_path(tmp_path)) is True

    detail = db.get_store_auth("ozon")["auth_detail"]
    assert "contract_currency" not in detail
    assert detail["listing_currency"] == "CNY"
    assert detail["allowed_currencies"] == ["CNY"]
    assert detail["currency_mode"] == "locked"
    assert detail["currency_status"] == "ready"
    assert detail["currency_source"] == "account_api"
    assert detail["currency_fingerprint"] == compute_currency_fingerprint(
        "ozon", "client-1", "CNY", ["CNY"], "locked", "account_api"
    )


def test_yandex_legacy_static_currency_reset_to_unresolved(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "yandex",
        credentials={"api_token": "token", "campaign_id": "111"},
        auth_status="测试成功",
        auth_detail={
            "business_id": "222",
            "listing_currency": "RUB",
            "currency_mode": "campaign_locked",
            "currency_source": "campaign_rule",
        },
    )

    migrate_store_currency_source(db, _config_path(tmp_path))

    detail = db.get_store_auth("yandex")["auth_detail"]
    # 店铺能力字段保留；静态币种推断清空为 unresolved，等待重新授权发现。
    assert detail["business_id"] == "222"
    assert detail.get("listing_currency", "") == ""
    assert "currency_mode" not in detail
    assert "currency_source" not in detail


def test_mercadolibre_legacy_site_rule_reset_to_unresolved(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "mercadolibre",
        credentials={"access_token": "token"},
        auth_status="测试失败",
        auth_detail={
            "listing_currency": "MXN",
            "currency_mode": "site_locked",
            "currency_source": "site_rule",
        },
    )

    migrate_store_currency_source(db, _config_path(tmp_path))

    detail = db.get_store_auth("mercadolibre")["auth_detail"]
    assert detail.get("listing_currency", "") == ""
    assert "currency_mode" not in detail
    assert "currency_source" not in detail


def test_new_format_records_are_not_modified(tmp_path: Path) -> None:
    db = _db(tmp_path)
    fingerprint = compute_currency_fingerprint(
        "ozon", "client-1", "CNY", ["CNY"], "locked", "account_api"
    )
    db.update_store_auth(
        "ozon",
        credentials={"client_id": "client-1"},
        auth_status="测试成功",
        auth_detail={
            "listing_currency": "CNY",
            "allowed_currencies": ["CNY"],
            "currency_mode": "locked",
            "currency_status": "ready",
            "currency_source": "account_api",
            "currency_fingerprint": fingerprint,
        },
    )

    assert migrate_store_currency_source(db, _config_path(tmp_path)) is False

    detail = db.get_store_auth("ozon")["auth_detail"]
    assert detail["currency_fingerprint"] == fingerprint
    assert detail["currency_status"] == "ready"


def test_static_json_retired_fields_are_stripped(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "mercadolibre",
        credentials={"access_token": "token"},
        auth_status="测试成功",
        auth_detail={"shop_name": "SHOP"},
    )
    path = _config_path(tmp_path)
    _write_config(
        path,
        {
            "mercadolibre": {
                "site_id": "MLM",
                "category_id": "",
                "account_site_id": "CBT",
                "listing_currency": "MXN",
            },
            "ozon": {"category_id": "", "contract_currency": "RUB"},
            "listing": {"stock": "10", "currency_id": "MXN"},
        },
    )

    assert migrate_store_currency_source(db, path) is True

    config = json.loads(path.read_text(encoding="utf-8"))
    # account_site_id：成功授权记录移入 auth detail，静态文件物理删除。
    assert "account_site_id" not in config["mercadolibre"]
    assert config["mercadolibre"]["site_id"] == "MLM"
    assert "listing_currency" not in config["mercadolibre"]
    assert "contract_currency" not in config["ozon"]
    assert "currency_id" not in config["listing"]
    assert config["listing"]["stock"] == "10"
    assert (
        db.get_store_auth("mercadolibre")["auth_detail"]["account_site_id"]
        == "CBT"
    )


def test_static_json_account_site_id_discarded_for_failed_auth(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "mercadolibre",
        credentials={"access_token": "token"},
        auth_status="测试失败",
        auth_detail={},
    )
    path = _config_path(tmp_path)
    _write_config(path, {"mercadolibre": {"site_id": "MLM", "account_site_id": "CBT"}})

    migrate_store_currency_source(db, path)

    config = json.loads(path.read_text(encoding="utf-8"))
    assert "account_site_id" not in config["mercadolibre"]
    assert "account_site_id" not in db.get_store_auth("mercadolibre")["auth_detail"]


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.update_store_auth(
        "ozon",
        credentials={"client_id": "client-1"},
        auth_status="测试成功",
        auth_detail={
            "contract_currency": "CNY",
            "currency_mode": "account_locked",
            "currency_source": "account_api",
        },
    )
    path = _config_path(tmp_path)
    _write_config(path, {"ozon": {"category_id": ""}, "listing": {"currency_id": "MXN"}})

    assert migrate_store_currency_source(db, path) is True
    snapshot = db.list_store_auth()
    assert migrate_store_currency_source(db, path) is False
    assert db.list_store_auth() == snapshot
