# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from erp_web.context import get_context
from erp_web.product_model.common import normalize_list
from erp_web.services.config_service import is_sensitive_config_key
from erp_web.stores.config_store import store_auth_failure_code
from erp_web.stores.product_store import mask_secret

from .collect_helpers import collect_time_iso
from .json_store import write_json
from .publish_bus import append_publish_log
from .publish_helpers import _draft_for_platform

def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_config_key(key):
                sanitized[key] = mask_secret(item)
            else:
                sanitized[key] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    return value


def _publish_artifact_paths(
    platform: str,
    *,
    artifact_key: str = "",
) -> tuple[Path, Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_platform = "".join(
        char
        for char in str(platform or "").strip().lower()
        if char.isalnum() or char in "._-"
    ) or "unknown"
    raw_key = str(artifact_key or "").strip()
    if raw_key:
        safe_key = "".join(
            char
            for char in raw_key
            if char.isalnum() or char in "._-"
        ).strip("._-")[:64] or "artifact"
        digest = hashlib.sha256(
            raw_key.encode("utf-8")
        ).hexdigest()[:12]
        token = f"{safe_platform}-{safe_key}-{digest}"
    else:
        token = (
            f"{stamp}-{safe_platform}-{os.getpid()}-"
            f"{time.time_ns()}-{uuid.uuid4().hex[:12]}"
        )
    artifact_dir = get_context().paths.output_dir / "publish_artifacts"
    payload_path = artifact_dir / f"{token}-payload.json"
    response_path = artifact_dir / f"{token}-response.json"
    return payload_path, response_path


def _write_publish_artifacts(
    platform: str,
    payload: Any,
    response: Any,
    *,
    output_dir: Path | None = None,
    artifact_key: str = "",
) -> tuple[str, str]:
    payload_path, response_path = _publish_artifact_paths(
        platform,
        artifact_key=artifact_key,
    )
    if output_dir is not None:
        artifact_dir = Path(output_dir) / "publish_artifacts"
        payload_path = artifact_dir / payload_path.name
        response_path = artifact_dir / response_path.name
    payload_path.parent.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )
    if os.name != "nt":
        payload_path.parent.chmod(0o700)
    write_json(payload_path, _sanitize_for_log(payload))
    write_json(response_path, _sanitize_for_log(response))
    if os.name != "nt":
        payload_path.parent.chmod(0o700)
        payload_path.chmod(0o600)
        response_path.chmod(0o600)
    return str(payload_path), str(response_path)


def _product_id_for_log(product: dict[str, Any], platform: str) -> str:
    return str(product.get("product_id") or "").strip()


def _draft_id_for_log(
    product: dict[str, Any],
    platform: str,
) -> str:
    return str(
        _draft_for_platform(product, platform).get("draft_id") or ""
    ).strip()


def _first_product_image(product: dict[str, Any]) -> list[str]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs = [
        str(item.get("url") or item.get("path") or "").strip()
        for item in pool
        if isinstance(item, dict)
        and str(item.get("url") or item.get("path") or "").strip()
    ]
    return (refs or normalize_list(source.get("images")))[:1]


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
    return append_platform_publish_log(
        product,
        "mercadolibre",
        status,
        started_at,
        payload,
        response,
        error_code,
        error_message,
        field_errors,
        next_action,
    )


def append_platform_publish_log(
    product: dict[str, Any],
    platform: str,
    status: str,
    started_at: str,
    payload: Any,
    response: Any,
    error_code: str = "",
    error_message: str = "",
    field_errors: dict[str, Any] | None = None,
    next_action: str = "",
) -> tuple[str, str]:
    """记录平台发布阶段；平台名由发布适配器注册项传入。"""

    platform = str(platform or "").strip().lower()
    payload_path, response_path = _write_publish_artifacts(platform, payload, response)
    draft = _draft_for_platform(product, platform)
    append_publish_log(
        {
            "product_id": _product_id_for_log(product, platform),
            "platform": platform,
            "draft_id": _draft_id_for_log(product, platform),
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
            "shop": platform,
            "sku": str(draft.get("sku") or ""),
            "error": error_message,
            "image": _first_product_image(product),
        }
    )
    return payload_path, response_path


def mercadolibre_test_error_code(message: str) -> str:
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
    return str(draft.get("category_id") or "").strip()


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
    "_draft_id_for_log",
    "_product_id_for_log",
    "_sanitize_for_log",
    "append_ml_auth_test_log",
    "append_ml_publish_log",
    "append_platform_publish_log",
    "mercadolibre_test_error_code",
]
