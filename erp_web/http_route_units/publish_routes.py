# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == "/api/publish-precheck":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        config = load_store_config()
        platforms = body.get("platforms") or []
        if isinstance(platforms, str):
            platforms = [platforms]
        platforms = [str(item).strip().lower() for item in platforms if str(item).strip()]
        if not platforms:
            platforms = [str(body.get("platform") or "mercadolibre").strip().lower()]
        results: dict[str, Any] = {}
        updated = product
        for platform in platforms:
            result = validate_platform_draft(updated, platform, config)
            updated = apply_precheck_to_product(updated, platform, result, status="ready" if result.get("ok") else "not_ready")
            results[platform] = result
        saved = save_product(updated)
        handler.send_json({"ok": True, "platform": platforms[0] if len(platforms) == 1 else "", "platforms": results, "product": saved, "productsIndex": load_products_index()})
        return True
    if parsed.path == "/api/publish-payload-preview":
        body = handler.read_body()
        platform = str(body.get("platform") or "mercadolibre").strip().lower()
        product = normalize_product_fields(body.get("product") or load_product())
        if platform not in PLATFORMS:
            handler.send_json({"ok": False, "error": "不支持的平台"}, 400)
            return True
        if platform != "mercadolibre":
            handler.send_json(
                {
                    "ok": True,
                    "platform": platform,
                    "status": "pending_real_interface",
                    "message": "payload 待真实接口完善",
                    "payload": {"platform": platform, "message": "payload 待真实接口完善"},
                }
            )
            return True
        try:
            payload = app._sanitize_for_log(build_mercadolibre_payload_preview(product, load_store_config()))
            path = OUTPUT_DIR / "last_mercadolibre_payload.json"
            write_json(path, payload)
            append_ml_publish_log(product, "payload_preview", collect_time_iso(), payload, {"ok": True, "status": "payload_preview", "path": str(path)}, "", "", {}, "仅预览 payload，未调用真实发布")
            handler.send_json({"ok": True, "platform": platform, "status": "preview_only", "payload": payload, "path": str(path)})
        except Exception as exc:
            path = OUTPUT_DIR / "last_mercadolibre_payload.json"
            if path.exists():
                handler.send_json({"ok": True, "platform": platform, "status": "file_fallback", "payload": read_json(path, {}), "path": str(path), "warning": str(exc)})
            else:
                handler.send_json({"ok": False, "platform": platform, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/publish-product":
        body = handler.read_body()
        platform = body.get("platform", "mercadolibre")
        product = normalize_product_fields(body.get("product") or load_product())
        config = load_store_config()
        try:
            result = publish_product(product, platform, config)
            status = 200 if result.get("ok") else 400
            handler.send_json(result, status)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/mercadolibre/confirm-real-publish":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        confirm = bool(body.get("confirm_real_publish") or body.get("confirm"))
        try:
            result = mercadolibre_real_publish(product, confirm)
            handler.send_json(result, 200 if result.get("ok") else 400)
        except Exception as exc:
            handler.send_json({"ok": False, "status": "real_publish_failed", "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/mercadolibre/close-item":
        body = handler.read_body()
        try:
            result = mercadolibre_close_remote_item(str(body.get("item_id") or body.get("id") or ""))
            handler.send_json(result, 200 if result.get("ok") else 400)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/publish-bus/enqueue":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        platforms = body.get("platforms") or []
        if isinstance(platforms, str):
            platforms = [platforms]
        platforms = [str(item).strip() for item in platforms if str(item).strip()]
        try:
            eligible_platforms = publish_queue_platforms(product, platforms)
            rejected_platforms = [platform for platform in platforms if platform not in eligible_platforms]
            if not eligible_platforms:
                handler.send_json(
                    {
                        "ok": False,
                        "error": "当前商品未通过发布队列准入：请先把草稿推进到“校验通过”。",
                        "error_code": "PUBLISH_QUEUE_NOT_READY",
                        "eligible_platforms": [],
                        "rejected_platforms": rejected_platforms,
                        "workflow_statuses": (sync_product_workflow_statuses(product).get("workflow_statuses") or {}),
                    },
                    400,
                )
                return True
            result = PUBLISHING_BUS.enqueue(product, eligible_platforms, load_store_config())
            result["eligible_platforms"] = eligible_platforms
            result["rejected_platforms"] = rejected_platforms
            handler.send_json(result)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    return False
