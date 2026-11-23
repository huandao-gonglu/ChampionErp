from __future__ import annotations

"""类目 HTTP payload 适配器。

本模块负责请求字段归一化、领域调用编排和稳定 HTTP 响应组装。平台差异由
CategoryProvider 注册表处理；facade 不直接访问文件、网络或 SQLite。
"""

from typing import Any

from erp_web.context import get_context
from erp_web.facades.category_match_facade import match_category
from erp_web.product_model import validate_category_precheck
from erp_web.services.forbidden_category_service import forbidden_category_issue
from erp_web.services.operation_availability import require_model_platform
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.runtime_units.category_store import (
    fetch_category_attribute_page,
    fetch_category_attribute_values,
    fetch_category_record,
    search_categories_live,
)
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
    save_draft_target_listing_result,
)
Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]
CategorySubject = tuple[
    Payload,
    Payload | None,
    str,
    str,
    Payload | None,
    int,
]


def _platform(body: Payload) -> str:
    return str(body.get("platform") or "mercadolibre").strip().lower()


def _site(body: Payload) -> str:
    return str(
        body.get("site") or body.get("site_id") or body.get("country") or ""
    ).strip()


def _category_error(exc: Exception) -> ResponseWithStatus:
    return {
        "ok": False,
        "error": str(exc),
        "error_code": "CATEGORY_LIVE_API_FAILED",
        "next_action": "请确认目标站点、类目 ID，以及 Mercado Libre 或 Ozon 的授权信息可用后重试。",
    }, 400


def _load_category_subject(
    body: Payload,
    *,
    site: str | None = None,
) -> CategorySubject:
    platform = _platform(body)
    selected_site = _site(body) if site is None else site
    if body.get("draft_id") or body.get("draftId"):
        context, error, status = load_required_draft_publish_context(body)
        if error:
            return {}, None, platform, selected_site, error, status
        return (
            context["product"],
            context,
            str(context["platform"]),
            str(context.get("site") or selected_site),
            None,
            200,
        )
    products = get_context().products
    product, error, status = (
        products.load_required_product_from_body(body)
    )
    return product, None, platform, selected_site, error, status


def _category_record(
    body: Payload,
    platform: str,
    site: str,
) -> tuple[Payload, ResponseWithStatus | None]:
    supplied = body.get("category_record")
    if isinstance(supplied, dict):
        return supplied, None
    try:
        return fetch_category_record(
            platform,
            str(body.get("category_id") or "").strip(),
            site=site,
            include_attributes=True,
        ), None
    except Exception as exc:
        return {}, _category_error(exc)


def _category_path(record: Payload) -> str:
    value = record.get("category_path")
    if isinstance(value, str) and value.strip():
        return value.strip()
    path = (
        record.get("path_original")
        if isinstance(record.get("path_original"), list)
        else []
    )
    return " / ".join(str(item).strip() for item in path if str(item).strip())


