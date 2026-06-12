# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

from .publish_helpers import *
from .publish_validation import *
from .publish_logs_runtime import *

def run_mercadolibre_07d_test(mode: str, product: dict[str, Any] | None = None, category_id_override: str = "") -> dict[str, Any]:
    mode = str(mode or "auth_link").strip().lower()
    product = normalize_product_fields(product or load_product())
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    token = str(ml.get("access_token") or "").strip()
    refresh_token = str(ml.get("refresh_token") or "").strip()
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = _mercadolibre_app_secret(ml)
    redirect_uri = str(ml.get("redirect_uri") or "").strip()
    result: dict[str, Any] = {
        "ok": True,
        "platform": "mercadolibre",
        "mode": mode,
        "checked_at": collect_time_iso(),
        "real_publish_called": False,
        "message": "当前仍未真实发布。",
    }

    try:
        if mode == "auth_link":
            url = preview_mercadolibre_auth_link(app_id, redirect_uri)
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            result.update(
                {
                    "auth_url": url,
                    "redirect_uri": query.get("redirect_uri", [""])[0],
                    "redirect_uri_matches_config": query.get("redirect_uri", [""])[0] == redirect_uri,
                    "client_id_present": bool(query.get("client_id", [""])[0]),
                }
            )
            append_ml_auth_test_log("auth_link", "success", {"redirect_uri": redirect_uri}, result, next_action="打开授权链接并完成回调，或手动粘贴 code。")
            return result

        if mode == "user_info":
            if not token:
                raise RuntimeError("Mercado Libre access_token 为空。")
            data = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
            if not isinstance(data, dict):
                raise RuntimeError(f"Mercado Libre users/me 返回异常: {data}")
            ml["user_id"] = str(data.get("id") or ml.get("user_id") or "").strip()
            ml["seller_id"] = str(data.get("id") or ml.get("seller_id") or "").strip()
            ml["nickname"] = str(data.get("nickname") or ml.get("nickname") or "").strip()
            ml["site_id"] = str(data.get("site_id") or ml.get("site_id") or "MLM").strip() or "MLM"
            ml["shop_name"] = ml.get("nickname") or ml.get("shop_name") or ml.get("user_id") or ""
            ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
            ml["auth_error_code"] = ""
            ml["auth_error_message"] = ""
            save_store_config(config)
            result.update(
                {
                    "status": "success",
                    "user_id_present": bool(ml.get("user_id")),
                    "seller_id_present": bool(ml.get("seller_id")),
                    "nickname_present": bool(ml.get("nickname")),
                    "site_id": ml.get("site_id") or "",
                    "storeAuthSummary": summarize_store_auth_states(config),
                }
            )
            append_ml_auth_test_log("user_info", "success", {"endpoint": "users/me"}, result, next_action="授权可用于后续类目、图片和 payload 测试。")
            return result

        if mode == "refresh_token":
            if not app_id or not app_secret or not refresh_token:
                raise RuntimeError("App ID、Client Secret 或 Refresh Token 缺失。")
            refreshed = refresh_mercadolibre_token_from_body({})
            result.update({"status": "success", **_sanitize_for_log(refreshed)})
            append_ml_auth_test_log("refresh_token", "success", {"grant_type": "refresh_token"}, result, next_action="刷新成功后重新测试用户信息。")
            return result

        if mode == "category_attrs":
            category_id = str(category_id_override or "").strip() or _mercadolibre_category_id_from_product(product)
            if not category_id:
                raise RuntimeError("drafts.mercadolibre.category_id 为空。")
            if _is_mock_mercadolibre_category_id(category_id):
                raise RuntimeError("REAL_CATEGORY_REQUIRED: 当前 category_id 是 mock/seed 测试类目，请先选择真实 Mercado Libre 类目，或手动输入真实 category_id。")
            path = publisher.mercadolibre_category_path(category_id, token)
            attrs = publisher.mercadolibre_category_attributes(category_id, token)
            required_ids = _mercadolibre_required_attr_ids(attrs)
            draft_attrs = _draft_for_platform(product, "mercadolibre").get("attributes")
            draft_attrs = draft_attrs if isinstance(draft_attrs, dict) else {}
            missing = [attr_id for attr_id in required_ids if not str(draft_attrs.get(attr_id) or "").strip()]
            result.update(
                {
                    "status": "success",
                    "category_id": category_id,
                    "category_path": path,
                    "required_count": len(required_ids),
                    "missing_required": missing,
                    "field_errors": [
                        precheck_item("REQUIRED_ATTRIBUTE_MISSING", f"attributes.{attr_id}", f"真实类目缺少必填属性：{attr_id}", "error", "前往类目属性页补齐")
                        for attr_id in missing
                    ],
                    "required_attributes": attrs[:80],
                }
            )
            append_ml_auth_test_log("category_attrs", "success" if not missing else "failed", {"category_id": category_id}, result, error_code="REQUIRED_ATTRIBUTE_MISSING" if missing else "", error_message=f"缺少 {len(missing)} 个真实必填属性" if missing else "", next_action="前往类目属性页补齐缺失属性" if missing else "真实类目属性读取成功。")
            return result

        if mode == "image_upload":
            candidates = _mercadolibre_image_candidates(product)
            if not candidates:
                error = precheck_item("IMAGE_NOT_FOUND", "images", "Mercado Libre 没有可用图片", "error", "在 07D 向导上传一张测试主图")
                result.update({"ok": False, "status": "failed", "error_code": error["code"], "error_message": error["message"], "next_action": error["next_action"], "errors": [error], "product": product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": 0}, result, error["code"], error["message"], error["next_action"])
                return result
            has_uploadable = any(_mercadolibre_picture_id(item) or _local_path_from_image_item(item) for item in candidates)
            if not has_uploadable:
                error = precheck_item("IMAGE_UNAVAILABLE", "images", "Mercado Libre 图片不是本地文件，无法执行真实图片上传测试", "error", "请使用 07D 上传测试主图入口上传一张本地图片")
                result.update({"ok": False, "status": "failed", "error_code": error["code"], "error_message": error["message"], "next_action": error["next_action"], "errors": [error], "product": product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": len(candidates)}, result, error["code"], error["message"], error["next_action"])
                return result
            auth = ensure_mercadolibre_auth_ready(config)
            if not auth.get("ok"):
                raise RuntimeError(auth.get("message") or "Mercado Libre 授权不可用。")
            upload = ensure_mercadolibre_pictures_uploaded(product, str(auth.get("token") or ""))
            if not upload.get("ok"):
                first = (upload.get("errors") or [{}])[0]
                result.update({"ok": False, "status": "failed", "errors": upload.get("errors") or [], "product": upload.get("product") or product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": len(_mercadolibre_image_candidates(product))}, result, str(first.get("code") or "IMAGE_UPLOAD_FAILED"), str(first.get("message") or "图片上传失败"), str(first.get("next_action") or "前往图片池修复图片"))
                return result
            result.update({"status": "success", "picture_refs": upload.get("picture_refs") or [], "product": upload.get("product") or product})
            append_ml_auth_test_log("image_upload", "success", {"image_count": len(_mercadolibre_image_candidates(product))}, result, next_action="图片上传测试成功，仍未真实发布。")
            return result

        if mode == "payload_generate":
            payload = build_mercadolibre_payload_preview(product, config)
            path = OUTPUT_DIR / "last_mercadolibre_payload.json"
            write_json(path, _sanitize_for_log(payload))
            draft = _draft_for_platform(product, "mercadolibre")
            draft_category_id = str(draft.get("category_id") or "").strip()
            sites_to_sell = payload.get("sites_to_sell") if isinstance(payload.get("sites_to_sell"), list) else []
            attributes = payload.get("attributes") if isinstance(payload.get("attributes"), list) else []
            picture_items = payload.get("pictures") if isinstance(payload.get("pictures"), list) else []
            if not picture_items:
                for site in sites_to_sell:
                    if isinstance(site, dict) and isinstance(site.get("pictures"), list):
                        picture_items = site.get("pictures") or []
                        break
            condition_present = bool(payload.get("condition")) or any(str(attr.get("id") or "") in {"ITEM_CONDITION", "CONDITION"} for attr in attributes if isinstance(attr, dict))
            pictures_present = bool(picture_items)
            pictures_use_ml_id = bool(picture_items) and all(isinstance(pic, dict) and bool(pic.get("id")) and not pic.get("source") for pic in picture_items)
            shipping_present = bool(payload.get("shipping")) or any(str(site.get("logistic_type") or "").strip() for site in sites_to_sell if isinstance(site, dict))
            required_checks = {
                "title": bool(payload.get("title")),
                "category_id": bool(payload.get("category_id")),
                "category_id_from_draft": bool(draft_category_id) and str(payload.get("category_id") or "").strip() == draft_category_id,
                "price": "price" in payload,
                "currency_id": bool(payload.get("currency_id")),
                "available_quantity": "available_quantity" in payload,
                "buying_mode": bool(payload.get("buying_mode")),
                "listing_type_id": bool(payload.get("listing_type_id")),
                "condition": condition_present,
                "pictures": pictures_present,
                "pictures_with_mercadolibre_id": pictures_use_ml_id,
                "attributes": bool(attributes),
                "sale_terms": bool(payload.get("sale_terms")),
                "shipping_or_logistics": shipping_present,
            }
            missing_keys = [key for key, present in required_checks.items() if not present]
            result.update({"ok": not missing_keys, "status": "success" if not missing_keys else "failed", "payload": _sanitize_for_log(payload), "path": str(path), "missing_keys": missing_keys})
            append_ml_auth_test_log("payload_generate", "success" if not missing_keys else "failed", {"platform": "mercadolibre"}, {"path": str(path), "missing_keys": missing_keys, "payload": _sanitize_for_log(payload)}, error_code="PAYLOAD_FIELD_MISSING" if missing_keys else "", error_message=", ".join(missing_keys), next_action="补齐 payload 缺失字段" if missing_keys else "payload 已生成，仍未真实发布。")
            return result

        if mode == "all":
            outputs = []
            for sub_mode in ("auth_link", "user_info", "category_attrs", "payload_generate"):
                try:
                    outputs.append(run_mercadolibre_07d_test(sub_mode, product))
                except Exception as exc:
                    outputs.append({"ok": False, "mode": sub_mode, "error": str(exc), "error_code": _mercadolibre_test_error_code(str(exc))})
            result["tests"] = outputs
            result["ok"] = all(item.get("ok", True) and item.get("status") != "failed" for item in outputs)
            return result

        raise RuntimeError(f"不支持的 07D 测试模式：{mode}")
    except Exception as exc:
        message = str(exc)
        code = _mercadolibre_test_error_code(message)
        status = "failed"
        if code == "NETWORK_BLOCKED":
            next_action = "当前环境无法访问 Mercado Libre，请换到允许外网 socket 的本机环境重试。"
        elif code == "REAL_CATEGORY_REQUIRED":
            next_action = "请先选择真实 Mercado Libre 类目，或在 07D 向导里手动输入真实 category_id。"
        else:
            next_action = _auth_next_action("mercadolibre", "测试失败", code, message)
        response = {"ok": False, "platform": "mercadolibre", "mode": mode, "status": status, "error_code": code, "error_message": message, "next_action": next_action, "real_publish_called": False}
        append_ml_auth_test_log(mode, status, {"mode": mode}, response, code, message, next_action)
        return response


def _mercadolibre_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), list) else []
    seller_sku = ""
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        if str(attr.get("id") or "").upper() == "SELLER_SKU":
            seller_sku = str(attr.get("value_name") or attr.get("value_id") or "").strip()
            break
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "status": str(item.get("status") or "").strip(),
        "sub_status": item.get("sub_status") if isinstance(item.get("sub_status"), list) else [],
        "permalink": str(item.get("permalink") or "").strip(),
        "thumbnail": str(item.get("thumbnail") or item.get("secure_thumbnail") or "").strip(),
        "price": item.get("price"),
        "currency_id": str(item.get("currency_id") or "").strip(),
        "available_quantity": item.get("available_quantity"),
        "sold_quantity": item.get("sold_quantity"),
        "category_id": str(item.get("category_id") or "").strip(),
        "listing_type_id": str(item.get("listing_type_id") or "").strip(),
        "seller_sku": seller_sku,
        "date_created": str(item.get("date_created") or "").strip(),
        "last_updated": str(item.get("last_updated") or "").strip(),
        "raw": _sanitize_for_log(item),
    }


def mercadolibre_remote_items(status: str = "active", limit: int = 50) -> dict[str, Any]:
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        return {"ok": False, "error": auth.get("message") or "Mercado Libre 授权不可用", "error_code": auth.get("error_code") or "AUTH_INVALID", "next_action": auth.get("next_action") or "请先完成授权测试"}
    token = str(auth.get("token") or "").strip()
    store = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    user_id = str(store.get("user_id") or store.get("seller_id") or "").strip()
    if not user_id:
        me = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
        if not isinstance(me, dict):
            raise RuntimeError("Mercado Libre users/me 返回异常")
        user_id = str(me.get("id") or "").strip()
        if user_id:
            config.setdefault("mercadolibre", {})["user_id"] = user_id
            config.setdefault("mercadolibre", {})["seller_id"] = user_id
            save_store_config(config)
    if not user_id:
        raise RuntimeError("Mercado Libre seller id 为空，请先测试授权。")

    wanted = str(status or "active").strip().lower()
    statuses = ["active", "paused", "closed"] if wanted == "all" else [wanted]
    statuses = [item for item in statuses if item in {"active", "paused", "closed"}] or ["active"]
    limit = max(1, min(int(limit or 50), 100))

    item_ids: list[str] = []
    paging: dict[str, Any] = {}
    for item_status in statuses:
        query = urllib.parse.urlencode({"status": item_status, "limit": limit})
        search = publisher.request_json("GET", f"https://api.mercadolibre.com/users/{user_id}/items/search?{query}", token)
        if not isinstance(search, dict):
            continue
        paging[item_status] = search.get("paging") if isinstance(search.get("paging"), dict) else {}
        for item_id in search.get("results") or []:
            text = str(item_id or "").strip()
            if text and text not in item_ids:
                item_ids.append(text)

    items: list[dict[str, Any]] = []
    for index in range(0, len(item_ids), 20):
        chunk = item_ids[index:index + 20]
        if not chunk:
            continue
        batch = publisher.request_json("GET", f"https://api.mercadolibre.com/items?ids={urllib.parse.quote(','.join(chunk))}", token)
        if not isinstance(batch, list):
            continue
        for entry in batch:
            body = entry.get("body") if isinstance(entry, dict) else None
            if isinstance(body, dict):
                items.append(_mercadolibre_item_summary(body))

    return {
        "ok": True,
        "platform": "mercadolibre",
        "status": wanted,
        "user_id": user_id,
        "items": items,
        "item_ids": item_ids,
        "paging": paging,
        "checked_at": collect_time_iso(),
    }


def mercadolibre_close_remote_item(item_id: str) -> dict[str, Any]:
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "缺少 Mercado Libre item id", "error_code": "ITEM_ID_MISSING"}
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        return {"ok": False, "error": auth.get("message") or "Mercado Libre 授权不可用", "error_code": auth.get("error_code") or "AUTH_INVALID", "next_action": auth.get("next_action") or "请先完成授权测试"}
    token = str(auth.get("token") or "").strip()
    if item_id.upper().startswith("CBT"):
        store = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
        target_site_id = str(store.get("site_id") or "MLM").strip().upper() or "MLM"
        payload = {"site_id": target_site_id, "logistic_type": "remote", "deleted": True}
        result = publisher.request_json("PUT", f"https://api.mercadolibre.com/global/items/{urllib.parse.quote(item_id)}", token, payload)
        return {
            "ok": True,
            "platform": "mercadolibre",
            "item_id": item_id,
            "status": "closed",
            "raw": _sanitize_for_log(result if isinstance(result, dict) else {"response": result}),
            "message": f"{item_id} 已提交 Global Selling {target_site_id} 删除。",
        }
    result = publisher.request_json("PUT", f"https://api.mercadolibre.com/items/{urllib.parse.quote(item_id)}", token, {"status": "closed"})
    item = result if isinstance(result, dict) else {}
    return {
        "ok": True,
        "platform": "mercadolibre",
        "item_id": item_id,
        "status": str(item.get("status") or "closed"),
        "item": _mercadolibre_item_summary(item) if item else {},
        "raw": _sanitize_for_log(item),
        "message": f"{item_id} 已提交结束发布。",
    }


def _local_path_from_image_item(item: dict[str, Any]) -> Path | None:
    for key in ("path", "url", "preview_url"):
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        if value.startswith("/file?"):
            parsed = urllib.parse.urlparse(value)
            path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if path:
                candidate = Path(path)
                if candidate.exists():
                    return candidate
        if value.startswith("file:"):
            candidate = Path(urllib.parse.urlparse(value).path)
            if candidate.exists():
                return candidate
        if not value.startswith(("http://", "https://", "ml-id:")):
            candidate = Path(value)
            if candidate.exists():
                return candidate
    return None


def _mercadolibre_picture_id(item: dict[str, Any]) -> str:
    for key in ("platform_picture_id", "mercadolibre_picture_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    uploads = item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}
    ml_upload = uploads.get("mercadolibre") if isinstance(uploads.get("mercadolibre"), dict) else {}
    return str(ml_upload.get("picture_id") or ml_upload.get("id") or "").strip()


def _mercadolibre_image_candidates(product: dict[str, Any]) -> list[dict[str, Any]]:
    pool = _source_pool_items(product)
    candidates = []
    for item in pool:
        platforms = [str(value).strip().lower() for value in (item.get("platforms") or [])]
        if platforms and "mercadolibre" not in platforms:
            continue
        if str(item.get("status") or "").strip().lower() == "empty":
            continue
        if item.get("selected") or item.get("is_main"):
            candidates.append(item)
    if not candidates:
        candidates = [
            item for item in pool
            if (not item.get("platforms") or "mercadolibre" in [str(value).strip().lower() for value in (item.get("platforms") or [])])
            and str(item.get("status") or "").strip().lower() != "empty"
        ]
    return sorted(candidates, key=lambda item: (0 if item.get("is_main") else 1, int(item.get("order") or 0)))


def ensure_mercadolibre_pictures_uploaded(product: dict[str, Any], token: str) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    pool = _source_pool_items(normalized)
    selected_ids = {str(item.get("id") or "") for item in _mercadolibre_image_candidates(normalized)}
    errors: list[dict[str, str]] = []
    picture_refs: list[str] = []
    if not selected_ids:
        errors.append(precheck_item("IMAGE_NOT_FOUND", "images", "Mercado Libre 没有可用图片", "error", "前往图片池选择主图或勾选 Mercado Libre 图片"))
        return {"ok": False, "product": normalized, "picture_refs": [], "errors": errors}
    updated_pool: list[dict[str, Any]] = []
    for item in pool:
        item = dict(item)
        if str(item.get("id") or "") not in selected_ids:
            updated_pool.append(item)
            continue
        picture_id = _mercadolibre_picture_id(item)
        if not picture_id and str(item.get("url") or "").startswith("ml-id:"):
            picture_id = str(item.get("url") or "").split(":", 1)[1].strip()
        if picture_id:
            item["platform_picture_id"] = picture_id
            item["mercadolibre_picture_id"] = picture_id
            item["upload_status"] = "uploaded"
            item.setdefault("uploaded_at", collect_time_iso())
            item["platform_uploads"] = {**(item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}), "mercadolibre": {"picture_id": picture_id, "upload_status": "uploaded", "uploaded_at": item.get("uploaded_at")}}
            picture_refs.append(f"ml-id:{picture_id}")
            updated_pool.append(item)
            continue
        local_path = _local_path_from_image_item(item)
        if not local_path:
            item["upload_status"] = "failed"
            item["upload_error"] = "图片不是本地文件，无法在真实发布前上传 Mercado Libre。"
            errors.append(precheck_item("IMAGE_UNAVAILABLE", "images", f"图片不可上传或不可访问：{item.get('id') or item.get('url') or item.get('path')}", "error", "前往图片池替换为本地可上传图片"))
            updated_pool.append(item)
            continue
        try:
            upload = publisher.upload_mercadolibre_picture(local_path, token)
            picture_id = str(upload.get("id") or upload.get("secure_url") or upload.get("url") or "").strip()
            if not picture_id:
                raise RuntimeError(f"Mercado Libre 图片上传未返回 picture id: {upload}")
            item["platform_picture_id"] = picture_id
            item["mercadolibre_picture_id"] = picture_id
            item["upload_status"] = "uploaded"
            item["uploaded_at"] = collect_time_iso()
            item["platform_uploads"] = {**(item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}), "mercadolibre": {"picture_id": picture_id, "upload_status": "uploaded", "uploaded_at": item["uploaded_at"]}}
            picture_refs.append(f"ml-id:{picture_id}")
        except Exception as exc:
            item["upload_status"] = "failed"
            item["upload_error"] = str(exc)
            errors.append(precheck_item("IMAGE_UPLOAD_FAILED", "images", mercadolibre_picture_upload_error_message(exc), "error", "检查图片文件格式/尺寸后重试"))
        updated_pool.append(item)
    source["image_pool"] = updated_pool
    normalized["source"] = source
    normalized.setdefault("drafts", {}).setdefault("mercadolibre", default_draft("mercadolibre"))["images"] = picture_refs
    normalized["source_image_urls"] = picture_refs
    saved = save_product(normalized)
    errors = compact_precheck_items(errors)
    return {"ok": not errors, "product": saved, "picture_refs": picture_refs, "errors": errors}


def ensure_mercadolibre_auth_ready(config: dict[str, Any]) -> dict[str, Any]:
    store = config.setdefault("mercadolibre", {})
    token = str(store.get("access_token") or "").strip()
    if not token:
        return {"ok": False, "error_code": "AUTH_NOT_CONFIGURED", "message": "Mercado Libre Access Token 为空", "next_action": "请先完成授权测试"}
    try:
        me = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
        if not isinstance(me, dict):
            raise RuntimeError("Mercado Libre users/me 返回异常")
        name = str(me.get("nickname") or me.get("id") or "").strip()
        store["shop_name"] = name or store.get("shop_name", "")
        store["account_site_id"] = str(me.get("site_id") or store.get("account_site_id") or "").strip().upper()
        store["user_id"] = str(me.get("id") or store.get("user_id") or "").strip()
        store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
        store["auth_error_code"] = ""
        store["auth_error_message"] = ""
        save_store_config(config)
        return {"ok": True, "token": token, "seller": name or store.get("user_id") or ""}
    except Exception as exc:
        message = str(exc)
        if publisher.is_mercadolibre_auth_error(exc) and str(store.get("refresh_token") or "").strip():
            try:
                refreshed = publisher.refresh_mercadolibre_token(str(store.get("app_id") or ""), str(store.get("app_secret") or ""), str(store.get("refresh_token") or ""))
                token = str(refreshed.get("access_token") or "").strip()
                store["access_token"] = token
                store["refresh_token"] = str(refreshed.get("refresh_token") or store.get("refresh_token") or "").strip()
                me = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
                if not isinstance(me, dict):
                    raise RuntimeError("Mercado Libre users/me 返回异常")
                name = str(me.get("nickname") or me.get("id") or "").strip()
                store["shop_name"] = name or store.get("shop_name", "")
                store["account_site_id"] = str(me.get("site_id") or store.get("account_site_id") or "").strip().upper()
                store["user_id"] = str(me.get("id") or store.get("user_id") or "").strip()
                store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
                store["auth_error_code"] = ""
                store["auth_error_message"] = ""
                save_store_config(config)
                return {"ok": True, "token": token, "seller": name or store.get("user_id") or "", "refreshed": True}
            except Exception as refresh_exc:
                message = str(refresh_exc)
        code = store_auth_failure_code("mercadolibre", message)
        return {"ok": False, "error_code": "AUTH_TOKEN_EXPIRED" if "expired" in code.lower() or "expired" in message.lower() else "AUTH_INVALID", "message": message, "next_action": "请先完成授权测试或刷新 token"}


def mercadolibre_product_for_payload(product: dict[str, Any], picture_refs: list[str]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    draft = _draft_for_platform(normalized, "mercadolibre")
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    normalized["category_id"] = str(draft.get("category_id") or "").strip()
    normalized["attributes"] = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    normalized["brand"] = str(draft.get("brand") or normalized.get("brand") or "Generic").strip()
    normalized["model"] = str(draft.get("model") or normalized.get("model") or "General").strip()
    normalized["sku"] = str(draft.get("sku") or normalized.get("sku") or "").strip()
    normalized["upc"] = str(draft.get("upc") or draft.get("gtin") or draft.get("barcode") or normalized.get("upc") or "").strip()
    normalized["name"] = str(draft.get("title") or normalized.get("name") or "").strip()
    normalized["weight_kg"] = str(pkg.get("weight_kg") or normalized.get("weight_kg") or "").strip()
    normalized["dimensions"] = " x ".join(str(pkg.get(key) or "").strip() for key in ("length_cm", "width_cm", "height_cm") if str(pkg.get(key) or "").strip())
    normalized["source_image_urls"] = picture_refs
    return normalized


def mercadolibre_config_for_payload(config: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    cfg = deepcopy(config)
    draft = _draft_for_platform(product, "mercadolibre")
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    cfg.setdefault("mercadolibre", {})["category_id"] = str(draft.get("category_id") or "").strip()
    listing = cfg.setdefault("listing", {})
    for key, value in {
        "mercadolibre_price": draft.get("price"),
        "price": draft.get("price"),
        "stock": draft.get("stock"),
        "sku": draft.get("sku"),
        "upc": draft.get("upc") or draft.get("gtin") or draft.get("barcode"),
        "model": draft.get("model"),
        "mercadolibre_title": draft.get("title"),
        "package_length_cm": pkg.get("length_cm"),
        "package_width_cm": pkg.get("width_cm"),
        "package_height_cm": pkg.get("height_cm"),
        "package_weight_kg": pkg.get("weight_kg"),
        "mercadolibre_attributes": draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {},
    }.items():
        if value not in (None, ""):
            listing[key] = value
    if isinstance(draft.get("sale_terms"), list) and draft.get("sale_terms"):
        listing["mercadolibre_sale_terms"] = draft.get("sale_terms")
    shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    logistic_type = str(shipping.get("logistic_type") or shipping.get("mode") or "").strip()
    if logistic_type:
        listing["mercadolibre_logistic_type"] = logistic_type
    return cfg


def build_mercadolibre_payload_preview(product: dict[str, Any], config: dict[str, Any], picture_refs: list[str] | None = None) -> dict[str, Any]:
    refs = picture_refs if picture_refs is not None else image_pool_refs_for_platform(product, "mercadolibre")
    payload_product = mercadolibre_product_for_payload(product, refs)
    if picture_refs is not None:
        payload_product.setdefault("source", {})["image_pool"] = []
    payload_config = mercadolibre_config_for_payload(config, payload_product)
    return build_publish_payload(payload_product, "mercadolibre", payload_config)


def mercadolibre_real_publish(product: dict[str, Any], confirm: bool) -> dict[str, Any]:
    started_at = collect_time_iso()
    if not confirm:
        return {"ok": False, "status": "confirmation_required", "error": "真实发布需要二次确认。"}
    product = normalize_product_fields(product)
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        error = precheck_item(auth.get("error_code") or "AUTH_INVALID", "auth", auth.get("message") or "Mercado Libre 授权不可用", "error", auth.get("next_action") or "请先完成授权测试")
        precheck = {"platform": "mercadolibre", "ok": False, "errors": [error], "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "not_ready"}, error["code"], error["message"], _field_error_map([error]), error["next_action"])
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", error["message"], saved, precheck=precheck, next_action=error["next_action"])
    precheck = validate_mercadolibre_draft(product, config)
    if not precheck.get("ok"):
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        first = (precheck.get("errors") or [{}])[0]
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "not_ready"}, str(first.get("code") or ""), "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)), _field_error_map(list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])), str(first.get("next_action") or ""))
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "发布前预检未通过", saved, precheck=precheck, next_action=str(first.get("next_action") or ""))
    upload = ensure_mercadolibre_pictures_uploaded(product, str(auth.get("token") or ""))
    product = upload.get("product") or product
    if not upload.get("ok"):
        precheck = {"platform": "mercadolibre", "ok": False, "errors": upload.get("errors") or [], "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        first = (precheck.get("errors") or [{}])[0]
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "image_upload_failed"}, str(first.get("code") or "IMAGE_UPLOAD_FAILED"), "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)), _field_error_map(precheck.get("errors") or []), str(first.get("next_action") or ""))
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "图片上传失败，已禁止真实发布", saved, precheck=precheck, next_action=str(first.get("next_action") or "前往图片池替换或重新上传图片"))
    payload = build_mercadolibre_payload_preview(product, config, upload.get("picture_refs") or [])
    payload_path = OUTPUT_DIR / "last_mercadolibre_payload.json"
    write_json(payload_path, _sanitize_for_log(payload))
    payload_errors = validate_publish_payload("mercadolibre", payload, config)
    if payload_errors:
        errors = [precheck_item("PAYLOAD_INVALID", "payload", message, "error", "前往对应页面补齐字段") for message in payload_errors]
        precheck = {"platform": "mercadolibre", "ok": False, "errors": errors, "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        append_ml_publish_log(updated, "not_ready", started_at, payload, {"ok": False, "errors": payload_errors}, "PAYLOAD_INVALID", "，".join(payload_errors), {"payload": payload_errors}, "前往对应页面补齐字段")
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "，".join(payload_errors), saved, payload_path=str(payload_path), next_action="前往对应页面补齐字段")
    try:
        result = publisher.publish_mercadolibre(payload, str(auth.get("token") or ""))
        ok = isinstance(result, dict) and bool(result.get("id") or result.get("item_id") or result.get("ok") or result.get("success") or result.get("site_items"))
        status = "real_publish_success" if ok else "real_publish_failed"
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status=status)
        append_ml_publish_log(updated, status, started_at, payload, result, "" if ok else "REAL_PUBLISH_FAILED", "" if ok else "Mercado Libre 未返回成功状态", {}, "" if ok else "查看响应后重试")
        saved = save_product(updated)
        return {"ok": ok, "status": status, "result": _sanitize_for_log(result), "payload": _sanitize_for_log(payload), "payload_path": str(payload_path), "product": saved}
    except Exception as exc:
        parsed = publisher.parse_mercadolibre_error(exc)
        mapped = map_mercadolibre_publish_error(parsed)
        errors = [
            precheck_item(str(parsed.get("error") or "REAL_PUBLISH_FAILED"), field, str(values[0] if isinstance(values, list) and values else mapped["summary"]), "error", "前往对应字段修复后重试")
            for field, values in mapped["field_errors"].items()
        ] or [precheck_item(str(parsed.get("error") or "REAL_PUBLISH_FAILED"), "publish", mapped["summary"], "error", "查看字段映射并重试")]
        updated = apply_precheck_to_product(product, "mercadolibre", {"platform": "mercadolibre", "ok": False, "errors": errors, "warnings": [], "checked_at": collect_time_iso()}, status="real_publish_failed")
        append_ml_publish_log(updated, "real_publish_failed", started_at, payload, mapped, str(parsed.get("error") or "REAL_PUBLISH_FAILED"), mapped["summary"], mapped["field_errors"], "按字段提示修复后重试")
        saved = save_product(updated)
        return compact_publish_failure_response("real_publish_failed", mapped["summary"], saved, error_map=mapped, payload_path=str(payload_path), next_action="按字段提示修复后重试")


