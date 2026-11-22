"""订单采购装配；复用已授权店铺身份和唯一订单领域库。"""

import logging

from erp_web.services.alibaba_authorization_service import alibaba_runtime_config
from erp_web.context import get_context
from erp_web.services.alibaba_purchase_sync_service import sync_purchase as sync_alibaba_purchase
from erp_web.services.alibaba_purchase_assignment import supported
from erp_web.schemas.order_procurement import PurchaseRecord
from erp_web.schemas.orders import configured_accounts
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from erp_web.runtime_units.publish_confirmation import resolve_publish_store_binding
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.services.sku_procurement_service import SkuProcurementService, yandex_business_identity
from erp_web.stores.order_procurement_store import OrderProcurementStore
from erp_web.stores.online_product_store import OnlineProductStore
from erp_web.services.alibaba_purchase_query_service import query_purchase as query_alibaba_purchase


def _stable_account_identity(config: dict, platform: str, account_id: str = "") -> str:
    platform_key = str(platform or "").strip().lower()
    if platform_key not in {"mercadolibre", "ozon"}:
        return ""
    value = str(account_id or "").strip() or str(configured_accounts(config).get(platform_key) or "").strip()
    if not value:
        store = config.get(platform_key) if isinstance(config.get(platform_key), dict) else {}
        if platform_key == "mercadolibre":
            value = str(store.get("user_id") or store.get("seller_id") or "").strip()
        elif platform_key == "ozon":
            value = str(store.get("client_id") or store.get("seller_id") or "").strip()
    return f"{platform_key}:acc:{value}" if value else ""


def _store_identities(config: dict, platform: str, account_id: str = "") -> tuple[str, ...]:
    identities: list[str] = []
    platform_key = str(platform or "").strip().lower()
    if platform_key == "yandex":
        yandex_cfg = dict(config.get("yandex") or {})
        business_id = str(yandex_cfg.get("business_id") or "").strip()
        campaign_id = str(yandex_cfg.get("campaign_id") or "").strip()
        if account_id:
            if ":" in account_id:
                acc_biz, _, acc_camp = account_id.partition(":")
                business_id = acc_biz.strip() or business_id
                campaign_id = acc_camp.strip() or campaign_id
            else:
                campaign_id = account_id.strip()
        if business_id and campaign_id:
            try:
                identities.append(
                    resolve_publish_store_binding(
                        "yandex",
                        {**config, "yandex": {**yandex_cfg, "business_id": business_id, "campaign_id": campaign_id}},
                    ).identity
                )
            except ValueError:
                pass
        biz_identity = yandex_business_identity(business_id)
        if biz_identity and biz_identity not in identities:
            identities.append(biz_identity)
        return tuple(identities)
    # 兼容历史发布哈希标识与稳定的原生账号标识，避免重授权补全字段时货源映射失联。
    try:
        legacy_hash = resolve_publish_store_binding(platform_key, config).identity
        if legacy_hash:
            identities.append(legacy_hash)
    except ValueError:
        pass
    stable_acc = _stable_account_identity(config, platform_key, account_id)
    if stable_acc and stable_acc not in identities:
        identities.append(stable_acc)
    return tuple(identities)


def _write_store_identity(config: dict, platform: str, account_id: str = "") -> str:
    platform_key = str(platform or "").strip().lower()
    if platform_key == "yandex":
        yandex_cfg = dict(config.get("yandex") or {})
        business_id = str(yandex_cfg.get("business_id") or "").strip()
        if account_id and ":" in account_id:
            acc_biz, _, _ = account_id.partition(":")
            business_id = acc_biz.strip() or business_id
        biz_identity = yandex_business_identity(business_id)
        if biz_identity:
            return biz_identity
    stable_acc = _stable_account_identity(config, platform_key, account_id)
    if stable_acc:
        return stable_acc
    identities = _store_identities(config, platform_key, account_id)
    return identities[0] if identities else ""


