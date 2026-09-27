"""在线商品领域装配；服务通过注入使用平台适配器。"""
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_ozon import OzonOnlineAdapter
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.services.online_product_service import OnlineProductService


def create_online_product_service(context, *, start_worker=True):
    return OnlineProductService(context, adapter_factories={
        "mercadolibre": MercadoOnlineAdapter,
        "ozon": OzonOnlineAdapter,
        "yandex": YandexOnlineAdapter,
    }, start_worker=start_worker)
