from __future__ import annotations

"""店铺凭据与应用设置暴露给 HTTP 层的显式入口。"""

from typing import Any

from erp_web.context import get_context
from erp_web.services import config_service
from erp_web.services.browser_debug_service import (
    open_auth_link_in_browser,
)
from erp_web.runtime_units.store_credentials import (
    build_mercadolibre_auth_link,
    exchange_mercadolibre_code_from_body,
    refresh_mercadolibre_token_from_body,
    test_api_config,
    test_store_auth,
)
from erp_web.stores.config_store import (
    auth_next_action,
    explain_mercadolibre_auth_error,
    summarize_store_auth_states,
)
from erp_web.runtime_units.publish_logs_runtime import (
    append_ml_auth_test_log,
    mercadolibre_test_error_code,
)
from erp_web.runtime_units.publish_mercadolibre import run_mercadolibre_07d_test
ResponseWithStatus = tuple[dict[str, Any], int]


def load_app_config() -> dict[str, Any]:
    return get_context().config.load_app_config()


def save_app_config(config: dict[str, Any]) -> None:
    get_context().config.save_app_config(config)


def merge_app_config_fields(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    return get_context().config.merge_app_config_fields(
        current,
        incoming,
    )


def load_store_config() -> dict[str, Any]:
    return get_context().config.load_store_config()


def save_store_config(
    config: dict[str, Any],
    *,
    preserve_empty_sensitive: bool = True,
) -> None:
    get_context().config.save_store_config(
        config,
        preserve_empty_sensitive=preserve_empty_sensitive,
    )


def merge_store_config_fields(
    base: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    *,
    preserve_empty_sensitive: bool = True,
) -> dict[str, Any]:
    return get_context().config.merge_store_config_fields(
        base,
        updates,
        preserve_empty_sensitive=preserve_empty_sensitive,
    )


def clear_store_auth(platform: str) -> dict[str, Any]:
    return get_context().config.clear_store_auth(platform)


def mercadolibre_auth_checklist(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return get_context().config.mercadolibre_auth_checklist(config)


def load_required_product_from_body(
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.load_required_product_from_body(body)


def _app_dir():
    return get_context().paths.app_dir


def save_ai_config_payload(body: dict[str, Any]) -> ResponseWithStatus:
    app_dir = _app_dir()
    config_service.write_env_template(app_dir)
    incoming = body.get("config") if isinstance(body.get("config"), dict) else body
    app_config = config_service.merge_ai_config(
        app_dir,
        load_app_config(),
        incoming,
    )
    save_app_config(app_config)
    config_service.save_config_snapshot(app_dir, app_config)
    return {
        "ok": True,
        "config": config_service.public_ai_config(app_dir, load_app_config()),
    }, 200


def mercadolibre_auth_link_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = build_mercadolibre_auth_link(
            str(body.get("app_id") or ""),
            str(body.get("redirect_uri") or ""),
        )
        return {"ok": True, **result}, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def mercadolibre_auth_checklist_payload() -> ResponseWithStatus:
    return {"ok": True, "checklist": mercadolibre_auth_checklist()}, 200


def open_auth_link_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = open_auth_link_in_browser(
            str(body.get("url") or ""),
            str(body.get("browser") or "default"),
        )
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def _mercadolibre_error_payload(message: str) -> dict[str, Any]:
    code = mercadolibre_test_error_code(message)
    explanation = explain_mercadolibre_auth_error(code, message)
    return {
        "ok": False,
        "error": message,
        "error_code": explanation["code"],
        "next_action": explanation["next_action"],
        "auth_explanation": explanation,
    }


def exchange_mercadolibre_code_payload(
    body: dict[str, Any],
) -> ResponseWithStatus:
    try:
        return {"ok": True, **exchange_mercadolibre_code_from_body(body)}, 200
    except Exception as exc:
        message = str(exc)
        code = mercadolibre_test_error_code(message)
        append_ml_auth_test_log(
            "exchange_code",
            "failed",
            {
                "redirect_uri": body.get("redirect_uri") or "",
                "code_present": bool(body.get("code_or_url") or body.get("code")),
            },
            {"ok": False, "error_code": code, "error_message": message},
            code,
            message,
            auth_next_action("mercadolibre", "测试失败", code, message),
        )
        return _mercadolibre_error_payload(message), 400


def refresh_mercadolibre_token_payload(
    body: dict[str, Any],
) -> ResponseWithStatus:
    try:
        return {"ok": True, **refresh_mercadolibre_token_from_body(body)}, 200
    except Exception as exc:
        return _mercadolibre_error_payload(str(exc)), 400


def run_mercadolibre_auth_test_payload(
    body: dict[str, Any],
) -> ResponseWithStatus:
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    result = run_mercadolibre_07d_test(
        str(body.get("mode") or "auth_link"),
        product,
        str(body.get("category_id") or ""),
    )
    return result, 200


def test_store_auth_payload(body: dict[str, Any]) -> ResponseWithStatus:
    platform = str(body.get("platform") or "").strip().lower()
    try:
        result = test_store_auth(platform, str(body.get("scope") or ""))
        return result, 200
    except Exception as exc:
        message = str(exc)
        if platform == "mercadolibre":
            return _mercadolibre_error_payload(message), 400
        return {"ok": False, "error": message}, 400


def test_api_config_payload(body: dict[str, Any]) -> ResponseWithStatus:
    kind = str(body.get("kind") or "").strip().lower()
    try:
        result = test_api_config(
            kind,
            body.get("config") if isinstance(body.get("config"), dict) else {},
            str(body.get("test_value") or ""),
        )
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {
            "ok": False,
            "channel": kind,
            "error": str(exc),
            "next_action": "请检查当前卡片里的配置后再试一次。",
        }, 400


def save_settings_payload(body: dict[str, Any]) -> ResponseWithStatus:
    app_dir = _app_dir()
    incoming_app = body.get("appConfig")
    if isinstance(incoming_app, dict) and incoming_app:
        save_app_config(
            merge_app_config_fields(load_app_config(), incoming_app)
        )
    incoming_store = body.get("storeConfig")
    if isinstance(incoming_store, dict) and incoming_store:
        save_store_config(
            merge_store_config_fields(load_store_config(), incoming_store)
        )
    store_config = load_store_config()
    return {
        "ok": True,
        "appConfig": config_service.public_app_config(app_dir, load_app_config()),
        "storeConfig": config_service.public_store_config(store_config),
        "storeAuthSummary": summarize_store_auth_states(store_config),
    }, 200


def clear_store_auth_payload(body: dict[str, Any]) -> ResponseWithStatus:
    app_dir = _app_dir()
    store_config = clear_store_auth(str(body.get("platform") or ""))
    return {
        "ok": True,
        "appConfig": config_service.public_app_config(app_dir, load_app_config()),
        "storeConfig": config_service.public_store_config(store_config),
        "storeAuthSummary": summarize_store_auth_states(store_config),
    }, 200


__all__ = [
    "append_ml_auth_test_log",
    "auth_next_action",
    "build_mercadolibre_auth_link",
    "clear_store_auth",
    "clear_store_auth_payload",
    "exchange_mercadolibre_code_from_body",
    "explain_mercadolibre_auth_error",
    "load_app_config",
    "load_required_product_from_body",
    "load_store_config",
    "mercadolibre_auth_checklist",
    "mercadolibre_test_error_code",
    "merge_app_config_fields",
    "merge_store_config_fields",
    "open_auth_link_in_browser",
    "refresh_mercadolibre_token_from_body",
    "run_mercadolibre_07d_test",
    "save_app_config",
    "save_store_config",
    "summarize_store_auth_states",
    "test_api_config",
    "test_store_auth",
    "exchange_mercadolibre_code_payload",
    "mercadolibre_auth_checklist_payload",
    "mercadolibre_auth_link_payload",
    "open_auth_link_payload",
    "refresh_mercadolibre_token_payload",
    "run_mercadolibre_auth_test_payload",
    "save_ai_config_payload",
    "save_settings_payload",
    "test_api_config_payload",
    "test_store_auth_payload",
]