def create_service(context):
    orders = context.order_notifications
    store = OrderProcurementStore(orders.store)
    # 仅在首次启用采购库时一次性回填历史发布任务，后续新建上下文不再全表扫描 publish_jobs。
    if not store.meta("publish_jobs_backfilled_v1"):
        cursor = ""
        while True:
            jobs, cursor = context.db.list_publish_jobs(limit=100, cursor=cursor)
            for job in jobs:
                store.add_bindings(bindings_from_publish_job(job))
            if not cursor:
                break
        store.set_meta("publish_jobs_backfilled_v1", "1")

    def identity(platform, account_id=""):
        identities = _store_identities(context.config.load_store_config(), platform, account_id)
        return identities[0] if identities else ""

    def identities(platform, account_id=""):
        return _store_identities(context.config.load_store_config(), platform, account_id)

    online = OnlineProductStore(context.db)

    def yandex_images(account_id, offer_ids):
        config = context.config.load_store_config().get("yandex") or {}
        business = str(config.get("business_id") or "")
        from erp_web.schemas.orders import account_in_scope
        if not business or not account_in_scope(orders.accounts(), "yandex", account_id):
            return {}
        # 在线目录缓存以企业和所选发布店铺标识；商品图片本身属于企业商品。
        campaign = str(config.get("campaign_id") or "")
        return online.thumbnails("yandex", f"{business}:{campaign}", offer_ids)

    return OrderProcurementService(
        store,
        orders.accounts,
        identity,
        yandex_images_provider=yandex_images,
        identities_provider=identities,
    )


def detail(order_id=""):
    context = get_context()
    result = context.order_procurement.detail(order_id)
    order = result["order"]
    order["shop_name"] = next((shop.name for shop in context.order_notifications.shops()
                               if shop.platform == order["platform"] and shop.account_id == order["account_id"]),
                              order["account_id"])
    return result


def query_purchase(body, *, context=None):
    context = context or get_context()
    return query_alibaba_purchase(
        context.order_procurement, alibaba_runtime_config(context.config), body,
    )


def sync_purchase(body, *, once=False, context=None):
    context = context or get_context()
    return sync_alibaba_purchase(context.order_procurement, context.fulfillment,
                                lambda: alibaba_runtime_config(context.config), body, once=once)


def command(action, body, *, context=None):
    context = context or get_context()
    service = context.order_procurement
    result = {
        "select-source": service.select_source,
        "record-purchase": service.record_purchase,
        "cancel-purchase": service.cancel_purchase,
    }[action](body)
    if action == "record-purchase":
        record = next((r for r in result["purchases"] if r["request_id"] == body["request_id"]), None)
        if record and supported(PurchaseRecord.model_validate(record)):
            try:
                return sync_purchase({"order_id": body["order_id"], "record_id": record["id"]}, once=True, context=context)
            except Exception:
                logging.getLogger(__name__).error("采购登记已保存，自动同步未完成")
                return service.detail(body["order_id"])
    return result


def create_sku_procurement_service(context, *, client_factory=None):
    kwargs = {}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    return SkuProcurementService(
        context.order_procurement,
        config_provider=lambda: alibaba_runtime_config(context.config),
        read_identities_provider=lambda platform, account_id="": _store_identities(
            context.config.load_store_config(), platform, account_id
        ),
        write_identity_provider=lambda platform, account_id="": _write_store_identity(
            context.config.load_store_config(), platform, account_id
        ),
        owner_scope_provider=lambda: str(context.paths.data_dir),
        **kwargs,
    )


def sku_source_command(action, body, *, context=None):
    context = context or get_context()
    service = create_sku_procurement_service(context)
    result = {
        "resolve-1688": service.resolve_1688,
        "query": service.query_sources,
        "bind": service.bind_source,
        "set-default": service.set_default,
        "remove": service.remove_source,
        "link-master": service.link_master_sku,
    }[action](body)
    if isinstance(result.get("order_detail"), dict):
        order = result["order_detail"]["order"]
        order["shop_name"] = next(
            (
                shop.name
                for shop in context.order_notifications.shops()
                if shop.platform == order["platform"] and shop.account_id == order["account_id"]
            ),
            order["account_id"],
        )
    return result