def category_attrs_payload(body: Payload) -> ResponseWithStatus:
    platform = _platform(body)
    site = _site(body)
    category_id = str(body.get("category_id") or "").strip()
    if not category_id:
        return {
            "ok": False,
            "error": "缺少 category_id",
            "error_code": "CATEGORY_ID_REQUIRED",
        }, 400
    try:
        limit = int(body.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    try:
        return (
            fetch_category_attribute_page(
                platform,
                category_id,
                site=site,
                cursor=str(body.get("cursor") or "").strip(),
                limit=limit,
            ),
            200,
        )
    except Exception as exc:
        return _category_error(exc)


def category_attribute_values_payload(body: Payload) -> ResponseWithStatus:
    platform = _platform(body)
    site = _site(body)
    category_id = str(body.get("category_id") or "").strip()
    attribute_id = str(body.get("attribute_id") or "").strip()
    if not category_id or not attribute_id:
        return {
            "ok": False,
            "error": "缺少 category_id 或 attribute_id",
            "error_code": "CATEGORY_ATTRIBUTE_VALUE_INPUT_REQUIRED",
        }, 400
    try:
        return (
            fetch_category_attribute_values(
                platform,
                category_id,
                attribute_id,
                site=site,
                query=str(body.get("query") or "").strip(),
                cursor=str(body.get("cursor") or "").strip(),
                limit=int(body.get("limit") or 50),
            ),
            200,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "CATEGORY_QUERY_INVALID"}, 400
    except Exception as exc:
        return _category_error(exc)


def category_search_payload(body: Payload) -> ResponseWithStatus:
    platform = _platform(body)
    site = str(body.get("site") or body.get("country") or "").strip()
    query = str(body.get("query") or body.get("keyword") or "").strip()
    limit = int(body.get("limit") or 20)
    try:
        results = search_categories_live(
            platform,
            query=query,
            site=site,
            limit=limit,
        )
    except Exception as exc:
        return _category_error(exc)
    return {
        "ok": True,
        "platform": platform,
        "site": site,
        "query": query,
        "source": f"{platform}_live",
        "results": results,
    }, 200


def load_category_match_subject(
    body: Payload,
) -> tuple[Payload, Payload, Payload, ResponseWithStatus | None]:
    """解析类目匹配 subject：返回 ``(product, draft, target, error)``。

    单目标请求与批量请求共用同一草稿和目标校验。
    """

    product, context, platform, site, error, status = _load_category_subject(body)
    if error:
        return {}, {}, {}, (error, status)
    draft = (
        context.get("draft")
        if isinstance(context, dict) and isinstance(context.get("draft"), dict)
        else (
            (product.get("drafts") or {}).get(platform)
            if isinstance(product.get("drafts"), dict)
            else {}
        )
    )
    target = {
        "platform": platform,
        "site": site,
        "language": str(body.get("language") or body.get("locale") or "").strip(),
    }
    return product, draft or {}, target, None


def category_match_payload(body: Payload) -> ResponseWithStatus:
    """同步匹配一个目标站点，保留候选、证据及终检结果。"""
    product, draft, target, error = load_category_match_subject(body)
    if error is not None:
        return error
    require_model_platform(get_context().config.load_app_config(), "category.product_match")
    result = match_category(product, draft, target)
    return dict(result), 200


def category_match_batch_payload(body: Payload) -> ResponseWithStatus:
    raw_targets = body["targets"]
    if not 1 <= len(raw_targets) <= 100 or any(not isinstance(target, dict) for target in raw_targets):
        return {"ok": False, "error": "请选择 1 到 100 个类目匹配目标。"}, 400
    targets = [
        validate_request_payload(
            {"draft_id": body["draft_id"], **{key: target.get(key, "") for key in ("platform", "site", "language")}},
            endpoint="/api/v1/category-match",
        )
        for target in raw_targets
    ]
    items = []
    for target in targets:
        check_cancellation()
        try:
            result, _ = category_match_payload(target)
        except RequestValidationError as exc:
            result = {"ok": False, "error": str(exc), "error_code": exc.error_code}
        if not result.get("ok") and "failure" not in result and result.get("error"):
            result = {**result, "status": "failed", "failure": {
                "code": result.get("error_code", "CATEGORY_MATCH_FAILED"),
                "message": result["error"], "stage": "request", "retryable": False,
            }}
        items.append({
            "target": {key: target[key] for key in ("platform", "site")},
            **result,
        })
    return {"ok": any(item.get("ok") for item in items), "items": items}, 200


def category_precheck_payload(body: Payload) -> ResponseWithStatus:
    category_id = str(body.get("category_id") or "").strip()
    product, context, platform, site, error, status = _load_category_subject(body)
    if error:
        return error, status
    supplied_record = isinstance(body.get("category_record"), dict)
    record, record_error = _category_record(body, platform, site)
    if record_error:
        return record_error
    missing_fields = validate_category_precheck(product, platform, record)
    if platform == "yandex":
        settings = get_context().config.forbidden_categories()
        categories = {item["category_id"]: item["name"] for item in settings["categories"]}
        draft_category_id = str(product.get("drafts", {}).get(platform, {}).get("category_id") or "")
        trusted_record_ancestors = (
            tuple(str(item).strip() for item in (record.get("ancestor_ids") or ()) if str(item).strip())
            if not supplied_record and isinstance(record, dict)
            else ()
        )
        for checked_id in dict.fromkeys((draft_category_id, category_id)):
            ancestors = (
                trusted_record_ancestors
                if checked_id == category_id and trusted_record_ancestors
                else get_context().category_catalog.cached_category_ancestor_ids(platform, checked_id)
            )
            if issue := forbidden_category_issue(
                platform, checked_id, categories, ancestor_ids=ancestors,
            ):
                missing_fields.append(issue["message"])
        missing_fields = list(dict.fromkeys(missing_fields))
    category_precheck = {
        "ok": not missing_fields,
        "platform": platform,
        "site": site,
        "category_id": category_id,
        "category_path": _category_path(record),
        "missing_fields": missing_fields,
    }
    mutation = {}
    if context is not None:
        mutation = save_draft_target_listing_result(
            context,
            {
                "category_precheck": category_precheck,
                "validation_errors": [
                    field.removeprefix("attributes.")
                    for field in missing_fields if field.startswith("attributes.")
                ],
            },
        )
    return {
        **mutation,
        "ok": True,
        "platform": platform,
        "site": site,
        "category_id": category_id,
        "category_path": category_precheck["category_path"],
        "missing_fields": missing_fields,
    }, 200


__all__ = [
    "category_attrs_payload",
    "category_match_payload",
    "category_match_batch_payload",
    "category_precheck_payload",
    "category_search_payload",
    "load_category_match_subject",
]
