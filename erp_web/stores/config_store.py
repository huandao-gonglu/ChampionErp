# -*- coding: utf-8 -*-
from __future__ import annotations

"""App/store configuration store.

``ConfigStore`` owns the two JSON config files (``app_config.json`` static
app settings, ``store_config.json`` non-sensitive marketplace statics) and the
composition with the ``store_auth`` table: secrets and dynamic auth state live
only in SQLite, the file only contributes static fields. Pure auth-status
presentation helpers stay module-level. This module never imports
``erp_web.context`` — the store is constructed by ``AppContext``.
"""

import hashlib
import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from erp_web import app_config as app_config_runtime
from erp_web import marketplaces as publisher
from erp_web.context import AppPaths
from erp_web.db import ErpDatabase
from erp_web.marketplace_registry import MARKETPLACE_SPECS, marketplace_spec
from erp_web.runtime_units.category_store import write_json
from erp_web.services.config_service import (
    is_sensitive_config_key,
    mask_nested_config,
)
from erp_web.stores.product_store import mask_secret

logger = logging.getLogger(__name__)


_STORE_SENSITIVE_FIELDS = {
    "app_id",
    "client_id",
    "app_secret",
    "client_secret",
    "code_verifier",
    "access_token",
    "refresh_token",
    "redirect_uri",
    "content_token",
    "prices_token",
    "marketplace_token",
    "stocks_token",
    "api_key",
    "api_token",
}

# store_auth 表（SQLite）持有的字段：全部秘密 + 动态授权态。
# config/store_config.json 从此只保留非敏感静态项（site_id、listing 等）。
_STORE_AUTH_PLATFORMS = tuple(spec.key for spec in MARKETPLACE_SPECS)
_STORE_CREDENTIAL_FIELDS = _STORE_SENSITIVE_FIELDS | {
    "api_token",
    "user_id",
    "seller_id",
    "nickname",
}
_STORE_AUTH_DETAIL_FIELDS = {
    "auth_masked_account",
    "auth_error_code",
    "auth_error_message",
    "auth_next_action",
    "shop_name",
    "contract_currency",
    "listing_currency",
    "currency_mode",
    "currency_source",
    "currency_verified_at",
    "allowed_currencies",
    # Yandex 在线派生的动态授权/店铺能力（仅存 SQLite store_auth）。
    "business_id",
    "business_name",
    "placement_type",
    "api_availability",
    "api_key_name",
    "auth_scopes",
    "only_default_price",
    "stock_update_mode",
    "warehouse_ids",
    "capabilities_verified_at",
}
_STORE_DB_OWNED_FIELDS = _STORE_CREDENTIAL_FIELDS | _STORE_AUTH_DETAIL_FIELDS | {"auth_status", "auth_checked_at"}

_APP_RUNTIME_SECRET_NAMESPACE = "app_config"
_SECRET_SELECTOR_FIELDS = (
    "id",
    "source_id",
    "sourceId",
    "provider_id",
    "providerId",
    "model_id",
    "modelId",
    "key",
    "slug",
    "name",
)
_SELECTOR_FIELD_KEY = "$field"
_SELECTOR_VALUE_KEY = "$value"
_SELECTOR_FINGERPRINT_KEY = "$fingerprint"


def _is_app_runtime_secret_field(key: Any) -> bool:
    return is_sensitive_config_key(key)


