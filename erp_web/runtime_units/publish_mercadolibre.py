# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.product_model import (
    canonicalize_mercadolibre_siteless_user_product_id,
    default_draft,
    mercadolibre_publication_from_response,
    normalize_mercadolibre_publication,
    normalize_mercadolibre_sites_to_sell,
)
from erp_web.stores.config_store import (
    _store_auth_result_fields,
    auth_next_action,
    store_auth_failure_code,
)
from erp_web.stores.product_store import normalize_product_fields
from erp_web.marketplaces.publisher import PublishAdapterError

from .store_credentials import (
    _mercadolibre_app_secret,
    get_mercadolibre_access_token,
    preview_mercadolibre_auth_link,
    refresh_mercadolibre_token_from_body,
)
from .collect_helpers import collect_time_iso
from .json_store import write_json
from .image_pool_core import _local_path_from_image_item, _source_pool_items, image_pool_refs_for_platform
from .publish_helpers import (
    _draft_for_platform,
    _draft_for_selected_target,
    build_mercadolibre_publish_payload,
    compact_precheck_items,
    mercadolibre_picture_upload_error_message,
    precheck_item,
    validate_mercadolibre_publish_payload,
)
from .publish_logs_runtime import (
    _is_mock_mercadolibre_category_id,
    _mercadolibre_category_id_from_product,
    _mercadolibre_required_attr_ids,
    _sanitize_for_log,
    append_ml_auth_test_log,
    mercadolibre_test_error_code,
)


def _last_mercadolibre_payload_path() -> Path:
    return get_context().paths.output_dir / "last_mercadolibre_payload.json"


