# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

def publish_product(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    platform = str(platform or "").strip().lower()
    precheck = validate_platform_draft(product, platform, config)
    if not precheck.get("ok"):
        updated = apply_precheck_to_product(product, platform, precheck, status="not_ready")
        payload_path, response_path = _write_publish_artifacts(platform, {"precheck": precheck}, {"ok": False, "status": "not_ready"})
        log_entry = {
            "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
            "platform": platform,
            "draft_id": str(_draft_for_platform(updated, platform).get("sku") or ""),
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
            "image": normalize_list(updated.get("source_image_urls"))[:1],
        }
        append_publish_log(log_entry)
        saved = save_product(updated)
        return {
            "ok": False,
            "status": "not_ready",
            "error": log_entry["error_message"] or "发布前预检未通过",
            "precheck": precheck,
            "error_map": {"summary": log_entry["error_message"] or "发布前预检未通过", "field_errors": log_entry["field_errors"]},
            "product": saved,
        }

    product = apply_precheck_to_product(product, platform, precheck, status="local_precheck_passed")
    payload = build_publish_payload(product, platform, config)
    errors = validate_publish_payload(platform, payload, config)
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
                "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
                "platform": platform,
                "draft_id": str(_draft_for_platform(updated, platform).get("sku") or ""),
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
                "image": normalize_list(updated.get("source_image_urls"))[:1],
            }
        )
        saved = save_product(updated)
        return {"ok": False, "status": "not_ready", "error": "，".join(errors), "payload": payload, "product": saved}

    draft = _draft_for_platform(product, platform)
    started_at = collect_time_iso()
    result: Any
    status = "publishing"
    if platform == "mercadolibre":
        try:
            result = publisher.publish_mercadolibre(payload, config["mercadolibre"].get("access_token", ""))
            status = "real_publish_success" if isinstance(result, dict) and (result.get("id") or result.get("ok") or result.get("success")) else "real_publish_failed"
        except Exception as exc:
            parsed = publisher.parse_mercadolibre_error(exc)
            mapped = map_mercadolibre_publish_error(parsed)
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
                    "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
                    "platform": platform,
                    "draft_id": str(draft.get("sku") or ""),
                    "status": "real_publish_failed",
                    "started_at": started_at,
                    "finished_at": collect_time_iso(),
                    "request_payload_path": payload_path,
                    "response_body_path": response_path,
                    "error_code": str(parsed.get("error") or "REAL_PUBLISH_FAILED"),
                    "error_message": mapped["summary"],
                    "field_errors": mapped["field_errors"],
                    "next_action": "按字段提示修复后重试",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "shop": platform,
                    "sku": config.get("listing", {}).get("sku", ""),
                    "error": mapped["summary"],
                    "image": normalize_list(updated.get("source_image_urls"))[:1],
                }
            )
            saved = save_product(updated)
            return {"ok": False, "status": "real_publish_failed", "error": mapped["summary"], "error_map": mapped, "payload": payload, "product": saved}
    elif platform == "wildberries":
        result = {"ok": False, "status": "ready_for_real_publish", "message": "Wildberries 真实发布前，建议先确认授权与类目接口。当前保留本地预检与 payload。"}
        status = "ready_for_real_publish"
    else:
        result = {"ok": False, "status": "ready_for_real_publish", "message": "Ozon 真实发布接口仍需真实授权验证。当前保留本地预检与 payload。"}
        status = "ready_for_real_publish"

    ok = bool(result.get("id") or result.get("ok") or result.get("success")) if isinstance(result, dict) else True
    final_status = "real_publish_success" if ok and platform == "mercadolibre" else status if not ok else "mock_success"
    payload_path, response_path = _write_publish_artifacts(platform, payload, result)
    updated = apply_precheck_to_product(product, platform, precheck, status=final_status if ok else status)
    append_publish_log(
        {
            "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
            "platform": platform,
            "draft_id": str(draft.get("sku") or ""),
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
            "image": normalize_list(updated.get("source_image_urls"))[:1],
        }
    )
    saved = save_product(updated)
    return {"ok": ok, "status": final_status if ok else status, "result": result, "payload": payload, "product": saved, "precheck": precheck}


def save_task_bundle(product: dict[str, Any], platform: str, count: int) -> dict[str, Any]:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = TASK_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    image_paths = [Path(path) for path in normalize_list(product.get("source_images")) if Path(path).exists()][:5]
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


def safe_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def html_page(active_page: str = "workbench") -> str:
    if FRONT_DIST_INDEX_PATH.exists():
        template = FRONT_DIST_INDEX_PATH.read_text(encoding="utf-8")
    elif WEB_TEMPLATE_PATH.exists():
        template = WEB_TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        template = HTML_TEMPLATE
    return template.replace("__ACTIVE_PAGE__", active_page)
