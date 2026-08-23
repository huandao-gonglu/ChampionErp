# -*- coding: utf-8 -*-
from __future__ import annotations

"""店铺发布币种事实源切换的一次性内容迁移（迁移方案 §13）。

幂等：只处理仍携带退役字段（contract_currency、旧 mode/source、静态 JSON
派生币种）的数据；迁移后退役键被物理删除，再次运行为无操作，授权服务后续
写入的新可信状态也不会被覆盖。迁移不改变 SQL schema。
"""

import json
from pathlib import Path
from typing import Any

from erp_web import marketplaces as publisher
from erp_web.db import ErpDatabase
from erp_web.services.listing_currency_service import (
    compute_currency_fingerprint,
    store_identity_for_platform,
)

_CURRENCY_DETAIL_KEYS = (
    "listing_currency",
    "allowed_currencies",
    "currency_mode",
    "currency_status",
    "currency_source",
    "currency_verified_at",
    "currency_fingerprint",
    "currency_error_code",
    "currency_error_message",
)


def _clear_currency_fields(detail: dict[str, Any]) -> None:
    for key in _CURRENCY_DETAIL_KEYS:
        detail.pop(key, None)


def _migrate_auth_detail(
    platform: str,
    credentials: dict[str, Any],
    detail: dict[str, Any],
) -> bool:
    """迁移单条 store_auth 的币种状态；返回是否有变化。"""

    # 新格式记录（带指纹或带新 currency_status 字段，由新代码写入）不再参与
    # 旧状态迁移；仅确保退役键不残留。
    if (
        str(detail.get("currency_fingerprint") or "").strip()
        or str(detail.get("currency_status") or "").strip()
    ):
        if "contract_currency" in detail:
            detail.pop("contract_currency", None)
            return True
        return False

    changed = False
    contract_currency = ""
    if "contract_currency" in detail:
        contract_currency = str(detail.pop("contract_currency", "") or "").strip().upper()
        changed = True
    listing_currency = str(detail.get("listing_currency") or "").strip().upper()
    mode = str(detail.get("currency_mode") or "").strip()
    source = str(detail.get("currency_source") or "").strip()
    has_currency_keys = any(key in detail for key in _CURRENCY_DETAIL_KEYS)

    if platform == "ozon":
        currency = listing_currency or contract_currency
        if currency and source in {"account_api", ""}:
            # 现有 account_api 合同币种一致 → 迁移为 locked + ready。
            identity = store_identity_for_platform(platform, credentials)
            detail.update(
                {
                    "listing_currency": currency,
                    "allowed_currencies": [currency],
                    "currency_mode": "locked",
                    "currency_status": "ready",
                    "currency_source": "account_api",
                    "currency_fingerprint": compute_currency_fingerprint(
                        platform, identity, currency, [currency], "locked", "account_api"
                    ),
                    "currency_error_code": "",
                    "currency_error_message": "",
                }
            )
            return True
        # 其余 Ozon 旧状态不可信，要求重新测试授权。
        if has_currency_keys or contract_currency:
            _clear_currency_fields(detail)
            return True
        return changed

    # Yandex / Mercado Libre：静态站点/规则推断与旧字段一律不可信，
    # 重置为 unresolved，等待授权测试重新发现。
    if has_currency_keys or contract_currency:
        _clear_currency_fields(detail)
        return True
    return changed


def _migrate_static_config(
    store_config_path: Path,
    snapshot: dict[str, dict[str, Any]],
) -> tuple[bool, bool]:
    """剥离静态 JSON 中的派生币种字段；按授权状态迁移 account_site_id。

    返回 ``(文件是否变化, 是否有身份字段移入 auth detail)``。
    """

    if not store_config_path.exists():
        return False, False
    try:
        config = json.loads(store_config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, False
    if not isinstance(config, dict):
        return False, False

    changed = False
    identity_moved = False
    ml = config.get("mercadolibre")
    if isinstance(ml, dict) and "account_site_id" in ml:
        account_site_id = str(ml.pop("account_site_id") or "").strip()
        changed = True
        ml_record = snapshot.get("mercadolibre") or {}
        ml_detail = ml_record.get("auth_detail") if isinstance(ml_record, dict) else {}
        if (
            account_site_id
            and str(ml_record.get("auth_status") or "") == "测试成功"
            and isinstance(ml_detail, dict)
        ):
            # 仅成功授权记录保留远端身份；失败/无凭据记录的遗留值直接丢弃。
            ml_detail["account_site_id"] = account_site_id
            identity_moved = True

    for section_key in ("mercadolibre", "yandex", "ozon"):
        section = config.get(section_key)
        if not isinstance(section, dict):
            continue
        for retired_key in (
            "listing_currency",
            "contract_currency",
            "allowed_currencies",
            "currency_mode",
            "currency_status",
            "currency_source",
            "currency_verified_at",
            "currency_fingerprint",
            "currency_error_code",
            "currency_error_message",
        ):
            if retired_key in section:
                section.pop(retired_key, None)
                changed = True

    listing = config.get("listing")
    if isinstance(listing, dict) and "currency_id" in listing:
        listing.pop("currency_id", None)
        changed = True

    if changed:
        publisher.save_store_config(store_config_path, config)
    return changed, identity_moved


def migrate_store_currency_source(
    db: ErpDatabase,
    store_config_path: Path,
) -> bool:
    """执行一次性内容迁移；幂等，可安全在 ConfigStore 初始化时调用。"""

    changed = False
    snapshot = db.list_store_auth()
    for platform, record in snapshot.items():
        detail = record.get("auth_detail")
        if not isinstance(detail, dict):
            continue
        if _migrate_auth_detail(
            platform,
            record.get("credentials") if isinstance(record.get("credentials"), dict) else {},
            detail,
        ):
            changed = True
    file_changed, identity_moved = _migrate_static_config(
        store_config_path, snapshot
    )
    if changed or identity_moved:
        db.replace_store_auth_snapshot(snapshot)
    return changed or file_changed


__all__ = ["migrate_store_currency_source"]
