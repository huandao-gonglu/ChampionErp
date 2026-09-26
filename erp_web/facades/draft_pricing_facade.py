"""草稿核价 HTTP 适配；与 AI 使用同一个业务入口。"""
from typing import Any
from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.runtime_units.draft_pricing import price_draft
from erp_web.schemas.draft_pricing import DraftPricingHttpRequest
from erp_web.services.capability_errors import BusinessCapabilityError


def price_draft_payload(body: dict[str, Any], *, apply: bool) -> tuple[dict[str, Any], int]:
    try:
        request = DraftPricingHttpRequest.model_validate(body)
        products = get_context().products
        result = price_draft(request, product_store=products, apply=apply)
        if result["applied"]:
            result.update(productsIndex=products.load_products_index(), draftsIndex=products.load_drafts_index())
        return result, 200
    except ValidationError as exc:
        return {"ok": False, "error_code": "PRICING_INPUT_INVALID", "error": "核价参数格式不正确。",
                "errors": exc.errors(include_context=False, include_url=False)}, 400
    except BusinessCapabilityError as exc:
        return {"ok": False, "error_code": exc.code, "error": str(exc), **dict(exc.details or {})}, 409 if exc.code == "DRAFT_CHANGED" else 400


__all__ = ["price_draft_payload"]
