from __future__ import annotations

import urllib.parse
from typing import Any

from services import config_service, product_research_service

from erp_web.product_research_config import normalize_product_research_config
from erp_web.runtime_units.product_store import load_app_config, save_app_config
from erp_web.runtime_units.runtime_common import APP_DIR
from erp_web.schemas.api import ApiResponse


ResponseWithStatus = tuple[ApiResponse, int]
SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def _mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _restore_masked_config_value(key: str, value: Any, current_value: Any) -> Any:
    if isinstance(value, dict):
        current_dict = current_value if isinstance(current_value, dict) else {}
        return {
            nested_key: _restore_masked_config_value(nested_key, nested_value, current_dict.get(nested_key))
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return value
    if key.lower() not in SENSITIVE_CONFIG_KEYS:
        return value
    if current_value and str(value or "") == _mask_secret(current_value):
        return current_value
    return value


def _restore_masked_provider_secrets(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    providers = incoming.get("search_providers")
    if not isinstance(providers, list):
        return incoming
    current_rows = current.get("search_providers") if isinstance(current.get("search_providers"), list) else current.get("source_registry")
    current_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(current_rows, list):
        for row in current_rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("id") or row.get("source_id") or "").strip()
            if row_id:
                current_by_id[row_id] = row
    for provider in providers:
        if not isinstance(provider, dict) or not isinstance(provider.get("config_json"), dict):
            continue
        current_provider = current_by_id.get(str(provider.get("id") or provider.get("source_id") or "").strip())
        current_config = current_provider.get("config_json") if isinstance(current_provider, dict) and isinstance(current_provider.get("config_json"), dict) else {}
        for key, value in list(provider["config_json"].items()):
            provider["config_json"][key] = _restore_masked_config_value(key, value, current_config.get(key))
    return incoming


def create_search_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        app_config = load_app_config()
        task = product_research_service.create_search_task(APP_DIR, body, app_config.get("product_research", {}), app_config)
        return product_research_service.build_task_response(task), 200
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


def get_search_task_payload(parsed: urllib.parse.ParseResult) -> ResponseWithStatus:
    params = urllib.parse.parse_qs(parsed.query)
    task_id = str((params.get("task_id") or [""])[0] or "").strip()
    if task_id:
        task = product_research_service.load_search_task(APP_DIR, task_id)
        if not task:
            return {"ok": False, "error": "task_id not found"}, 404
        return product_research_service.build_task_response(task), 200
    try:
        limit = int((params.get("limit") or ["20"])[0] or 20)
    except ValueError:
        return {"ok": False, "error": "limit must be an integer"}, 400
    return {"ok": True, "items": product_research_service.list_search_tasks(APP_DIR, limit=limit)}, 200


def get_source_registry_payload() -> ResponseWithStatus:
    config = load_app_config().get("product_research", {})
    public_config = product_research_service.public_product_research_config(config)
    return {
        "ok": True,
        "config": public_config,
        "source_registry": public_config.get("source_registry", []),
    }, 200


def save_source_registry_payload(body: dict[str, Any]) -> ResponseWithStatus:
    incoming = body.get("config") if isinstance(body.get("config"), dict) else body
    app_config = load_app_config()
    current = app_config.get("product_research") if isinstance(app_config.get("product_research"), dict) else {}
    incoming = _restore_masked_provider_secrets(dict(incoming), current)
    next_config = dict(current)
    for key in (
        "search_defaults",
        "provider_runtime",
        "reference_market_map",
        "market_languages",
        "china_element_catalog",
        "upgrade_type_catalog",
        "scoring_weights",
        "search_providers",
        "target_markets",
        "source_registry",
    ):
        if key in incoming:
            next_config[key] = incoming[key]
    app_config["product_research"] = normalize_product_research_config(next_config)
    save_app_config(app_config)
    config_service.save_config_snapshot(APP_DIR, app_config)
    public_config = product_research_service.public_product_research_config(load_app_config().get("product_research", {}))
    return {
        "ok": True,
        "config": public_config,
        "source_registry": public_config.get("source_registry", []),
    }, 200


def test_search_provider_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        app_config = load_app_config()
        current = app_config.get("product_research") if isinstance(app_config.get("product_research"), dict) else {}
        provider = body.get("provider") if isinstance(body.get("provider"), dict) else {}
        restored = _restore_masked_provider_secrets({"search_providers": [provider]}, current)
        test_body = dict(body)
        test_body["provider"] = (restored.get("search_providers") or [{}])[0]
        result = product_research_service.test_search_provider_connection(test_body, current, APP_DIR, app_config)
        return result, 200
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


def complete_provider_config_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        app_config = load_app_config()
        current = app_config.get("product_research") if isinstance(app_config.get("product_research"), dict) else {}
        provider = body.get("provider") if isinstance(body.get("provider"), dict) else {}
        restored = _restore_masked_provider_secrets({"search_providers": [provider]}, current)
        model_id = str(body.get("model_id") or body.get("ai_model_id") or "").strip()
        suggestion = product_research_service.complete_provider_config_with_ai(
            (restored.get("search_providers") or [{}])[0],
            APP_DIR,
            app_config,
            model_id=model_id,
        )
        return {"ok": True, "suggestion": suggestion}, 200
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


__all__ = [
    "complete_provider_config_payload",
    "create_search_task_payload",
    "get_search_task_payload",
    "get_source_registry_payload",
    "save_source_registry_payload",
    "test_search_provider_payload",
]