def _secret_identity_projection(value: Any) -> Any:
    """Build a stable, secret-free value used only for list-item matching."""
    if isinstance(value, dict):
        return {
            str(key): (
                ""
                if _is_app_runtime_secret_field(key)
                or str(key).startswith("masked_")
                else _secret_identity_projection(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_secret_identity_projection(item) for item in value]
    return value


def _secret_item_fingerprint(value: Any) -> str:
    serialized = json.dumps(
        _secret_identity_projection(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _secret_list_selector(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        for field in _SECRET_SELECTOR_FIELDS:
            candidate = str(value.get(field) or "").strip()
            if candidate:
                return {
                    _SELECTOR_FIELD_KEY: field,
                    _SELECTOR_VALUE_KEY: candidate,
                }
    return {
        _SELECTOR_FINGERPRINT_KEY: _secret_item_fingerprint(value),
    }


def _secret_path(parts: list[Any]) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _split_app_runtime_secrets(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return file-safe config and path-addressed secrets for SQLite."""
    static = deepcopy(config if isinstance(config, dict) else {})
    secrets: dict[str, Any] = {}

    def visit(value: Any, path: list[Any]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = [*path, str(key)]
                if _is_app_runtime_secret_field(key):
                    if item not in (None, ""):
                        secrets[_secret_path(next_path)] = deepcopy(item)
                    value[key] = ""
                else:
                    visit(item, next_path)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, [*path, _secret_list_selector(item)])

    visit(static, [])
    return static, secrets


def _apply_app_runtime_secrets(
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> dict[str, Any]:
    def select_list_item(items: list[Any], selector: dict[str, Any]) -> Any:
        field = str(selector.get(_SELECTOR_FIELD_KEY) or "")
        expected = str(selector.get(_SELECTOR_VALUE_KEY) or "")
        if field and expected:
            for item in items:
                if (
                    isinstance(item, dict)
                    and str(item.get(field) or "").strip() == expected
                ):
                    return item
            return None
        fingerprint = str(
            selector.get(_SELECTOR_FINGERPRINT_KEY) or ""
        )
        if fingerprint:
            for item in items:
                if _secret_item_fingerprint(item) == fingerprint:
                    return item
        return None

    runtime = deepcopy(config if isinstance(config, dict) else {})
    for encoded_path, secret in secrets.items():
        try:
            parts = json.loads(encoded_path)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "runtime_secrets 包含无法识别的 secret_path；"
                "仅接受当前 selector 路径格式"
            ) from exc
        if not isinstance(parts, list) or not parts:
            raise RuntimeError(
                "runtime_secrets 包含无法识别的 secret_path；"
                "仅接受当前 selector 路径格式"
            )
        if any(isinstance(part, int) for part in parts):
            raise RuntimeError(
                "runtime_secrets 包含已退役的列表 index 路径；"
                "请清空开发数据库后使用当前 selector 路径格式"
            )
        if not isinstance(parts[-1], str):
            raise RuntimeError(
                "runtime_secrets 的 secret_path 末段必须是字段名"
            )
        target: Any = runtime
        valid = True
        for part in parts[:-1]:
            if isinstance(part, dict):
                selector_keys = set(part)
                valid_selector = (
                    selector_keys
                    == {_SELECTOR_FIELD_KEY, _SELECTOR_VALUE_KEY}
                    and bool(str(part.get(_SELECTOR_FIELD_KEY) or "").strip())
                    and bool(str(part.get(_SELECTOR_VALUE_KEY) or "").strip())
                ) or (
                    selector_keys == {_SELECTOR_FINGERPRINT_KEY}
                    and bool(
                        str(
                            part.get(_SELECTOR_FINGERPRINT_KEY) or ""
                        ).strip()
                    )
                )
                if not valid_selector:
                    raise RuntimeError(
                        "runtime_secrets 包含无法识别的 selector"
                    )
                if not isinstance(target, list):
                    valid = False
                    break
                selected = select_list_item(target, part)
                if selected is None:
                    valid = False
                    break
                target = selected
            elif isinstance(part, str):
                if not isinstance(target, dict) or part not in target:
                    valid = False
                    break
                target = target[part]
            else:
                raise RuntimeError(
                    "runtime_secrets 包含无法识别的 secret_path 段"
                )
        if not valid:
            continue
        last = parts[-1]
        if isinstance(target, dict):
            target[last] = deepcopy(secret)
    return runtime


def _strip_db_owned_store_fields(config: dict[str, Any] | None) -> dict[str, Any]:
    """Drop credential/auth-state keys from platform sections (file side only)."""
    stripped = deepcopy(config if isinstance(config, dict) else {})
    for platform in _STORE_AUTH_PLATFORMS:
        section = stripped.get(platform)
        if not isinstance(section, dict):
            continue
        stripped[platform] = {
            key: value for key, value in section.items() if key not in _STORE_DB_OWNED_FIELDS
        }
    return stripped


def _non_empty_db_owned_store_fields(
    config: dict[str, Any] | None,
) -> list[str]:
    """Return file paths that illegally contain DB-owned runtime values."""
    violations: list[str] = []
    source = config if isinstance(config, dict) else {}
    for platform in _STORE_AUTH_PLATFORMS:
        section = source.get(platform)
        if not isinstance(section, dict):
            continue
        for key in _STORE_DB_OWNED_FIELDS:
            if key not in section:
                continue
            value = section[key]
            if value not in (None, ""):
                violations.append(f"{platform}.{key}")
    return sorted(violations)


def _sync_mercadolibre_secret_aliases(store: dict[str, Any]) -> None:
    app_secret = str(store.get("app_secret") or "").strip()
    client_secret = str(store.get("client_secret") or "").strip()
    if client_secret and not app_secret:
        store["app_secret"] = client_secret
    if app_secret and not client_secret:
        store["client_secret"] = app_secret


class ConfigStore:
    """Read/write app + store configuration (file statics ⊕ store_auth table)."""

    def __init__(self, paths: AppPaths, db: ErpDatabase) -> None:
        self._app_config_path = paths.app_config_path
        self._store_config_path = paths.store_config_path
        self._db = db
        self._save_lock = threading.RLock()

    # -- app config -----------------------------------------------------------

    def default_app_config(self) -> dict[str, Any]:
        return app_config_runtime.default_app_config()

    def normalize_app_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return app_config_runtime.normalize_app_config(config)

    def load_app_config(self) -> dict[str, Any]:
        if self._app_config_path.exists():
            try:
                raw = json.loads(
                    self._app_config_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"{self._app_config_path.name} 不是有效的当前 JSON 配置"
                ) from exc
        else:
            raw = self.default_app_config()
        normalized = self.normalize_app_config(raw)
        static_config, file_secrets = _split_app_runtime_secrets(normalized)
        if file_secrets:
            raise RuntimeError(
                f"{self._app_config_path.name} 含有非空明文秘密字段；"
                "当前格式要求秘密仅存 SQLite runtime_secrets"
            )
        stored_secrets = self._db.load_runtime_secrets(
            _APP_RUNTIME_SECRET_NAMESPACE
        )
        if not self._app_config_path.exists():
            write_json(self._app_config_path, static_config)
        runtime = _apply_app_runtime_secrets(static_config, stored_secrets)
        normalized_runtime = self.normalize_app_config(runtime)
        _, canonical_secrets = _split_app_runtime_secrets(
            normalized_runtime
        )
        if canonical_secrets != stored_secrets:
            self._db.replace_runtime_secrets(
                _APP_RUNTIME_SECRET_NAMESPACE,
                canonical_secrets,
            )
        return normalized_runtime

    def save_app_config(self, config: dict[str, Any]) -> None:
        with self._save_lock:
            config = self.normalize_app_config(config)
            static_config, secrets = _split_app_runtime_secrets(config)
            previous_secrets = self._db.load_runtime_secrets(
                _APP_RUNTIME_SECRET_NAMESPACE
            )
            try:
                self._db.replace_runtime_secrets(
                    _APP_RUNTIME_SECRET_NAMESPACE,
                    secrets,
                )
                write_json(self._app_config_path, static_config)
            except BaseException:
                self._db.replace_runtime_secrets(
                    _APP_RUNTIME_SECRET_NAMESPACE,
                    previous_secrets,
                )
                raise

    def merge_app_config_fields(
        self,
        current: dict[str, Any] | None,
        incoming: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Whitelist merge for client-supplied appConfig payloads (no mass assignment).

        Only known top-level app-config keys are accepted; nested dict sections
        (e.g. 1688_api / yunexpress / ai_use_case_bindings) are shallow-merged so a
        partial update does not wipe sibling fields. Unknown keys are ignored with
        a warning.
        """
        merged = deepcopy(current if isinstance(current, dict) else self.default_app_config())
        incoming = incoming if isinstance(incoming, dict) else {}
        allowed_keys = set(self.default_app_config()) | set(app_config_runtime.PRESERVED_APP_CONFIG_KEYS)
        for key, value in incoming.items():
            if key not in allowed_keys:
                logger.warning("merge_app_config_fields 忽略未知 appConfig 顶层键: %s", key)
                continue
            section = merged.get(key)
            if isinstance(value, dict) and isinstance(section, dict):
                for field, field_value in value.items():
                    existing = section.get(field)
                    if (
                        str(existing or "").strip()
                        and mask_nested_config(existing, field) == field_value
                    ):
                        continue
                    section[field] = deepcopy(field_value)
            else:
                merged[key] = deepcopy(value)
        return merged

    # -- store config -----------------------------------------------------------

    def default_store_config(self) -> dict[str, Any]:
        return publisher.load_store_config(self._store_config_path.with_name("__default_store_config__.json"))

    def load_store_config(self) -> dict[str, Any]:
        """Compose the runtime store config: static file + store_auth table.

        读侧唯一入口：所有取 access_token / api_key / app_secret 的调用方拿到的
        值均来自 store_auth 表；文件只贡献 site_id、listing 等静态项。
        """
        file_config = publisher.load_store_config(self._store_config_path)
        violations = _non_empty_db_owned_store_fields(file_config)
        if violations:
            raise RuntimeError(
                f"{self._store_config_path.name} 含有已退役的文件凭据或"
                "授权状态字段："
                + ", ".join(violations)
                + "；当前格式要求这些值仅存 SQLite store_auth"
            )
        static_config = _strip_db_owned_store_fields(file_config)
        raw = static_config
        db_records = self._db.list_store_auth()
        for platform in _STORE_AUTH_PLATFORMS:
            record = db_records.get(platform)
            if not record:
                continue
            section = raw.setdefault(platform, {})
            if not isinstance(section, dict):
                section = {}
                raw[platform] = section
            for key, value in record["credentials"].items():
                if str(value if value is not None else "").strip():
                    section[key] = value
            for key, value in record["auth_detail"].items():
                section[key] = value
            if record["auth_status"]:
                section["auth_status"] = record["auth_status"]
            if record["checked_at"]:
                section["auth_checked_at"] = record["checked_at"]
        return self.normalize_store_config(raw)

    def merge_store_config_fields(
        self,
        base: dict[str, Any] | None,
        updates: dict[str, Any] | None,
        *,
        preserve_empty_sensitive: bool = True,
    ) -> dict[str, Any]:
        merged = deepcopy(base if isinstance(base, dict) else self.default_store_config())
        updates = updates if isinstance(updates, dict) else {}
        for section_key, section_updates in updates.items():
            if not isinstance(section_updates, dict):
                merged[section_key] = deepcopy(section_updates)
                continue
            section = merged.setdefault(section_key, {})
            if not isinstance(section, dict):
                section = {}
                merged[section_key] = section
            for field, value in section_updates.items():
                if (
                    preserve_empty_sensitive
                    and field in _STORE_SENSITIVE_FIELDS
                    and str(section.get(field) or "").strip()
                    and (
                        value in (None, "")
                        or mask_nested_config(section.get(field), field) == value
                    )
                ):
                    continue
                section[field] = deepcopy(value)
            if section_key == "mercadolibre":
                _sync_mercadolibre_secret_aliases(section)
        return merged

    def normalize_store_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        normalized = self.merge_store_config_fields(self.default_store_config(), config, preserve_empty_sensitive=False)
        ml = normalized.get("mercadolibre") if isinstance(normalized.get("mercadolibre"), dict) else {}
        if isinstance(ml, dict) and not str(ml.get("code_verifier") or "").strip():
            ml.pop("code_verifier", None)
        return normalized

    def update_store_config_fields(self, platform: str, fields: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> dict[str, Any]:
        platform = str(platform or "").strip().lower()
        config = self.load_store_config()
        updated = self.merge_store_config_fields(config, {platform: fields}, preserve_empty_sensitive=preserve_empty_sensitive)
        self.save_store_config(updated)
        return updated

    def _invalidate_auth_on_identity_change(
        self,
        config: dict[str, Any],
        *,
        preserve_empty_sensitive: bool,
    ) -> None:
        """身份字段变化时原子清除旧授权能力与成功态。

        对声明了 ``store_binding_fields`` 的平台（Yandex）：真实 token 或
        Campaign ID 任一变化时，在同一次保存中清除旧 business_id、scopes、
        价格/库存能力和旧成功态，并把状态置为“已保存，未测试”。这样即使
        保存后、前端自动测试前进程退出，新 Campaign ID 也不会继承旧店铺
        的授权证明。
        """

        stored_config: dict[str, Any] | None = None
        for spec in MARKETPLACE_SPECS:
            if not spec.store_binding_fields:
                continue
            platform = spec.key
            section = config.get(platform)
            if not isinstance(section, dict):
                continue
            if stored_config is None:
                try:
                    stored_config = self.load_store_config()
                except RuntimeError:
                    stored_config = {}
            stored_section = stored_config.get(platform)
            stored_section = (
                stored_section if isinstance(stored_section, dict) else {}
            )
            changed = False
            for field in spec.credential_fields:
                incoming_value = section.get(field.key)
                stored_value = stored_section.get(field.key)
                if (
                    field.secret
                    and preserve_empty_sensitive
                    and str(stored_value or "").strip()
                    and (
                        incoming_value in (None, "")
                        or mask_nested_config(stored_value, field.key)
                        == incoming_value
                    )
                ):
                    # 空值或脱敏回显视为保留原秘密，不构成身份切换。
                    continue
                if str(
                    incoming_value if incoming_value is not None else ""
                ) != str(stored_value if stored_value is not None else ""):
                    changed = True
                    break
            if not changed:
                continue
            for field_name in _STORE_AUTH_DETAIL_FIELDS:
                section[field_name] = ""
            section["auth_status"] = "已保存，未测试"
            section["auth_checked_at"] = ""

    def _save_store_auth_sections(self, config: dict[str, Any], *, preserve_empty_sensitive: bool) -> None:
        """Route secrets and dynamic auth state of each platform into store_auth."""
        for platform in _STORE_AUTH_PLATFORMS:
            section = config.get(platform)
            if not isinstance(section, dict):
                continue
            if platform == "mercadolibre":
                _sync_mercadolibre_secret_aliases(section)
            credentials = {key: section[key] for key in _STORE_CREDENTIAL_FIELDS if key in section}
            auth_detail = {key: section[key] for key in _STORE_AUTH_DETAIL_FIELDS if key in section}
            auth_status = section.get("auth_status") if "auth_status" in section else None
            checked_at = section.get("auth_checked_at") if "auth_checked_at" in section else None
            if not credentials and not auth_detail and auth_status is None and checked_at is None:
                continue
            self._db.update_store_auth(
                platform,
                credentials=credentials if credentials else None,
                replace_credentials=not preserve_empty_sensitive,
                auth_status=None if auth_status is None else str(auth_status),
                auth_detail=auth_detail if auth_detail else None,
                checked_at=None if checked_at is None else str(checked_at),
            )

    def save_store_config(self, config: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> None:
        with self._save_lock:
            config = config if isinstance(config, dict) else {}
            previous_auth = self._db.list_store_auth()
            try:
                self._invalidate_auth_on_identity_change(
                    config,
                    preserve_empty_sensitive=preserve_empty_sensitive,
                )
                self._save_store_auth_sections(
                    config,
                    preserve_empty_sensitive=preserve_empty_sensitive,
                )
                self._store_config_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                static_config = _strip_db_owned_store_fields(config)
                if preserve_empty_sensitive:
                    existing = (
                        publisher.load_store_config(
                            self._store_config_path
                        )
                        if self._store_config_path.exists()
                        else self.default_store_config()
                    )
                    merged = self.merge_store_config_fields(
                        _strip_db_owned_store_fields(existing),
                        static_config,
                        preserve_empty_sensitive=True,
                    )
                else:
                    merged = self.normalize_store_config(static_config)
                publisher.save_store_config(
                    self._store_config_path,
                    _strip_db_owned_store_fields(merged),
                )
            except BaseException:
                self._db.replace_store_auth_snapshot(previous_auth)
                raise

    def clear_store_auth(self, platform: str) -> dict[str, Any]:
        """Clear credentials/auth state without deleting non-sensitive settings."""

        platform = str(platform or "").strip().lower()
        if marketplace_spec(platform) is None:
            raise ValueError(f"未注册的平台：{platform or '(空)'}")
        self._db.delete_store_auth(platform)
        return self.load_store_config()

    def mercadolibre_auth_checklist(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        ml = config if isinstance(config, dict) else self.load_store_config().get("mercadolibre", {})
        app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
        app_secret = str(ml.get("app_secret") or ml.get("client_secret") or "").strip()
        redirect_uri = str(ml.get("redirect_uri") or "").strip()
        site_id = str(ml.get("site_id") or "CBT").strip() or "CBT"
        code_verifier = str(ml.get("code_verifier") or "").strip()
        access_token = str(ml.get("access_token") or "").strip()
        refresh_token = str(ml.get("refresh_token") or "").strip()
        missing: list[str] = []
        if not app_id:
            missing.append("APP_ID_MISSING")
        if not app_secret:
            missing.append("CLIENT_SECRET_MISSING")
        if not redirect_uri:
            missing.append("REDIRECT_URI_MISSING")
        elif not redirect_uri.lower().startswith("https://"):
            missing.append("REDIRECT_URI_MUST_BE_HTTPS")
        ready_for_auth_link = not any(code in missing for code in {"APP_ID_MISSING", "CLIENT_SECRET_MISSING", "REDIRECT_URI_MISSING", "REDIRECT_URI_MUST_BE_HTTPS"})
        token_ready = bool(access_token and refresh_token)
        if not ready_for_auth_link:
            if "APP_ID_MISSING" in missing:
                next_action = "填写 Mercado Libre Developers 里的 App ID / Client ID。"
            elif "CLIENT_SECRET_MISSING" in missing:
                next_action = "填写 Mercado Libre Developers 里的 Client Secret。"
            elif "REDIRECT_URI_MISSING" in missing:
                next_action = "填写 Redirect URI，默认可用 https://example.com/callback。"
            else:
                next_action = "Redirect URI 必须以 https:// 开头，并与 Developers 后台完全一致。"
        elif not token_ready:
            next_action = "生成授权链接，用店铺主账号浏览器打开，复制 code 回 ERP 换 token。"
        else:
            next_action = "授权配置已具备。到草稿的类目/属性页实时匹配 Mercado Libre 类目，并按选中类目读取必填属性。"
        fields = [
            {"key": "app_id", "label": "App ID / Client ID", "ok": bool(app_id), "value": mask_secret(app_id) if app_id else "缺失"},
            {"key": "app_secret", "label": "Client Secret", "ok": bool(app_secret), "value": mask_secret(app_secret) if app_secret else "缺失"},
            {"key": "redirect_uri", "label": "Redirect URI", "ok": bool(redirect_uri) and redirect_uri.lower().startswith("https://"), "value": redirect_uri or "缺失"},
            {"key": "site_id", "label": "Site", "ok": bool(site_id), "value": site_id},
            {"key": "code_verifier", "label": "code_verifier", "ok": bool(code_verifier), "value": "已生成，等待 code 换 token" if code_verifier else "未生成"},
            {"key": "access_token", "label": "Access Token", "ok": bool(access_token), "value": mask_secret(access_token) if access_token else "未保存"},
            {"key": "refresh_token", "label": "Refresh Token", "ok": bool(refresh_token), "value": mask_secret(refresh_token) if refresh_token else "未保存"},
        ]
        lines = ["Mercado Libre 授权配置检查清单"]
        lines.extend([f"- {item['label']}：{'OK' if item['ok'] else '缺失/需检查'}（{item['value']}）" for item in fields])
        lines.append(f"- 下一步：{next_action}")
        return {
            "platform": "mercadolibre",
            "ready_for_auth_link": ready_for_auth_link,
            "token_ready": token_ready,
            "missing_codes": missing,
            "fields": fields,
            "next_action": next_action,
            "copy_text": "\n".join(lines),
        }


# -- pure auth-status presentation helpers -----------------------------------

# 未注册平台的兜底凭证判断键；已注册平台一律用
# ``MarketplaceSpec.credential_fields`` 判断，不再往这里追加新平台凭据。
_LEGACY_CREDENTIAL_KEYS = (
    "access_token",
    "refresh_token",
    "app_id",
    "app_secret",
    "code_verifier",
    "content_token",
    "prices_token",
    "marketplace_token",
    "stocks_token",
    "client_id",
    "api_key",
)

_DEFAULT_STORE_SECTION_CACHE: dict[str, Any] | None = None


def _default_store_section(platform: str) -> dict[str, Any]:
    """返回平台在默认 store config 中的静态字段，用于排除默认值凭证。"""

    global _DEFAULT_STORE_SECTION_CACHE
    if _DEFAULT_STORE_SECTION_CACHE is None:
        _DEFAULT_STORE_SECTION_CACHE = publisher.load_store_config(
            Path("__default_store_config_probe__.json")
        )
    section = _DEFAULT_STORE_SECTION_CACHE.get(platform)
    return section if isinstance(section, dict) else {}


def _has_registry_credentials(platform: str, store: dict[str, Any]) -> bool:
    """按注册表凭据描述符判断是否存在已保存凭证。

    与默认值相同的字段（例如 ML 的 ``site_id=CBT``）不算凭证，避免未配置
    平台永远显示“已保存，未测试”。
    """

    spec = marketplace_spec(platform)
    if spec is None:
        return any(
            str(store.get(key) or "").strip() for key in _LEGACY_CREDENTIAL_KEYS
        )
    defaults = _default_store_section(platform)
    for field in spec.credential_fields:
        value = str(store.get(field.key) or "").strip()
        if not value:
            continue
        if value == str(defaults.get(field.key) or "").strip():
            continue
        return True
    return False


def _auth_status_label(status: Any, store: dict[str, Any], has_credentials: bool = False) -> str:
    text = str(status or "").strip().lower()
    error_code = str(store.get("auth_error_code") or "").strip().lower()
    error_message = str(store.get("auth_error_message") or "").strip().lower()
    if text in {"ok", "success", "tested", "测试成功"}:
        return "测试成功"
    if text in {"failed", "error", "测试失败"}:
        if "429" in error_code or "429" in error_message or "420" in error_code or "420" in error_message or "rate" in error_code or "too many requests" in error_message or "限流" in error_message:
            return "被限流"
        if "expired" in error_code or "expired" in error_message:
            return "Token 过期"
        if "permission" in error_code or "401" in error_message or "403" in error_message or "unauthorized" in error_message:
            return "权限不足"
        return "测试失败"
    if text in {"saved", "pending", "saved_not_tested", "已保存，未测试"}:
        return "已保存，未测试"
    if has_credentials:
        return "已保存，未测试"
    return "未配置"


def auth_next_action(platform: str, status_label: str, error_code: str, error_message: str) -> str:
    platform = str(platform or "").strip().lower()
    error_code_l = str(error_code or "").strip().lower()
    error_message_l = str(error_message or "").strip().lower()
    if status_label == "测试成功":
        return "已可用于发布"
    if status_label == "被限流":
        return "等待一段时间后重新测试"
    if status_label == "Token 过期":
        return {
            "mercadolibre": "使用刷新 token 更新 access token",
        }.get(platform, "重新生成并保存 token")
    if status_label == "权限不足":
        return {
            "mercadolibre": "检查 App 权限和授权范围",
        }.get(platform, "检查 Token 权限是否包含当前接口")
    if "redirect_uri" in error_code_l or "redirect_uri" in error_message_l:
        return "检查 Redirect URI 是否与开发者后台完全一致"
    if "invalid_client" in error_code_l:
        return "检查 App ID / Client Secret 是否正确"
    if "invalid_grant" in error_code_l or "refresh token invalid" in error_message_l:
        return "重新生成授权链接并重新授权"
    if "callback" in error_code_l or "callback" in error_message_l:
        return "确认回调地址可访问且已正确注册"
    if "network" in error_code_l or "ssl" in error_message_l or "unexpected_eof" in error_message_l or "eof occurred" in error_message_l:
        return "检查本机网络、代理或防火墙后重试 Mercado Libre 授权接口"
    return {
        "mercadolibre": "重新发起授权并检查回调地址",
        "yandex": "确认 Yandex API Token 已保存且具备目标接口权限",
        "ozon": "确认 Client ID 和 API Key 已保存且未过期",
    }.get(platform, "检查配置后重新测试")


def explain_mercadolibre_auth_error(error_code: str = "", error_message: str = "") -> dict[str, str]:
    # Function-level import: publish_logs_runtime sits above the stores in the
    # import graph, so a top-level import here would be a circular dependency.
    from erp_web.runtime_units.publish_logs_runtime import mercadolibre_test_error_code

    code = str(error_code or "").strip()
    message = str(error_message or "").strip()
    text = f"{code} {message}".lower()
    normalized = mercadolibre_test_error_code(text) if code.lower() not in {
        "invalid_grant",
        "redirect_uri_mismatch",
        "code_verifier_missing",
        "token_expired",
        "refresh_token_invalid",
        "invalid_client",
    } else code.lower()
    if "code_verifier" in text:
        normalized = "code_verifier_missing"
    if "redirect_uri" in text and ("mismatch" in text or "different" in text or "does not match" in text):
        normalized = "redirect_uri_mismatch"
    if "expired" in text and "token" in text:
        normalized = "token_expired"
    if "ssl" in text or "unexpected_eof" in text or "eof occurred" in text or "urlopen error" in text:
        normalized = "network_tls_failed"
    if normalized == "invalid_grant":
        return {
            "platform": "mercadolibre",
            "code": "invalid_grant",
            "title": "授权 code 已失效或已被使用",
            "plain_message": "Mercado Libre 的 code 是一次性的，通常几分钟内有效；粘贴慢了、重复使用、或重新生成过授权链接都会导致这个错误。",
            "next_action": "重新生成授权链接，用已登录店铺主账号的浏览器打开，授权后立刻复制地址栏里的 code 回 ERP 换 token。",
        }
    if normalized == "redirect_uri_mismatch":
        return {
            "platform": "mercadolibre",
            "code": "redirect_uri_mismatch",
            "title": "Redirect URI 不一致",
            "plain_message": "ERP 里填写的 Redirect URI 必须和 Mercado Libre Developers 后台应用里保存的地址完全一致，包括 https、路径和末尾斜杠。",
            "next_action": "检查 ERP 和 Mercado Libre Developers 后台的 Redirect URI，保持完全一致后重新生成授权链接。",
        }
    if normalized == "code_verifier_missing":
        return {
            "platform": "mercadolibre",
            "code": "CODE_VERIFIER_MISSING",
            "title": "缺少本次授权链接对应的 code_verifier",
            "plain_message": "PKCE 授权要求“生成授权链接”和“用 code 换 token”必须来自同一次流程。重启 ERP、清空配置或直接粘旧 code 都可能缺这个值。",
            "next_action": "重新生成授权链接，不要复用旧 code；授权后直接回到当前 ERP 页面换 token。",
        }
    if normalized in {"token_expired", "refresh_token_invalid"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Token 已过期或 Refresh Token 不可用",
            "plain_message": "当前保存的 Mercado Libre token 不能继续调用接口，可能是过期、被后台撤销，或复制了不完整的 token。",
            "next_action": "先点击刷新 token；如果仍失败，重新生成授权链接并重新授权。",
        }
    if normalized == "invalid_client":
        return {
            "platform": "mercadolibre",
            "code": "invalid_client",
            "title": "App ID 或 Client Secret 不正确",
            "plain_message": "Mercado Libre 不认可当前应用信息，通常是 App ID、Client Secret 填错，或复制时多了空格。",
            "next_action": "回 Mercado Libre Developers 应用详情复制 App ID 和 Client Secret，保存后重新生成授权链接。",
        }
    if normalized in {"NETWORK_BLOCKED", "NETWORK_TIMEOUT", "network_tls_failed"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Mercado Libre 授权接口网络连接失败",
            "plain_message": "ERP 已请求 Mercado Libre token 接口，但 HTTPS/TLS 连接在读取响应时被提前断开，常见原因是代理、VPN、公司网络 TLS 拦截、防火墙或临时网络抖动。",
            "next_action": "确认当前电脑能稳定访问 https://api.mercadolibre.com，关闭会拦截 HTTPS 的代理/抓包工具后重试；如果必须走代理，请让 Python/系统网络也使用同一代理。",
        }
    return {
        "platform": "mercadolibre",
        "code": normalized or code or "mercadolibre_auth_failed",
        "title": "Mercado Libre 授权失败",
        "plain_message": message or "授权接口返回失败，但没有提供更具体的错误原因。",
        "next_action": auth_next_action("mercadolibre", "测试失败", normalized or code, message),
    }


def summarize_store_auth(platform: str, store: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    store = store if isinstance(store, dict) else {}
    status_label = _auth_status_label(
        store.get("auth_status"),
        store,
        _has_registry_credentials(platform, store),
    )
    error_code = str(store.get("auth_error_code") or "").strip()
    error_message = str(store.get("auth_error_message") or "").strip()
    masked_account = str(store.get("auth_masked_account") or "").strip()
    spec = marketplace_spec(platform)
    if not masked_account:
        secret_fields = frozenset(spec.secret_credential_keys()) if spec else frozenset()
        for field in spec.masked_account_fields if spec else ():
            value = str(store.get(field) or "").strip()
            if value:
                masked_account = mask_secret(value) if field in secret_fields else value
                break
    if not masked_account:
        candidate_keys = (
            spec.secret_credential_keys()
            if spec
            else ("access_token", "refresh_token", "api_token", "api_key", "app_secret")
        )
        candidates = [store.get(key) for key in candidate_keys]
        for candidate in candidates:
            if str(candidate or "").strip():
                masked_account = mask_secret(candidate)
                break
    return {
        "platform": platform,
        "status": status_label,
        "checked_at": str(store.get("auth_checked_at") or "").strip(),
        "masked_account": masked_account,
        "error_code": error_code,
        "error_message": error_message,
        "next_action": str(store.get("auth_next_action") or auth_next_action(platform, status_label, error_code, error_message)).strip(),
        "shop_name": str(store.get("shop_name") or "").strip(),
        "site_id": str(store.get("site_id") or store.get("country") or "").strip(),
        "bound": status_label in {"测试成功", "已绑定"},
    }


def summarize_store_auth_states(store_config: dict[str, Any]) -> dict[str, Any]:
    store_config = store_config if isinstance(store_config, dict) else {}
    return {
        platform: summarize_store_auth(platform, store_config.get(platform, {}))
        for platform in _STORE_AUTH_PLATFORMS
    }


def store_auth_failure_code(platform: str, message: str) -> str:
    text = str(message or "").lower()
    platform = str(platform or "").strip().lower()
    # Yandex 用 HTTP 420 表示限流；不能只识别 429。
    if (
        "429" in text
        or "420" in text
        or "too many requests" in text
        or "限流" in text
        or "rate limit" in text
    ):
        return "rate_limited"
    if "401" in text or "403" in text or "unauthorized" in text:
        return "permission_denied"
    if platform == "mercadolibre":
        if "redirect_uri" in text and "mismatch" in text:
            return "redirect_uri_mismatch"
        if "invalid_client" in text or "client_id" in text and "invalid" in text:
            return "invalid_client"
        if "invalid_grant" in text:
            return "invalid_grant"
        if "refresh token" in text and "invalid" in text:
            return "refresh_token_invalid"
        if "expired" in text and "token" in text:
            return "token_expired"
        if "callback" in text:
            return "callback_not_received"
    spec = marketplace_spec(platform)
    return spec.auth_failure_code if spec else "auth_failed"


def _store_auth_result_fields(
    platform: str,
    status: str,
    account: str = "",
    error_code: str = "",
    error_message: str = "",
    next_action: str = "",
) -> dict[str, str]:
    from erp_web.runtime_units.collect_helpers import collect_time_iso

    platform = str(platform or "").strip().lower()
    account_text = str(account or "").strip()
    error_code_text = str(error_code or "").strip()
    error_message_text = str(error_message or "").strip()
    next_action_text = str(next_action or "").strip()
    return {
        "auth_status": status,
        "auth_checked_at": collect_time_iso(),
        "auth_masked_account": account_text,
        "auth_error_code": error_code_text,
        "auth_error_message": error_message_text,
        "auth_next_action": next_action_text or auth_next_action(platform, status, error_code_text, error_message_text),
    }


__all__ = [
    "ConfigStore",
    "auth_next_action",
    "explain_mercadolibre_auth_error",
    "store_auth_failure_code",
    "summarize_store_auth",
    "summarize_store_auth_states",
]