def _07d_auth_link(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    redirect_uri = ctx["redirect_uri"]
    url = preview_mercadolibre_auth_link(ctx["app_id"], redirect_uri)
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


def _07d_refresh_token(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    if not ctx["app_id"] or not ctx["app_secret"] or not ctx["refresh_token"]:
        raise RuntimeError("App ID、Client Secret 或 Refresh Token 缺失。")
    refreshed = refresh_mercadolibre_token_from_body({})
    result.update({"status": "success", **_sanitize_for_log(refreshed)})
    append_ml_auth_test_log("refresh_token", "success", {"grant_type": "refresh_token"}, result, next_action="刷新成功后重新测试用户信息。")
    return result


def _07d_category_attrs(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    product = ctx["product"]
    token = get_mercadolibre_access_token(ctx["config"])
    category_id = ctx["category_id_override"] or _mercadolibre_category_id_from_product(product)
    if not category_id:
        raise RuntimeError("drafts.mercadolibre.category_id 为空。")
    if _is_mock_mercadolibre_category_id(category_id):
        raise RuntimeError("REAL_CATEGORY_REQUIRED: 当前 category_id 是 mock/seed 测试类目，请先选择真实 Mercado Libre 类目，或手动输入真实 category_id。")
    path = publisher.mercadolibre_category_path(category_id, token)
    attrs = publisher.mercadolibre_category_attributes_for_publish(category_id, token)
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


def _07d_image_upload(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    product = ctx["product"]
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
    token = get_mercadolibre_access_token(ctx["config"])
    upload = ensure_mercadolibre_pictures_uploaded(product, token)
    if not upload.get("ok"):
        first = (upload.get("errors") or [{}])[0]
        result.update({"ok": False, "status": "failed", "errors": upload.get("errors") or [], "product": upload.get("product") or product})
        append_ml_auth_test_log("image_upload", "failed", {"image_count": len(_mercadolibre_image_candidates(product))}, result, str(first.get("code") or "IMAGE_UPLOAD_FAILED"), str(first.get("message") or "图片上传失败"), str(first.get("next_action") or "前往图片池修复图片"))
        return result
    result.update({"status": "success", "picture_refs": upload.get("picture_refs") or [], "product": upload.get("product") or product})
    append_ml_auth_test_log("image_upload", "success", {"image_count": len(_mercadolibre_image_candidates(product))}, result, next_action="图片上传测试成功，仍未真实发布。")
    return result


def _07d_payload_generate(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    product = ctx["product"]
    payload = build_mercadolibre_payload_preview(product, ctx["config"])
    path = _last_mercadolibre_payload_path()
    write_json(path, _sanitize_for_log(payload))
    # 诊断预览与正式入队共用同一份 payload 校验，避免传统 Global Items
    # 被 User Products 专用的 family_name/title_absent 检查误报。
    missing_keys = validate_mercadolibre_publish_payload(payload, ctx["config"])
    result.update({"ok": not missing_keys, "status": "success" if not missing_keys else "failed", "payload": _sanitize_for_log(payload), "path": str(path), "missing_keys": missing_keys})
    append_ml_auth_test_log("payload_generate", "success" if not missing_keys else "failed", {"platform": "mercadolibre"}, {"path": str(path), "missing_keys": missing_keys, "payload": _sanitize_for_log(payload)}, error_code="PAYLOAD_FIELD_MISSING" if missing_keys else "", error_message=", ".join(missing_keys), next_action="补齐 payload 缺失字段" if missing_keys else "payload 已生成，仍未真实发布。")
    return result


def _07d_all(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    product = ctx["product"]
    outputs = []
    for sub_mode in ("auth_link", "category_attrs", "payload_generate"):
        try:
            outputs.append(run_mercadolibre_07d_test(sub_mode, product))
        except Exception as exc:
            outputs.append({"ok": False, "mode": sub_mode, "error": str(exc), "error_code": mercadolibre_test_error_code(str(exc))})
    result["tests"] = outputs
    result["ok"] = all(item.get("ok", True) and item.get("status") != "failed" for item in outputs)
    return result


# "07D" is the historical wizard-step number this test suite belongs to.
# 诊断模式不得写授权/币种状态；用户信息由统一授权服务接管（已移除
# user_info 模式）。
_07D_MODE_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "auth_link": _07d_auth_link,
    "refresh_token": _07d_refresh_token,
    "category_attrs": _07d_category_attrs,
    "image_upload": _07d_image_upload,
    "payload_generate": _07d_payload_generate,
    "all": _07d_all,
}


def run_mercadolibre_07d_test(mode: str, product: dict[str, Any] | None = None, category_id_override: str = "") -> dict[str, Any]:
    mode = str(mode or "auth_link").strip().lower()
    product = normalize_product_fields(
        product or get_context().products.load_product()
    )
    config = get_context().config.load_store_config()
    ml = config.setdefault("mercadolibre", {})
    ctx: dict[str, Any] = {
        "mode": mode,
        "product": product,
        "config": config,
        "ml": ml,
        "refresh_token": str(ml.get("refresh_token") or "").strip(),
        "app_id": str(ml.get("app_id") or ml.get("client_id") or "").strip(),
        "app_secret": _mercadolibre_app_secret(ml),
        "redirect_uri": str(ml.get("redirect_uri") or "").strip(),
        "category_id_override": str(category_id_override or "").strip(),
        "result": {
            "ok": True,
            "platform": "mercadolibre",
            "mode": mode,
            "checked_at": collect_time_iso(),
            "real_publish_called": False,
            "message": "当前仍未真实发布。",
        },
    }

    try:
        handler = _07D_MODE_HANDLERS.get(mode)
        if handler is None:
            raise RuntimeError(f"不支持的 07D 测试模式：{mode}")
        return handler(ctx)
    except Exception as exc:
        message = str(exc)
        code = mercadolibre_test_error_code(message)
        status = "failed"
        if code == "NETWORK_BLOCKED":
            next_action = "当前环境无法访问 Mercado Libre，请换到允许外网 socket 的本机环境重试。"
        elif code == "REAL_CATEGORY_REQUIRED":
            next_action = "请先选择真实 Mercado Libre 类目，或在 07D 向导里手动输入真实 category_id。"
        else:
            next_action = auth_next_action("mercadolibre", "测试失败", code, message)
        response = {"ok": False, "platform": "mercadolibre", "mode": mode, "status": status, "error_code": code, "error_message": message, "next_action": next_action, "real_publish_called": False}
        append_ml_auth_test_log(mode, status, {"mode": mode}, response, code, message, next_action)
        return response


def _mercadolibre_response_item_id(value: dict[str, Any]) -> str:
    for key in ("id", "item_id", "itemId"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    for key in ("item", "body"):
        nested = value.get(key)
        if isinstance(nested, dict):
            text = _mercadolibre_response_item_id(nested)
            if text:
                return text
    return ""


def _mercadolibre_site_item_errors(result: dict[str, Any]) -> list[dict[str, Any]]:
    site_items = result.get("site_items") if isinstance(result.get("site_items"), list) else []
    errors: list[dict[str, Any]] = []
    for site_item in site_items:
        if not isinstance(site_item, dict):
            continue
        error = site_item.get("error")
        if not isinstance(error, dict):
            continue
        entry = dict(error)
        for key in ("site_id", "logistic_type"):
            if site_item.get(key) and not entry.get(key):
                entry[key] = site_item.get(key)
        errors.append(entry)
    return errors


def _mercadolibre_publish_result_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if _mercadolibre_site_item_errors(result):
        return False
    site_items = result.get("site_items") if isinstance(result.get("site_items"), list) else []
    if site_items:
        return any(isinstance(item, dict) and _mercadolibre_response_item_id(item) for item in site_items)
    return bool(_mercadolibre_response_item_id(result) or result.get("ok") or result.get("success"))


def _mercadolibre_publish_result_error_map(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"summary": "Mercado Libre 未返回成功状态", "field_errors": {}, "parsed": {"raw": str(result)}}
    site_errors = _mercadolibre_site_item_errors(result)
    if not site_errors:
        return {
            "summary": str(result.get("message") or result.get("error") or "Mercado Libre 未返回成功状态"),
            "field_errors": {},
            "parsed": {"raw": result},
        }

    parsed = publisher.parse_mercadolibre_error(json.dumps(site_errors[0], ensure_ascii=False))
    mapped = map_mercadolibre_publish_error(parsed)
    messages: list[str] = []
    for error in site_errors:
        prefix = str(error.get("site_id") or "").strip()
        cause = error.get("cause") if isinstance(error.get("cause"), list) else []
        cause_messages = [
            str(item.get("message") or "").strip()
            for item in cause
            if isinstance(item, dict) and str(item.get("message") or "").strip()
        ]
        if not cause_messages:
            fallback = str(error.get("message") or error.get("error") or "").strip()
            if fallback:
                cause_messages = [fallback]
        for message in cause_messages:
            messages.append(f"{prefix}: {message}" if prefix else message)
    if messages:
        mapped["summary"] = "；".join(messages)
    mapped["site_item_errors"] = site_errors
    return mapped


def _local_mercadolibre_user_product_records() -> list[dict[str, Any]]:
    """从本地草稿 publication 构建 User Products 索引。

    Mercado Libre 没有可依赖的全量 Siteless families 列表端点，因此本地
    publication 是唯一主索引；远端 mapping 只能刷新已知 Siteless ID。
    """

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for indexed in get_context().products.iter_drafts_index(scope="all"):
        if str(indexed.get("platform") or "").strip().lower() != "mercadolibre":
            continue
        raw = indexed.get("raw") if isinstance(indexed.get("raw"), dict) else {}
        publication = normalize_mercadolibre_publication(raw.get("publication"))
        siteless_id = str(publication.get("siteless_user_product_id") or "").strip()
        if not siteless_id or siteless_id in seen:
            continue
        seen.add(siteless_id)
        records.append(
            {
                "product_id": str(indexed.get("source_product_id") or indexed.get("product_id") or "").strip(),
                "draft_id": str(indexed.get("draft_id") or "").strip(),
                "title": str(indexed.get("title") or indexed.get("product_title") or publication.get("family_name") or "").strip(),
                "main_image": str(indexed.get("main_image") or "").strip(),
                "publication": publication,
                "updated_at": str(publication.get("updated_at") or indexed.get("updated_at") or "").strip(),
            }
        )
    records.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return records


def _mapping_response_for_publication(
    response: Any,
    publication: dict[str, Any],
) -> dict[str, Any]:
    """严格校验官方 mapping 单元素数组并投影身份字段。"""

    if not isinstance(response, list):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_RESPONSE_INVALID: mapping 响应必须是顶层数组"
        )
    if len(response) != 1:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_CARDINALITY_INVALID: "
            "mapping 响应必须且只能包含一个对象"
        )
    if not isinstance(response[0], dict):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_RESPONSE_INVALID: mapping 数组元素必须是对象"
        )

    body = dict(response[0])
    expected_siteless_id = canonicalize_mercadolibre_siteless_user_product_id(
        publication.get("siteless_user_product_id")
    )
    if not re.fullmatch(r"U\d+", expected_siteless_id):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_LOCAL_ID_INVALID: "
            "本地 publication 缺少合法的 U{id} Siteless 身份"
        )
    raw_remote_id = str(body.get("siteless_user_product_id") or "").strip()
    if not raw_remote_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_MISSING: "
            "mapping 响应缺少 siteless_user_product_id"
        )
    remote_siteless_id = canonicalize_mercadolibre_siteless_user_product_id(
        raw_remote_id
    )
    if not re.fullmatch(r"U\d+", remote_siteless_id):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_INVALID: "
            "mapping 响应的 Siteless 身份格式无效"
        )
    if remote_siteless_id != expected_siteless_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_MISMATCH: "
            f"expected={expected_siteless_id}, actual={remote_siteless_id}"
        )

    expected_owner_id = str(publication.get("account_user_id") or "").strip()
    remote_owner_id = str(body.get("owner_id") or "").strip()
    if not remote_owner_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_OWNER_ID_MISSING: mapping 响应缺少 owner_id"
        )
    if not expected_owner_id or remote_owner_id != expected_owner_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_OWNER_ID_MISMATCH: "
            f"expected={expected_owner_id or '<missing>'}, actual={remote_owner_id}"
        )

    parent_item_id = str(body.get("item_id") or "").strip()
    parent_user_product_id = str(body.get("user_product_id") or "").strip()
    if not parent_item_id or not parent_user_product_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_PARENT_ID_MISSING: "
            "mapping 响应缺少 CBT parent item/user-product 身份"
        )
    if (
        canonicalize_mercadolibre_siteless_user_product_id(
            parent_user_product_id
        )
        != expected_siteless_id
    ):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_PARENT_ID_MISMATCH: "
            "mapping 的 CBT parent_user_product_id 与 Siteless 身份不一致"
        )

    raw_markets = body.get("site_items")
    if not isinstance(raw_markets, list):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITE_ITEMS_INVALID: "
            "mapping 响应的 site_items 必须是数组"
        )
    site_items: list[dict[str, Any]] = []
    for raw in raw_markets:
        if not isinstance(raw, dict):
            raise RuntimeError(
                "MERCADOLIBRE_MAPPING_SITE_ITEMS_INVALID: "
                "mapping site_items 元素必须是对象"
            )
        site_id = str(raw.get("site_id") or "").strip().upper()
        item_id = str(raw.get("item_id") or "").strip()
        if not site_id or site_id == "CBT" or not item_id:
            raise RuntimeError(
                "MERCADOLIBRE_MAPPING_SITE_ITEM_IDENTITY_INVALID: "
                "mapping 子市场缺少合法的 site_id/item_id"
            )
        site_items.append(
            {
                **raw,
                "site_id": site_id,
                "item_id": item_id,
            }
        )
    return {
        **body,
        "account_user_id": remote_owner_id,
        "parent_item_id": parent_item_id,
        "parent_user_product_id": parent_user_product_id,
        "siteless_user_product_id": remote_siteless_id,
        "site_items": site_items,
    }


