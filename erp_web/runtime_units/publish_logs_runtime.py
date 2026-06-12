# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

from .publish_helpers import *
from .publish_validation import *

def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ("token", "secret", "api_key", "apikey", "authorization")):
                sanitized[key] = mask_secret(item)
            else:
                sanitized[key] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    return value


def _publish_artifact_paths(platform: str) -> tuple[Path, Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    token = f"{stamp}-{platform}-{os.getpid()}"
    payload_path = OUTPUT_DIR / "publish_artifacts" / f"{token}-payload.json"
    response_path = OUTPUT_DIR / "publish_artifacts" / f"{token}-response.json"
    return payload_path, response_path


def _write_publish_artifacts(platform: str, payload: Any, response: Any) -> tuple[str, str]:
    payload_path, response_path = _publish_artifact_paths(platform)
    write_json(payload_path, _sanitize_for_log(payload))
    write_json(response_path, _sanitize_for_log(response))
    return str(payload_path), str(response_path)


def _product_id_for_log(product: dict[str, Any], platform: str) -> str:
    draft = _draft_for_platform(product, platform)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    return str(source.get("source_url") or product.get("source_url") or draft.get("sku") or product.get("sku") or product.get("name") or "").strip()


def append_ml_publish_log(
    product: dict[str, Any],
    status: str,
    started_at: str,
    payload: Any,
    response: Any,
    error_code: str = "",
    error_message: str = "",
    field_errors: dict[str, Any] | None = None,
    next_action: str = "",
) -> tuple[str, str]:
    payload_path, response_path = _write_publish_artifacts("mercadolibre", payload, response)
    draft = _draft_for_platform(product, "mercadolibre")
    append_publish_log(
        {
            "product_id": _product_id_for_log(product, "mercadolibre"),
            "platform": "mercadolibre",
            "draft_id": str(draft.get("sku") or ""),
            "status": status,
            "started_at": started_at,
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": error_code,
            "error_message": error_message,
            "field_errors": field_errors or {},
            "next_action": next_action,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": "mercadolibre",
            "sku": str(draft.get("sku") or ""),
            "error": error_message,
            "image": normalize_list(product.get("source_image_urls"))[:1],
        }
    )
    return payload_path, response_path


def _mercadolibre_test_error_code(message: str) -> str:
    text = str(message or "").lower()
    if "ssl" in text or "unexpected_eof" in text or "eof occurred" in text:
        return "network_tls_failed"
    if "winerror 10013" in text or "urlopen error" in text and "socket" in text:
        return "NETWORK_BLOCKED"
    if "timed out" in text or "timeout" in text:
        return "NETWORK_TIMEOUT"
    if "invalid access token" in text or "invalid_token" in text:
        return "INVALID_ACCESS_TOKEN"
    if "expired" in text and "token" in text:
        return "TOKEN_EXPIRED"
    if "invalid_grant" in text:
        return "INVALID_GRANT"
    if "real_category_required" in text or "mock/seed" in text or "测试类目" in text or "category_id 为空" in text:
        return "REAL_CATEGORY_REQUIRED"
    if "403" in text or "permission" in text or "forbidden" in text:
        return "PERMISSION_DENIED"
    return store_auth_failure_code("mercadolibre", message).upper()


def append_ml_auth_test_log(
    test_type: str,
    status: str,
    request_payload: Any | None = None,
    response_body: Any | None = None,
    error_code: str = "",
    error_message: str = "",
    next_action: str = "",
) -> tuple[str, str]:
    payload_path, response_path = _write_publish_artifacts(
        "mercadolibre-07d",
        request_payload or {"test_type": test_type},
        response_body or {},
    )
    append_publish_log(
        {
            "platform": "mercadolibre",
            "test_type": test_type,
            "status": status,
            "checked_at": collect_time_iso(),
            "started_at": collect_time_iso(),
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": error_code,
            "error_message": error_message,
            "field_errors": {},
            "next_action": next_action,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": "mercadolibre",
        }
    )
    return payload_path, response_path


def _mercadolibre_category_id_from_product(product: dict[str, Any]) -> str:
    draft = _draft_for_platform(product, "mercadolibre")
    return str(draft.get("category_id") or product.get("category_id") or "").strip()


def _is_mock_mercadolibre_category_id(category_id: str) -> bool:
    value = str(category_id or "").strip().lower()
    return value in {"mock", "mock_test", "seed_test"} or value.startswith("mock_") or value.startswith("seed_")


def _mercadolibre_required_attr_ids(attrs: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for attr in attrs if isinstance(attrs, list) else []:
        if isinstance(attr, dict) and attr.get("required"):
            attr_id = str(attr.get("id") or "").strip()
            if attr_id:
                ids.append(attr_id)
    return ids


__all__ = [
    "_is_mock_mercadolibre_category_id",
    "_mercadolibre_category_id_from_product",
    "_mercadolibre_required_attr_ids",
    "_mercadolibre_test_error_code",
    "_sanitize_for_log",
    "append_ml_auth_test_log",
    "append_ml_publish_log",
]
