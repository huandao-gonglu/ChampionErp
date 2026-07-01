# -*- coding: utf-8 -*-
from __future__ import annotations


from typing import Callable

from erp_web.services import config_service

from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime_units.auth_runtime import (
    build_mercadolibre_auth_link,
    exchange_mercadolibre_code_from_body,
    mercadolibre_auth_checklist,
    refresh_mercadolibre_token_from_body,
    test_api_config,
    test_store_auth,
)
from ..runtime_units.browser_debug import open_auth_link_in_browser
from ..runtime_units.product_store import (
    explain_mercadolibre_auth_error,
    load_app_config,
    load_product,
    load_store_config,
    merge_store_config_fields,
    normalize_product_fields,
    save_app_config,
    save_store_config,
    summarize_store_auth_states,
)
from ..runtime_units.publish_logs_runtime import append_ml_auth_test_log
from ..runtime_units.publish_mercadolibre import run_mercadolibre_07d_test
from ..runtime_units.runtime_common import APP_DIR


PostHandler = Callable[[JsonRequestHandler], None]

def handle_ai_config_save(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    config_service.write_env_template(APP_DIR)
    app_cfg = config_service.merge_ai_config(APP_DIR, load_app_config(), body.get("config") if isinstance(body.get("config"), dict) else body)
    save_app_config(app_cfg)
    config_service.save_config_snapshot(APP_DIR, app_cfg)
    handler.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})
    return


def handle_mercadolibre_auth_link(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        result = build_mercadolibre_auth_link(str(body.get("app_id") or ""), str(body.get("redirect_uri") or ""))
        handler.send_json({"ok": True, **result})
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return


def handle_mercadolibre_auth_checklist(handler: JsonRequestHandler) -> None:
    handler.send_json({"ok": True, "checklist": mercadolibre_auth_checklist()})
    return


def handle_open_auth_link(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        result = open_auth_link_in_browser(str(body.get("url") or ""), str(body.get("browser") or "default"))
        status = 200 if result.get("ok") else 400
        handler.send_json(result, status)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return


def handle_mercadolibre_exchange_code(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        result = exchange_mercadolibre_code_from_body(body)
        handler.send_json({"ok": True, **result})
    except Exception as exc:
        message = str(exc)
        code = app._mercadolibre_test_error_code(message)
        append_ml_auth_test_log(
            "exchange_code",
            "failed",
            {"redirect_uri": body.get("redirect_uri") or "", "code_present": bool(body.get("code_or_url") or body.get("code"))},
            {"ok": False, "error_code": code, "error_message": message},
            code,
            message,
            app._auth_next_action("mercadolibre", "测试失败", code, message),
        )
        explanation = explain_mercadolibre_auth_error(code, message)
        handler.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
    return


def handle_mercadolibre_refresh_token(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        result = refresh_mercadolibre_token_from_body(body)
        handler.send_json({"ok": True, **result})
    except Exception as exc:
        message = str(exc)
        code = app._mercadolibre_test_error_code(message)
        explanation = explain_mercadolibre_auth_error(code, message)
        handler.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
    return


def handle_mercadolibre_real_auth_test(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    product = normalize_product_fields(body.get("product") or load_product())
    result = run_mercadolibre_07d_test(str(body.get("mode") or "auth_link"), product, str(body.get("category_id") or ""))
    handler.send_json(result)
    return


def handle_test_store_auth(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        handler.send_json(test_store_auth(str(body.get("platform") or ""), str(body.get("scope") or "")))
    except Exception as exc:
        platform = str(body.get("platform") or "").strip().lower()
        message = str(exc)
        if platform == "mercadolibre":
            code = app._mercadolibre_test_error_code(message)
            explanation = explain_mercadolibre_auth_error(code, message)
            handler.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
        else:
            handler.send_json({"ok": False, "error": message}, 400)
    return


def handle_test_api_config(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        result = test_api_config(
            str(body.get("kind") or ""),
            body.get("config") if isinstance(body.get("config"), dict) else {},
            str(body.get("test_value") or ""),
        )
        handler.send_json(result, 200 if result.get("ok") else 400)
    except Exception as exc:
        kind = str(body.get("kind") or "").strip().lower()
        handler.send_json(
            {
                "ok": False,
                "channel": kind,
                "error": str(exc),
                "next_action": "请检查当前卡片里的配置后再试一次。",
            },
            400,
        )
    return


def handle_save_settings(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    if body.get("appConfig"):
        app_cfg = load_app_config()
        app_cfg.update(body["appConfig"])
        save_app_config(app_cfg)
    if body.get("storeConfig"):
        store_cfg = load_store_config()
        for key, value in body["storeConfig"].items():
            if isinstance(value, dict):
                if key == "mercadolibre":
                    if value.get("client_secret") and not value.get("app_secret"):
                        value["app_secret"] = value.get("client_secret")
                    if value.get("app_secret") and not value.get("client_secret"):
                        value["client_secret"] = value.get("app_secret")
                store_cfg = merge_store_config_fields(store_cfg, {key: value})
        save_store_config(store_cfg)
    store_cfg = load_store_config()
    handler.send_json({"ok": True, "appConfig": load_app_config(), "storeConfig": store_cfg, "storeAuthSummary": summarize_store_auth_states(store_cfg)})
    return


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/ai-config/save": handle_ai_config_save,
    "/api/mercadolibre/auth-link": handle_mercadolibre_auth_link,
    "/api/mercadolibre/auth-checklist": handle_mercadolibre_auth_checklist,
    "/api/open-auth-link": handle_open_auth_link,
    "/api/mercadolibre/exchange-code": handle_mercadolibre_exchange_code,
    "/api/mercadolibre/refresh-token": handle_mercadolibre_refresh_token,
    "/api/mercadolibre/real-auth-test": handle_mercadolibre_real_auth_test,
    "/api/test-store-auth": handle_test_store_auth,
    "/api/test-api-config": handle_test_api_config,
    "/api/save-settings": handle_save_settings,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