def _public_mercadolibre_user_product_row(
    record: dict[str, Any],
) -> dict[str, Any]:
    publication = normalize_mercadolibre_publication(record.get("publication"))
    return {
        "product_id": str(record.get("product_id") or "").strip(),
        "draft_id": str(record.get("draft_id") or "").strip(),
        "title": str(record.get("title") or "").strip(),
        "thumbnail": str(record.get("main_image") or "").strip(),
        **publication,
    }


def _persist_mercadolibre_publication(
    draft_id: str,
    publication: dict[str, Any],
) -> dict[str, Any]:
    products = get_context().products
    product = products.load_draft_from_index(draft_id)
    product.pop("current_draft_id", None)
    product.pop("current_draft_platform", None)
    product_id = str(product.get("product_id") or "").strip()
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get("mercadolibre") if isinstance(drafts.get("mercadolibre"), dict) else {}
    if not product_id or not draft or str(draft.get("draft_id") or "").strip() != draft_id:
        raise RuntimeError(f"Mercado Libre publication 关联草稿不存在：{draft_id}")
    draft["publication"] = normalize_mercadolibre_publication(publication)
    drafts["mercadolibre"] = draft
    product["drafts"] = drafts
    saved = products.save_product(product)
    saved_drafts = saved.get("drafts") if isinstance(saved.get("drafts"), dict) else {}
    saved_draft = saved_drafts.get("mercadolibre") if isinstance(saved_drafts.get("mercadolibre"), dict) else {}
    return normalize_mercadolibre_publication(saved_draft.get("publication"))


