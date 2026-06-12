# -*- coding: utf-8 -*-
from __future__ import annotations


from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == "/api/ai-config/save":
        body = handler.read_body()
        config_service.write_env_template(APP_DIR)
        app_cfg = config_service.merge_ai_config(APP_DIR, load_app_config(), body.get("config") if isinstance(body.get("config"), dict) else body)
        save_app_config(app_cfg)
        config_service.save_config_snapshot(APP_DIR, app_cfg)
        handler.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})
        return True
    if parsed.path == "/api/mercadolibre/auth-link":
        body = handler.read_body()
        try:
            result = build_mercadolibre_auth_link(str(body.get("app_id") or ""), str(body.get("redirect_uri") or ""))
            handler.send_json({"ok": True, **result})
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/mercadolibre/auth-checklist":
        handler.send_json({"ok": True, "checklist": mercadolibre_auth_checklist()})
        return True
    if parsed.path == "/api/open-auth-link":
        body = handler.read_body()
        try:
            result = open_auth_link_in_browser(str(body.get("url") or ""), str(body.get("browser") or "default"))
            status = 200 if result.get("ok") else 400
            handler.send_json(result, status)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/mercadolibre/exchange-code":
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
        return True
    if parsed.path == "/api/mercadolibre/refresh-token":
        body = handler.read_body()
        try:
            result = refresh_mercadolibre_token_from_body(body)
            handler.send_json({"ok": True, **result})
        except Exception as exc:
            message = str(exc)
            code = app._mercadolibre_test_error_code(message)
            explanation = explain_mercadolibre_auth_error(code, message)
            handler.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
        return True
    if parsed.path == "/api/mercadolibre/real-auth-test":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        result = run_mercadolibre_07d_test(str(body.get("mode") or "auth_link"), product, str(body.get("category_id") or ""))
        handler.send_json(result)
        return True
    if parsed.path == "/api/test-store-auth":
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
        return True
    if parsed.path == "/api/save-settings":
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
        return True
    return False
