"""核价回归使用明确的官方响应替身；不依赖真实账号或网络。"""
from decimal import Decimal


def official_commission(common, target, price):
    platform = target.get("platform", "mercadolibre")
    # 旧算术用例中的比例现在表示官方响应替身的费率，不再是业务请求参数。
    percent = target.get("commission_percent", 20 if platform in {"yandex", "ozon"} else 16)
    endpoints = {"yandex": "/v2/tariffs/calculate", "ozon": "/mock/ozon/prelisting", "mercadolibre": "/sites/MLM/listing_prices"}
    return {"percent": str(percent), "evidence": {
        "source": "platform_api", "purpose": "prelisting", "platform": platform, "endpoint": endpoints[platform],
        "category_id": str(target.get("category_id") or ""), "account_id": "test",
        "price": dict(price), "amount": str(Decimal(price["amount"]) * Decimal(str(percent)) / 100),
        "currency": price["currency"], "quoted_at": "2026-10-18T00:00:00Z",
    }}


def ogl_basis():
    """发布边界测试的 OGL 依据占位；费用数值由 test_ogl_pricing 独立验证。"""
    from erp_web.international_shipping.tariff_store import default_snapshot
    return {'shipping_evidence': {'source': 'ogl_tariff', 'tariff_version': default_snapshot()['version']},
            'electronic_service': {'rate': '.02', 'fixed_cny': '0', 'rub_per_cny': '12'},
            'shipping_quote_mode': 'auto'}