def _refresh_mercadolibre_user_product(
    record: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    publication = normalize_mercadolibre_publication(record.get("publication"))
    siteless_id = str(publication.get("siteless_user_product_id") or "").strip()
    response = publisher.request_json(
        "GET",
        "https://api.mercadolibre.com/marketplace/user-products/"
        f"{urllib.parse.quote(siteless_id, safe='')}/mapping",
        token,
    )
    merged = mercadolibre_publication_from_response(
        _mapping_response_for_publication(response, publication),
        existing=publication,
        family_name=str(publication.get("family_name") or record.get("title") or ""),
        updated_at=collect_time_iso(),
    )
    # mapping 端点只证明 ID/operation 关系，不返回权威状态、售价或刊登类型。
    # 因此只接受身份字段；已有事实原样保留，新发现映射保持未知状态。
    previous_markets = [
        dict(item)
        for item in publication.get("markets", [])
        if isinstance(item, dict)
    ]
    identity_markets: list[dict[str, Any]] = []
    for market in merged.get("markets", []):
        if not isinstance(market, dict):
            continue
        item_id = str(market.get("item_id") or "").strip()
        site_id = str(market.get("site_id") or "").strip().upper()
        logistic_type = str(
            market.get("logistic_type") or ""
        ).strip().lower()
        previous = next(
            (
                item
                for item in previous_markets
                if (
                    item_id
                    and str(item.get("item_id") or "").strip() == item_id
                )
                or (
                    str(item.get("site_id") or "").strip().upper() == site_id
                    and str(item.get("logistic_type") or "").strip().lower()
                    == logistic_type
                )
            ),
            {},
        )
        identity = {
            key: value
            for key, value in market.items()
            if key
            in {
                "site_id",
                "seller_id",
                "logistic_type",
                "item_id",
                "user_product_id",
            }
        }
        for key in (
            "status",
            "currency_id",
            "listing_type_id",
            "price",
            "net_proceeds",
            "free_shipping",
            "sale_terms",
            "error",
            "last_operation",
            "updated_at",
        ):
            if key in previous:
                identity[key] = previous[key]
        identity_markets.append(identity)
    merged = normalize_mercadolibre_publication(
        {
            **merged,
            "family_name": publication.get("family_name"),
            "status": publication.get("status"),
            "updated_at": publication.get("updated_at"),
            "markets": identity_markets,
        }
    )
    record["publication"] = _persist_mercadolibre_publication(
        str(record.get("draft_id") or ""),
        merged,
    )
    record["updated_at"] = str(record["publication"].get("updated_at") or "")
    return record


def mercadolibre_user_products(
    status: str = "all",
    page: int = 1,
    per_page: int = 50,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """按本地 Siteless publication 查询 Mercado User Products。"""

    records = _local_mercadolibre_user_product_records()
    refresh_errors: list[dict[str, str]] = []
    if refresh and records:
        config = get_context().config.load_store_config()
        try:
            token = get_mercadolibre_access_token(config)
        except PublishAdapterError as exc:
            return {
                "ok": False,
                "error": str(exc) or "Mercado Libre 授权不可用",
                "error_code": str(
                    exc.details.get("auth_error_code") or exc.code
                ),
                "next_action": str(
                    exc.details.get("next_action") or "请先完成授权测试"
                ),
            }
        current_account_user_id = str(
            (config.get("mercadolibre") or {}).get("user_id") or ""
        ).strip()
        for record in records:
            try:
                publication = normalize_mercadolibre_publication(
                    record.get("publication")
                )
                publication_account_user_id = str(
                    publication.get("account_user_id") or ""
                ).strip()
                if (
                    not publication_account_user_id
                    or publication_account_user_id != current_account_user_id
                ):
                    raise RuntimeError(
                        "MERCADOLIBRE_PUBLICATION_ACCOUNT_MISMATCH: "
                        "本地 User Product 不属于当前 CBT 父账号"
                    )
                _refresh_mercadolibre_user_product(record, token)
            except Exception as exc:
                refresh_errors.append(
                    {
                        "siteless_user_product_id": str(
                            (record.get("publication") or {}).get("siteless_user_product_id")
                            if isinstance(record.get("publication"), dict)
                            else ""
                        ),
                        "error": str(exc),
                    }
                )

    wanted = str(status or "all").strip().lower()
    if wanted not in {
        "active",
        "paused",
        "closed",
        "failed",
        "partial",
        "all",
    }:
        wanted = "all"
    if wanted != "all":
        records = [
            record
            for record in records
            if str((record.get("publication") or {}).get("status") or "").strip().lower() == wanted
        ]
    page_size = max(1, min(int(per_page or 50), 100))
    current_page = max(1, int(page or 1))
    offset = (current_page - 1) * page_size
    total = len(records)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "ok": True,
        "platform": "mercadolibre",
        "status": wanted,
        "items": [
            _public_mercadolibre_user_product_row(record)
            for record in records[offset:offset + page_size]
        ],
        "pagination": {
            "page": current_page,
            "per_page": page_size,
            "offset": offset,
            "total": total,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": offset + page_size < total,
        },
        "refresh_errors": refresh_errors,
        "refresh_scope": "identity_mapping_only" if refresh else "local_snapshot",
        "checked_at": collect_time_iso(),
    }


def _mercadolibre_partial_update_rows(
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """展开 206 响应里的市场/变体结果，同时保留其响应路径。"""

    results: list[dict[str, Any]] = []
    nested_keys = ("listing_sites", "site_items", "variants")

    def collect(values: Any, path: str) -> None:
        if not isinstance(values, list):
            return
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                continue
            field = f"{path}[{index}]"
            results.append({**raw, "_response_path": field})
            for key in nested_keys:
                collect(raw.get(key), f"{field}.{key}")

    for key in nested_keys:
        collect(body.get(key), key)
    return results


def _mercadolibre_update_row_failed(row: dict[str, Any]) -> bool:
    return (
        row.get("success") is False
        or row.get("error") not in (None, "", [], {})
        or row.get("errors") not in (None, "", [], {})
    )


def _mercadolibre_update_row_confirmed_paused(
    row: dict[str, Any],
) -> bool:
    if _mercadolibre_update_row_failed(row):
        return False
    return row.get("success") is True or str(
        row.get("status") or ""
    ).strip().lower() == "paused"


def _mercadolibre_update_row_error(row: dict[str, Any]) -> Any:
    return (
        row.get("error")
        or row.get("errors")
        or {
            "code": "MERCADOLIBRE_PAUSE_UNCONFIRMED",
            "message": "Mercado Libre 未确认该市场已暂停",
            "response_path": str(row.get("_response_path") or ""),
        }
    )


def _mercadolibre_market_matches_update_row(
    market: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    market_ids = {
        str(market.get("item_id") or "").strip(),
        str(market.get("user_product_id") or "").strip(),
    }
    row_ids = {
        str(row.get("id") or "").strip(),
        str(row.get("item_id") or "").strip(),
        str(row.get("user_product_id") or "").strip(),
    }
    if (market_ids - {""}) & (row_ids - {""}):
        return True
    market_site = str(market.get("site_id") or "").strip().upper()
    row_site = str(row.get("site_id") or "").strip().upper()
    if not market_site or market_site != row_site:
        return False
    market_logistic = str(
        market.get("logistic_type") or ""
    ).strip().lower()
    row_logistic = str(row.get("logistic_type") or "").strip().lower()
    return not market_logistic or not row_logistic or market_logistic == row_logistic


def _mercadolibre_pause_root_error(body: dict[str, Any]) -> Any:
    if body.get("error") not in (None, "", [], {}):
        return body.get("error")
    if body.get("errors") not in (None, "", [], {}):
        return body.get("errors")
    if body.get("success") is False:
        return body.get("message") or "Mercado Libre 返回 success=false"
    return None


def _active_mercadolibre_publish_job(
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """只读复用 publish-job 状态，避免 publish 与 pause 并发写同一草稿。"""

    product_id = str(record.get("product_id") or "").strip()
    draft_id = str(record.get("draft_id") or "").strip()
    states, _next_cursor = get_context().db.list_publish_jobs(
        limit=100,
        platform="mercadolibre",
        product_id=product_id,
    )
    active_statuses = {
        "pending",
        "queued",
        "running",
        "retrying",
        "completed",
        "outcome_unknown",
    }
    for state in states:
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "").strip().lower()
        if status not in active_statuses:
            continue
        if (
            status == "completed"
            and state.get("terminal_results_persisted") is True
        ):
            continue
        platforms = (
            state.get("platforms")
            if isinstance(state.get("platforms"), dict)
            else {}
        )
        platform_state = (
            platforms.get("mercadolibre")
            if isinstance(platforms.get("mercadolibre"), dict)
            else {}
        )
        bound_draft_id = str(
            state.get("draft_id")
            or platform_state.get("draft_id")
            or ""
        ).strip()
        if bound_draft_id == draft_id:
            return state
    return None


def _mercadolibre_pause_outcome_unknown(
    siteless_id: str,
    publication: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    details = (
        dict(exc.details)
        if isinstance(exc, PublishAdapterError)
        else {}
    )
    details.update(
        {
            "outcome_unknown": True,
            "remote_write_dispatched": True,
        }
    )
    return {
        "ok": False,
        "platform": "mercadolibre",
        "siteless_user_product_id": siteless_id,
        "status": str(publication.get("status") or ""),
        "error_code": "USER_PRODUCT_PAUSE_OUTCOME_UNKNOWN",
        "error": (
            "暂停请求已经发出，但网络或 Mercado 服务端失败使结果无法确认："
            f"{exc}"
        ),
        "outcome_unknown": True,
        "retryable": False,
        "details": details,
    }


def _mercadolibre_pause_error_is_outcome_unknown(
    exc: PublishAdapterError,
) -> bool:
    details = dict(exc.details)
    if details.get("outcome_unknown") is True:
        return True
    try:
        status_code = int(details.get("http_status") or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code >= 500 or status_code in {408, 423, 425}:
        return True
    code = str(exc.code or "").strip().upper()
    return status_code == 0 and any(
        marker in code for marker in ("NETWORK", "TIMEOUT", "SERVER_ERROR")
    )


def mercadolibre_pause_user_product(
    siteless_user_product_id: str,
) -> dict[str, Any]:
    """暂停一个已持久化的 Siteless User Product。"""

    siteless_id = canonicalize_mercadolibre_siteless_user_product_id(
        siteless_user_product_id
    )
    if not siteless_id:
        return {
            "ok": False,
            "error": "缺少 siteless_user_product_id",
            "error_code": "SITELESS_USER_PRODUCT_ID_MISSING",
        }
    if not re.fullmatch(r"U\d+", siteless_id):
        return {
            "ok": False,
            "error": "siteless_user_product_id 必须使用官方 U{id} 或 CBTU{id} 格式",
            "error_code": "MERCADOLIBRE_SITELESS_USER_PRODUCT_ID_INVALID",
        }
    record = next(
        (
            item
            for item in _local_mercadolibre_user_product_records()
            if canonicalize_mercadolibre_siteless_user_product_id(
                (item.get("publication") or {}).get(
                    "siteless_user_product_id"
                )
                if isinstance(item.get("publication"), dict)
                else ""
            )
            == siteless_id
        ),
        None,
    )
    if record is None:
        return {
            "ok": False,
            "error": "本地不存在该 Mercado Siteless User Product",
            "error_code": "MERCADOLIBRE_USER_PRODUCT_NOT_FOUND",
        }
    publication = normalize_mercadolibre_publication(record.get("publication"))
    if str(publication.get("status") or "").strip().lower() == "paused":
        return {
            "ok": True,
            "platform": "mercadolibre",
            "siteless_user_product_id": siteless_id,
            "status": "paused",
            "user_product": _public_mercadolibre_user_product_row(record),
            "message": f"{siteless_id} 已处于暂停状态。",
        }
    config = get_context().config.load_store_config()
    try:
        token = get_mercadolibre_access_token(config)
    except PublishAdapterError as exc:
        return {
            "ok": False,
            "error": str(exc) or "Mercado Libre 授权不可用",
            "error_code": str(
                exc.details.get("auth_error_code") or exc.code
            ),
            "next_action": str(
                exc.details.get("next_action") or "请先完成授权测试"
            ),
        }
    current_account_user_id = str(
        (config.get("mercadolibre") or {}).get("user_id") or ""
    ).strip()
    publication_account_user_id = str(
        publication.get("account_user_id") or ""
    ).strip()
    if not publication_account_user_id:
        return {
            "ok": False,
            "error": "该 Siteless User Product 缺少 CBT 父账号归属，已阻止暂停",
            "error_code": "MERCADOLIBRE_PUBLICATION_ACCOUNT_MISSING",
        }
    if (
        not current_account_user_id
        or publication_account_user_id != current_account_user_id
    ):
        return {
            "ok": False,
            "error": "该 Siteless User Product 不属于当前授权的 CBT 父账号",
            "error_code": "MERCADOLIBRE_PUBLICATION_ACCOUNT_MISMATCH",
        }
    active_job = _active_mercadolibre_publish_job(record)
    if active_job is not None:
        return {
            "ok": False,
            "platform": "mercadolibre",
            "siteless_user_product_id": siteless_id,
            "status": str(publication.get("status") or ""),
            "error_code": "MERCADOLIBRE_USER_PRODUCT_PUBLISH_ACTIVE",
            "error": (
                "同一草稿仍有发布任务处于活动或结果未知状态，"
                "完成对账前不能并发暂停"
            ),
            "active_job_id": str(active_job.get("job_id") or ""),
            "active_job_status": str(active_job.get("status") or ""),
            "next_action": "等待发布任务完成；outcome_unknown 需先人工对账",
        }
    try:
        response = publisher.request_json(
            "PUT",
            "https://api.mercadolibre.com/global/user-products/"
            f"{urllib.parse.quote(siteless_id, safe='')}",
            token,
            {"status": "paused"},
        )
    except PublishAdapterError as exc:
        if _mercadolibre_pause_error_is_outcome_unknown(exc):
            return _mercadolibre_pause_outcome_unknown(
                siteless_id,
                publication,
                exc,
            )
        return {
            "ok": False,
            "platform": "mercadolibre",
            "siteless_user_product_id": siteless_id,
            "status": str(publication.get("status") or ""),
            "error_code": exc.code,
            "error": str(exc),
            "retryable": bool(exc.retryable),
        }
    except Exception as exc:
        return _mercadolibre_pause_outcome_unknown(
            siteless_id,
            publication,
            exc,
        )
    body = response if isinstance(response, dict) else {}
    returned_id = str(
        body.get("id") or body.get("siteless_user_product_id") or ""
    ).strip()
    canonical_returned_id = (
        canonicalize_mercadolibre_siteless_user_product_id(returned_id)
    )
    if canonical_returned_id != siteless_id:
        return {
            "ok": False,
            "platform": "mercadolibre",
            "siteless_user_product_id": siteless_id,
            "status": str(publication.get("status") or ""),
            "error_code": "USER_PRODUCT_PAUSE_OUTCOME_UNKNOWN",
            "error": (
                "暂停写请求响应缺少匹配的 Siteless User Product ID，"
                "远端结果无法确认"
            ),
            "outcome_unknown": True,
            "retryable": False,
            "details": {
                "outcome_unknown": True,
                "remote_write_dispatched": True,
                "expected_siteless_user_product_id": siteless_id,
                "returned_siteless_user_product_id": canonical_returned_id,
            },
            "raw": _sanitize_for_log(body),
        }

    now = collect_time_iso()
    update_rows = _mercadolibre_partial_update_rows(body)
    root_error = _mercadolibre_pause_root_error(body)
    failed_rows = [
        row for row in update_rows
        if _mercadolibre_update_row_failed(row)
    ]
    markets: list[dict[str, Any]] = []
    confirmed_count = 0
    unconfirmed_count = 0
    matched_row_ids: set[int] = set()
    previous_markets = [
        dict(market)
        for market in publication.get("markets", [])
        if isinstance(market, dict)
    ]
    partial_response = bool(update_rows or root_error)
    for market in previous_markets:
        matched_rows = [
            row
            for row in update_rows
            if _mercadolibre_market_matches_update_row(market, row)
        ]
        matched_row_ids.update(id(row) for row in matched_rows)
        matching_failure = next(
            (
                row
                for row in matched_rows
                if _mercadolibre_update_row_failed(row)
            ),
            None,
        )
        matching_success = next(
            (
                row
                for row in matched_rows
                if _mercadolibre_update_row_confirmed_paused(row)
            ),
            None,
        )
        if matching_failure is not None:
            operation_error = _mercadolibre_update_row_error(
                matching_failure
            )
            markets.append(
                {
                    **market,
                    "error": operation_error,
                    "last_operation": {
                        "status": "failed",
                        "error": operation_error,
                        "updated_at": now,
                    },
                }
            )
            unconfirmed_count += 1
            continue
        if matching_success is not None:
            confirmed = {
                **market,
                "status": "paused",
                "updated_at": now,
                "last_operation": {
                    "status": "succeeded",
                    "updated_at": now,
                },
            }
            confirmed.pop("error", None)
            markets.append(confirmed)
            confirmed_count += 1
            continue
        if partial_response:
            operation_error = root_error or {
                "code": "MERCADOLIBRE_PAUSE_UNCONFIRMED",
                "message": "Mercado Libre 响应未确认该市场已暂停",
            }
            markets.append(
                {
                    **market,
                    "error": operation_error,
                    "last_operation": {
                        "status": "failed",
                        "error": operation_error,
                        "updated_at": now,
                    },
                }
            )
            unconfirmed_count += 1
            continue
        confirmed = {
            **market,
            "status": "paused",
            "updated_at": now,
            "last_operation": {
                "status": "succeeded",
                "updated_at": now,
            },
        }
        confirmed.pop("error", None)
        markets.append(confirmed)
        confirmed_count += 1

    unmatched_failures = [
        row for row in failed_rows
        if id(row) not in matched_row_ids
    ]
    fully_confirmed = (
        bool(previous_markets)
        and confirmed_count == len(previous_markets)
        and not root_error
        and not unmatched_failures
    )
    aggregate_status = (
        "paused"
        if fully_confirmed
        else "partial"
        if confirmed_count
        else str(publication.get("status") or "")
    )
    merged = normalize_mercadolibre_publication(
        {
            **publication,
            "siteless_user_product_id": siteless_id,
            "status": aggregate_status,
            "markets": markets,
            "updated_at": now,
        }
    )
    saved = _persist_mercadolibre_publication(str(record.get("draft_id") or ""), merged)
    if not fully_confirmed:
        partial_errors = [
            _mercadolibre_update_row_error(row)
            for row in failed_rows
        ]
        if root_error:
            partial_errors.insert(0, root_error)
        if not partial_errors:
            partial_errors.append(
                {
                    "code": "MERCADOLIBRE_PAUSE_UNCONFIRMED",
                    "message": "Mercado Libre 未确认全部市场已暂停",
                }
            )
        return {
            "ok": False,
            "platform": "mercadolibre",
            "siteless_user_product_id": siteless_id,
            "status": aggregate_status,
            "partial": confirmed_count > 0,
            "confirmed_market_count": confirmed_count,
            "unconfirmed_market_count": unconfirmed_count,
            "error_code": "MERCADOLIBRE_USER_PRODUCT_PAUSE_FAILED",
            "error": str(partial_errors),
            "user_product": _public_mercadolibre_user_product_row(
                {**record, "publication": saved}
            ),
            "raw": _sanitize_for_log(body),
        }
    return {
        "ok": True,
        "platform": "mercadolibre",
        "siteless_user_product_id": siteless_id,
        "status": "paused",
        "user_product": _public_mercadolibre_user_product_row(
            {**record, "publication": saved}
        ),
        "raw": _sanitize_for_log(body),
        "message": f"{siteless_id} 已提交暂停。",
    }


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
    # 此处是 Mercado 上传服务写入 picture ID 的唯一可信路径；通用保存入口
    # 会保护已有平台事实，故明确允许本次服务端结果替换上传状态。
    saved = get_context().products.save_product(
        normalized,
        preserve_platform_facts=False,
    )
    errors = compact_precheck_items(errors)
    return {"ok": not errors, "product": saved, "picture_refs": picture_refs, "errors": errors}


def mercadolibre_product_for_payload(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    draft = _draft_for_selected_target(normalized, "mercadolibre")
    normalized.setdefault("drafts", {})["mercadolibre"] = draft
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    normalized["category_id"] = str(draft.get("category_id") or "").strip()
    normalized["attributes"] = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    normalized["brand"] = str(draft.get("brand") or normalized.get("brand") or "Generic").strip()
    normalized["model"] = str(draft.get("model") or normalized.get("model") or "General").strip()
    normalized["sku"] = str(draft.get("sku") or normalized.get("sku") or "").strip()
    normalized["upc"] = str(draft.get("upc") or "").strip()
    normalized["name"] = str(draft.get("title") or normalized.get("name") or "").strip()
    normalized["weight_kg"] = str(pkg.get("weight_kg") or normalized.get("weight_kg") or "").strip()
    normalized["dimensions"] = " x ".join(str(pkg.get(key) or "").strip() for key in ("length_cm", "width_cm", "height_cm") if str(pkg.get(key) or "").strip())
    return normalized


def build_mercadolibre_payload_preview(product: dict[str, Any], config: dict[str, Any], picture_refs: list[str] | None = None) -> dict[str, Any]:
    refs = picture_refs if picture_refs is not None else image_pool_refs_for_platform(product, "mercadolibre")
    payload_product = mercadolibre_product_for_payload(product)
    if picture_refs is not None:
        payload_product.setdefault("source", {})["image_pool"] = []
    from .publish_context import prepare_publish_context

    prepared_context = prepare_publish_context(payload_product, "mercadolibre")
    if prepared_context.category_definition is None:
        raise RuntimeError(
            prepared_context.definition_error
            or "Mercado Libre 类目属性定义暂时不可用"
        )
    return build_mercadolibre_publish_payload(
        payload_product,
        config,
        refs,
        category_definition=prepared_context.category_definition,
    )


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
        "sale_terms": "请检查 Warranty type / Warranty time 等保修条款。",
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


__all__ = [
    "build_mercadolibre_payload_preview",
    "ensure_mercadolibre_pictures_uploaded",
    "map_mercadolibre_publish_error",
    "mercadolibre_pause_user_product",
    "mercadolibre_user_products",
    "run_mercadolibre_07d_test",
]
