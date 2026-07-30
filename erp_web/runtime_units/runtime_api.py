# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from erp_web.context import get_context
from erp_web.http_request import safe_json_body
from erp_web.stores.product_store import normalize_product_fields

from erp_web import listing_planner as generator
from .category_store import write_json
from .collect_helpers import collect_time_iso
from .copy_generation import list_presets, platform_to_preset_key
from .publish_bus import append_publish_log
from .publish_helpers import (
    _draft_for_platform,
    _field_error_map,
    precheck_item,
)
from .publish_adapter import publishing_adapter_for, unsupported_publish_response
from .publish_logs_runtime import (
    _draft_id_for_log,
    _product_id_for_log,
    _write_publish_artifacts,
)
from .publish_validation import apply_precheck_to_product
from .image_pool_core import source_image_refs


def _remote_publish_succeeded(result: Any) -> bool:
    return bool(
        isinstance(result, dict)
        and (
            result.get("id")
            or result.get("item_id")
            or result.get("external_id")
            or (
                result.get("ok") is True
                and str(result.get("status") or "").strip().lower()
                in {"published", "success", "real_publish_success"}
            )
            or result.get("success") is True
        )
    )


def publish_product(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    platform = str(platform or "").strip().lower()
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        return unsupported_publish_response(platform)
    product = adapter.resolve_category(product, config)
    precheck = adapter.validate_draft(product, config)
    if not precheck.get("ok"):
        updated = apply_precheck_to_product(product, platform, precheck, status="not_ready")
        payload_path, response_path = _write_publish_artifacts(platform, {"precheck": precheck}, {"ok": False, "status": "not_ready"})
        log_entry = {
            "product_id": _product_id_for_log(updated, platform),
            "platform": platform,
            "draft_id": _draft_id_for_log(updated, platform),
            "status": "not_ready",
            "started_at": precheck.get("checked_at") or collect_time_iso(),
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": (precheck.get("errors") or [{}])[0].get("code", ""),
            "error_message": "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)),
            "field_errors": _field_error_map(list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])),
            "next_action": (precheck.get("errors") or [{}])[0].get("next_action", ""),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": config.get("listing", {}).get("sku", ""),
            "error": "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)),
            "image": source_image_refs(updated)[:1],
        }
        append_publish_log(log_entry)
        saved = get_context().products.save_product(updated)
        return {
            "ok": False,
            "status": "not_ready",
            "error": log_entry["error_message"] or "发布前预检未通过",
            "precheck": precheck,
            "error_map": {"summary": log_entry["error_message"] or "发布前预检未通过", "field_errors": log_entry["field_errors"]},
            "product": saved,
        }

    product = apply_precheck_to_product(product, platform, precheck, status="local_precheck_passed")
    payload = adapter.build_payload(product, config)
    errors = adapter.validate_payload(payload, config)
    if errors:
        updated = apply_precheck_to_product(
            product,
            platform,
            {
                "platform": platform,
                "ok": False,
                "errors": [precheck_item("PAYLOAD_INVALID", "payload", message, "error", "前往对应页面补齐字段") for message in errors],
                "warnings": [],
                "checked_at": collect_time_iso(),
            },
            status="not_ready",
        )
        payload_path, response_path = _write_publish_artifacts(platform, payload, {"ok": False, "errors": errors})
        append_publish_log(
            {
                "product_id": _product_id_for_log(updated, platform),
                "platform": platform,
                "draft_id": _draft_id_for_log(updated, platform),
                "status": "not_ready",
                "started_at": collect_time_iso(),
                "finished_at": collect_time_iso(),
                "request_payload_path": payload_path,
                "response_body_path": response_path,
                "error_code": "PAYLOAD_INVALID",
                "error_message": "，".join(errors),
                "field_errors": {"payload": errors},
                "next_action": "前往对应页面补齐字段",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "shop": platform,
                "sku": config.get("listing", {}).get("sku", ""),
                "error": "，".join(errors),
                "image": source_image_refs(updated)[:1],
            }
        )
        saved = get_context().products.save_product(updated)
        return {"ok": False, "status": "not_ready", "error": "，".join(errors), "payload": payload, "product": saved}

    draft = _draft_for_platform(product, platform)
    started_at = collect_time_iso()
    try:
        result: Any = adapter.publish_payload(payload, config)
        status = (
            "real_publish_success"
            if _remote_publish_succeeded(result)
            else "real_publish_failed"
        )
    except Exception as exc:
        mapped = adapter.map_publish_error(exc)
        payload_path, response_path = _write_publish_artifacts(platform, payload, mapped)
        updated = apply_precheck_to_product(
            product,
            platform,
            {
                "platform": platform,
                "ok": False,
                "errors": [
                    precheck_item("REAL_PUBLISH_FAILED", field, str(values[0] if isinstance(values, list) and values else mapped["summary"]), "error", "前往对应字段修复后重试")
                    for field, values in mapped["field_errors"].items()
                ] or [precheck_item("REAL_PUBLISH_FAILED", "publish", mapped["summary"], "error", "查看字段映射并重试")],
                "warnings": [],
                "checked_at": collect_time_iso(),
            },
            status="real_publish_failed",
        )
        append_publish_log(
            {
                "product_id": _product_id_for_log(updated, platform),
                "platform": platform,
                "draft_id": _draft_id_for_log(updated, platform),
                "status": "real_publish_failed",
                "started_at": started_at,
                "finished_at": collect_time_iso(),
                "request_payload_path": payload_path,
                "response_body_path": response_path,
                "error_code": str(mapped.get("error_code") or "REAL_PUBLISH_FAILED"),
                "error_message": mapped["summary"],
                "field_errors": mapped["field_errors"],
                "next_action": "按字段提示修复后重试",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "shop": platform,
                "sku": config.get("listing", {}).get("sku", ""),
                "error": mapped["summary"],
                "image": source_image_refs(updated)[:1],
            }
        )
        saved = get_context().products.save_product(updated)
        return {"ok": False, "status": "real_publish_failed", "error": mapped["summary"], "error_map": mapped, "payload": payload, "product": saved}

    ok = _remote_publish_succeeded(result)
    final_status = "real_publish_success" if ok else status
    payload_path, response_path = _write_publish_artifacts(platform, payload, result)
    updated = apply_precheck_to_product(product, platform, precheck, status=final_status if ok else status)
    append_publish_log(
        {
            "product_id": _product_id_for_log(updated, platform),
            "platform": platform,
            "draft_id": _draft_id_for_log(updated, platform),
            "status": final_status if ok else status,
            "started_at": started_at,
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": "" if ok else str(result.get("error_code") or result.get("status") or ""),
            "error_message": "" if ok else str(result.get("error") or result.get("message") or json.dumps(result, ensure_ascii=False)),
            "field_errors": _field_error_map(updated["drafts"][platform].get("validation_errors") or []),
            "next_action": "" if ok else "查看 payload 与日志，再决定是否真实发布",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": config.get("listing", {}).get("sku", ""),
            "error": "" if ok else str(result.get("error") or result.get("message") or json.dumps(result, ensure_ascii=False)),
            "image": source_image_refs(updated)[:1],
        }
    )
    saved = get_context().products.save_product(updated)
    return {"ok": ok, "status": final_status if ok else status, "result": result, "payload": payload, "product": saved, "precheck": precheck}