def map_mercadolibre_publish_error(parsed: dict[str, Any]) -> dict[str, Any]:
    field_errors: dict[str, Any] = {}
    for field in parsed.get("missing_fields") or []:
        field = publisher.normalize_mercadolibre_error_field(str(field))
        if field:
            field_errors.setdefault(field, [])
    missing_attrs = [str(item) for item in parsed.get("missing_attributes") or [] if str(item).strip()]
    if missing_attrs:
        field_errors["attributes"] = missing_attrs

    guidance = {
        "auth": "Mercado Libre 授权无效或已过期，请前往授权页刷新 token。",
        "logistic_type": "当前类目不支持店铺后台的 remote/me1 发货模式，请换一个可发墨西哥的类目，不要随意改物流方式。",
        "attributes": "请在平台属性区域补齐缺失属性后重试。",
        "pictures": "请重新检查图片上传结果，优先使用已导入并可访问的商品图片。",
        "title": "请把 Mercado Libre 标题控制在 60 个字符以内。",
        "sale_terms": "请检查 Warranty type / Warranty time 等售后条款。",
        "category_id": "请填写或更换 Mercado Libre 类目 ID。",
        "price": "请先完成核价并填写发布价格。",
        "stock": "请检查库存 available_quantity 是否为有效正数。",
    }
    hints = [guidance[key] for key in field_errors if key in guidance]
    summary = "；".join(hints) or str(parsed.get("message") or parsed.get("error") or "发布失败")
    return {
        "summary": summary,
        "field_errors": field_errors,
        "missing_attributes": missing_attrs,
        "missing_fields": list(field_errors.keys()),
        "parsed": parsed,
    }
