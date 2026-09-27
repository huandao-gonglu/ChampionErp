"""在线目录占位与同步中断规则，不包含平台请求或任务编排。"""
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.online_products import Capability, OnlineListing, listing_identity


def catalog_listing(platform, account_id, remote_id, *, model, seller_sku=""):
    return OnlineListing(id=listing_identity(platform, account_id, remote_id), platform=platform,
        account_id=account_id, remote_id=remote_id, model=model, seller_sku=seller_sku, details_state="pending",
        capabilities={name: Capability(reason="商品详情正在同步") for name in ("price", "stock", "content", "sale_state")})


def raise_if_access_blocked(error):
    """账号拒绝或限流后停止新请求，不把整店重复请求当作失败重试。"""
    if isinstance(error, PublishAdapterError) and error.details.get("http_status") in (401, 403, 420, 429):
        raise error