def save_task_bundle(product: dict[str, Any], platform: str, count: int) -> dict[str, Any]:
    task_dir = get_context().paths.task_dir
    task_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = task_dir / stamp
    folder.mkdir(parents=True, exist_ok=True)
    image_paths = [
        Path(path)
        for path in source_image_refs(product)
        if Path(path).exists()
    ][:5]
    prompt = generator.build_plan(product, [generator.PlatformPlan(key=platform_to_preset_key(platform), preset=list_presets()[platform_to_preset_key(platform)])])
    prompt_text = json.dumps(prompt, ensure_ascii=False, indent=2)
    prompt_file = folder / "task_prompt.json"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    metadata = {
        "productName": product.get("name", ""),
        "platform": platform,
        "count": count,
        "sourceCount": len(image_paths),
        "prompt": str(prompt_file),
    }
    write_json(folder / "metadata.json", metadata)
    return {"folder": str(folder), "prompt": str(prompt_file), "metadata": metadata}


def html_page(active_page: str = "workbench") -> str:
    paths = get_context().paths
    if paths.front_dist_index_path.exists():
        template = paths.front_dist_index_path.read_text(encoding="utf-8")
    elif paths.web_template_path.exists():
        template = paths.web_template_path.read_text(encoding="utf-8")
    else:
        # Historical fallback referenced an HTML_TEMPLATE constant that never
        # existed anywhere, i.e. this branch was a guaranteed NameError. Fail
        # loudly with an actionable message instead.
        raise FileNotFoundError(
            "No frontend template found: build the frontend (expected "
            f"{paths.front_dist_index_path}) or provide {paths.web_template_path}."
        )
    return template.replace("__ACTIVE_PAGE__", active_page)


__all__ = [
    "html_page",
    "publish_product",
    "safe_json_body",
    "save_task_bundle",
]
