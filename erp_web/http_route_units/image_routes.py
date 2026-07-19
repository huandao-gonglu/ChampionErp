from __future__ import annotations

from typing import Any

from erp_web.services import image_service, image_translate_service


IMAGE_POST_PATHS = {
    "/api/image-pool/upload",
    "/api/image-pool/save",
    "/api/image-pool/action",
    "/api/image-pool/sync-generated",
    "/api/image-translate",
    "/api/image-edit",
}


def request_product(body: dict[str, Any], app: Any) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    product_id = str(body.get("product_id") or "").strip()
    if not product_id:
        return {}, {"ok": False, "error": "product_id 不能为空"}, 400
    product = app.load_product_from_index(product_id, "")
    loaded_id = str(product.get("product_id") or product.get("id") or "").strip()
    if loaded_id != product_id:
        return {}, {"ok": False, "error": "商品不存在", "product_id": product_id}, 404
    return app.normalize_product_fields(product), None, 200


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def handle_post(handler: Any, path: str, app: Any) -> bool:
    if path not in IMAGE_POST_PATHS:
        return False

    body = handler.read_body()

    if path == "/api/image-pool/upload":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        uploads = body.get("uploads") or []
        if isinstance(uploads, dict):
            uploads = [uploads]
        if not isinstance(uploads, list) or not uploads:
            raise RuntimeError("缂哄皯涓婁紶鍥剧墖")
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        uploaded_items = image_service.upload_images(app.APP_DIR, uploads, str(product.get("product_id") or ""))
        if not uploaded_items:
            raise RuntimeError("涓婁紶鍥剧墖澶辫触锛屾湭瑙ｇ爜鎴愬姛")
        pool = image_service.add_images(source.get("image_pool") if isinstance(source.get("image_pool"), list) else [], uploaded_items, app.APP_DIR)
        saved = app.save_product(app.apply_service_image_pool(product, pool))
        handler.send_json(
            {
                "ok": True,
                "product": saved,
                "imagePool": app.current_image_pool(saved),
                "sourceImages": app.current_source_images(saved),
                "productsIndex": app.load_products_index(),
            }
        )
        return True

    if path == "/api/image-pool/save":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        result = app.save_image_pool_for_product(
            str(product.get("product_id") or ""),
            image_service.normalize_pool(body.get("image_pool") if isinstance(body.get("image_pool"), list) else [], app.APP_DIR),
        )
        handler.send_json(result, 200 if result.get("ok") else 400)
        return True

    if path == "/api/image-pool/action":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
        updated_pool = image_service.apply_image_action(app.APP_DIR, pool, str(body.get("action") or ""), {**body, "product_id": product.get("product_id")})
        if str(body.get("action") or "").strip().lower() == "filter":
            handler.send_json({"ok": True, "imagePool": updated_pool})
            return True
        saved = app.save_product(app.apply_service_image_pool(product, updated_pool))
        handler.send_json(
            {
                "ok": True,
                "product": saved,
                "imagePool": app.current_image_pool(saved),
                "sourceImages": app.current_source_images(saved),
                "productsIndex": app.load_products_index(),
            }
        )
        return True

    if path == "/api/image-pool/sync-generated":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        merged = app.sync_generated_images_into_pool(product)
        saved = app.save_product(merged)
        handler.send_json(
            {
                "ok": True,
                "product": saved,
                "imagePool": app.current_image_pool(saved),
                "sourceImages": app.current_source_images(saved),
                "productsIndex": app.load_products_index(),
            }
        )
        return True

    if path == "/api/image-translate":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        image_ids = body.get("source_image_ids") if isinstance(body.get("source_image_ids"), list) else []
        result = image_translate_service.translate_images(
            app.APP_DIR,
            product,
            app.load_app_config(),
            target_language=str(body.get("language") or body.get("target_language") or "es"),
            platform=str(body.get("platform") or "mercadolibre"),
            image_ids=image_ids,
            mode=str(body.get("mode") or "translate"),
        )
        if result.get("ok"):
            merged = app.append_images_to_product_pool(product, result.get("imagePoolItems") if isinstance(result.get("imagePoolItems"), list) else [])
            saved = app.save_product(merged)
            draft_result = {}
            draft_error = None
            draft_status = 200
            if _truthy(body.get("apply_to_draft")):
                draft_result, draft_error, draft_status = app.apply_image_assets_to_draft(
                    str(body.get("draft_id") or body.get("draftId") or ""),
                    result.get("imagePoolItems") if isinstance(result.get("imagePoolItems"), list) else [],
                    str(body.get("draft_image_strategy") or body.get("draftImageStrategy") or "append"),
                )
                if draft_error:
                    handler.send_json({**result, **draft_error, "product": saved, "productsIndex": app.load_products_index()}, draft_status)
                    return True
            handler.send_json(
                {
                    "ok": True,
                    **result,
                    "product": saved,
                    "imagePool": app.current_image_pool(saved),
                    "sourceImages": app.current_source_images(saved),
                    "productsIndex": app.load_products_index(),
                    "draft": draft_result.get("draft") if isinstance(draft_result, dict) else None,
                    "productContext": draft_result.get("productContext") if isinstance(draft_result, dict) else None,
                    "draftsIndex": draft_result.get("draftsIndex") if isinstance(draft_result, dict) else app.load_drafts_index(),
                }
            )
            return True
        handler.send_json({"ok": False, "product": product, "imagePool": app.current_image_pool(product), **result}, 200)
        return True

    if path == "/api/image-edit":
        product, error_response, status = request_product(body, app)
        if error_response:
            handler.send_json(error_response, status)
            return True
        image_ids = body.get("source_image_ids") if isinstance(body.get("source_image_ids"), list) else []
        result = image_translate_service.edit_images(
            app.APP_DIR,
            product,
            app.load_app_config(),
            prompt=str(body.get("prompt") or ""),
            platform=str(body.get("platform") or "mercadolibre"),
            image_ids=image_ids,
        )
        if result.get("ok"):
            image_items = result.get("imagePoolItems") if isinstance(result.get("imagePoolItems"), list) else []
            merged = app.append_images_to_product_pool(product, image_items)
            saved = app.save_product(merged)
            draft_result = {}
            draft_error = None
            draft_status = 200
            if _truthy(body.get("apply_to_draft")):
                draft_result, draft_error, draft_status = app.apply_image_assets_to_draft(
                    str(body.get("draft_id") or body.get("draftId") or ""),
                    image_items,
                    str(body.get("draft_image_strategy") or body.get("draftImageStrategy") or "append"),
                )
                if draft_error:
                    handler.send_json({**result, **draft_error, "product": saved, "productsIndex": app.load_products_index()}, draft_status)
                    return True
            handler.send_json(
                {
                    "ok": True,
                    **result,
                    "product": saved,
                    "imagePool": app.current_image_pool(saved),
                    "sourceImages": app.current_source_images(saved),
                    "productsIndex": app.load_products_index(),
                    "draft": draft_result.get("draft") if isinstance(draft_result, dict) else None,
                    "productContext": draft_result.get("productContext") if isinstance(draft_result, dict) else None,
                    "draftsIndex": draft_result.get("draftsIndex") if isinstance(draft_result, dict) else app.load_drafts_index(),
                }
            )
            return True
        handler.send_json({"ok": False, "product": product, "imagePool": app.current_image_pool(product), **result}, 200)
        return True

    return False
