from __future__ import annotations

"""类目 HTTP payload 适配器。

本模块负责请求字段归一化、领域调用编排和稳定 HTTP 响应组装。平台差异由
CategoryProvider 注册表处理；facade 不直接访问文件、网络或 SQLite。
"""

from typing import Any

from erp_web.context import get_context
from erp_web.facades.category_match_facade import match_category
from erp_web.product_model import validate_category_precheck
from erp_web.runtime_units.category_attribute_ai_fill import (
    apply_ai_model_attribute_fill,
)
from erp_web.runtime_units.category_store import (
    fetch_category_attribute_values,
    fetch_category_attributes,
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
        return fetch_category_attributes(platform, category_id, site=site), 200
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
        return fetch_category_attribute_values(
            platform,
            category_id,
            attribute_id,
            site=site,
            query=str(body.get("query") or "").strip(),
            limit=int(body.get("limit") or 50),
        ), 200
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

    旧同步 endpoint 删除后，focused start endpoint 继续用同一合同读取
    商品/草稿与目标站点；``error`` 非空时不得继续启动运行。
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


def _draft_fill_payload(
    context: Payload,
    updated: Payload,
    platform: str,
    meta: Payload,
) -> Payload:
    drafts = updated.get("drafts") if isinstance(updated.get("drafts"), dict) else {}
    updated_draft = (
        drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    )
    updated_draft = {
        **updated_draft,
        "category_precheck": {},
        "last_precheck": {},
        "last_precheck_target": {},
    }
    saved = save_draft_target_listing_result(context, updated_draft)
    saved_draft = saved.get("draft", {})
    return {
        "ok": True,
        "fill_source": meta.get("source"),
        "warning": meta.get("warning", ""),
        "ai_filled": meta.get("ai_filled", []),
        "draft": saved_draft,
        "productContext": saved.get("productContext"),
        "productsIndex": saved.get("productsIndex", []),
        "draftsIndex": saved.get("draftsIndex", []),
        "attributes": updated_draft.get("attributes", {}),
        "need_review": updated_draft.get("validation_errors", []),
    }


def _product_fill_payload(
    updated: Payload,
    platform: str,
    meta: Payload,
) -> Payload:
    products = get_context().products
    saved = products.save_product(updated)
    drafts = saved.get("drafts") if isinstance(saved.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    return {
        "ok": True,
        "fill_source": meta.get("source"),
        "warning": meta.get("warning", ""),
        "ai_filled": meta.get("ai_filled", []),
        "product": saved,
        "draft": draft,
        "attributes": draft.get("attributes", {}),
        "need_review": draft.get("validation_errors", []),
    }


def category_ai_fill_payload(body: Payload) -> ResponseWithStatus:
    product, context, platform, site, error, status = _load_category_subject(body)
    if error:
        return error, status
    record, record_error = _category_record(body, platform, site)
    if record_error:
        return record_error
    updated, meta = apply_ai_model_attribute_fill(product, platform, record)
    if context is not None:
        return _draft_fill_payload(context, updated, platform, meta), 200
    return _product_fill_payload(updated, platform, meta), 200


def category_match_payload(body: Payload) -> ResponseWithStatus:
    """同步 focused 类目匹配唯一入口：业务控制与类型化结果。

    复用 ``match_category()`` 共享编排（输入校验、检索装配、Agent 运行、
    业务终检），同步返回当前类型化 ``CategoryMatchResult``。业务判断型失败
    保留 ``ok=false`` 的类型化结果（HTTP 200），由 caller 读取
    failure/status；实时展示由 HTTP 公共边界的 presentation 上下文自动
    关联，本 facade 不感知 SSE/registry/presentation。
    """

    product, draft, target, error = load_category_match_subject(body)
    if error is not None:
        return error
    result = match_category(product, draft, target)
    return dict(result), 200


def category_precheck_payload(body: Payload) -> ResponseWithStatus:
    category_id = str(body.get("category_id") or "").strip()
    product, _, platform, site, error, status = _load_category_subject(body)
    if error:
        return error, status
    record, record_error = _category_record(body, platform, site)
    if record_error:
        return record_error
    missing_fields = validate_category_precheck(product, platform, record)
    return {
        "ok": True,
        "platform": platform,
        "site": site,
        "category_id": category_id,
        "category_path": _category_path(record),
        "category_record": record,
        "missing_fields": missing_fields,
    }, 200


__all__ = [
    "category_ai_fill_payload",
    "category_attrs_payload",
    "category_match_payload",
    "category_precheck_payload",
    "category_search_payload",
    "load_category_match_subject",
]
