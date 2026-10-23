"""核价页面的模板配置和汇率查询适配。"""
from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.runtime_units.pricing_runtime import fetch_pricing_exchange_rates


def pricing_templates_payload() -> tuple[dict, int]:
    return {"ok": True, **get_context().config.load_app_config()["pricing_templates"]}, 200


def mutate_pricing_template_payload(body: dict, operation: str) -> tuple[dict, int]:
    try:
        return {"ok": True, **get_context().config.mutate_pricing_template(operation, body)}, 200
    except ValidationError as exc:
        return {"ok": False, "error": "核价模板参数不正确，请检查名称、费用、定价与汇率。",
                "error_code": "PRICING_TEMPLATE_INVALID", "errors": exc.errors(include_context=False, include_url=False)}, 400
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "PRICING_TEMPLATE_INVALID"}, 400


def pricing_exchange_rates_payload(force_refresh: bool = False) -> tuple[dict, int]:
    result = fetch_pricing_exchange_rates(force_refresh=force_refresh)
    public = {key: result[key] for key in ("ok", "rates", "source", "fetched_at", "cached", "stale", "error") if key in result}
    return public, 200 if result.get("ok") else 502


def yandex_warehouses_payload(*, refresh: bool = False, body: dict | None = None) -> tuple[dict, int]:
    try:
        service = get_context().yandex_logistics
        return (service.save(body) if body is not None else service.detail(refresh)), 200
    except (ValidationError, ValueError) as exc:
        return {'ok': False, 'error': str(exc), 'error_code': 'YANDEX_WAREHOUSE_CONFIG_INVALID'}, 400


__all__ = ["yandex_warehouses_payload", "pricing_templates_payload", "mutate_pricing_template_payload", "pricing_exchange_rates_payload"]
