"""单件在线状态读取：限定已知商品身份，不扫描目录，不读取价格和库存接口。"""
from urllib.parse import quote

from erp_web.marketplaces.mercadolibre_mapping import validate_user_product_mapping
from erp_web.product_model import canonicalize_mercadolibre_siteless_user_product_id
from erp_web.runtime_units.online_ozon_read import index_rows as ozon_rows
from erp_web.runtime_units.online_yandex_read import catalog_pages, read_batch
from erp_web.schemas.online_products import MarketStatus, OnlineStatus


def yandex_status(adapter, listing):
    remote_id = listing.remote_id

    mappings = [row for page in catalog_pages(adapter, [remote_id]) for row in page]
    if not mappings:
        raise ValueError("Yandex 平台未找到该商品，可能已在商家后台删除，请同步目录清理；已保留原记录")
    if len(mappings) != 1 or mappings[0]["offer"]["offerId"] != remote_id:
        raise ValueError("Yandex 返回的商品身份异常，无法确认归档状态，已保留原记录")
    archived = mappings[0]["offer"].get("archived", False)
    if type(archived) is not bool:
        raise ValueError("Yandex 商品归档状态无效，已保留原记录")
    result = read_batch(adapter, mappings, {}, scope="status")
    if result.errors or not result.listings or not result.listings[0].raw_status:
        raise ValueError(result.errors.get(remote_id) or "Yandex 未返回该商品的店铺状态，已保留原记录")
    fresh = result.listings[0]
    return OnlineStatus(remote_id=remote_id, raw_status=fresh.raw_status,
        sale_state=fresh.sale_state, archived=archived, raw_sub_status=fresh.raw_sub_status,
        platform_issues=fresh.platform_issues,
        markets=[MarketStatus(id=market.id, name=market.name, raw_status=market.raw_status or 'unknown',
                              raw_sub_status=market.raw_sub_status) for market in fresh.markets],
        warehouses=fresh.snapshot.get('warehouse_catalog', []),
        hidden_campaigns=fresh.snapshot.get('hidden_campaigns', {}))


def ozon_status(adapter, listing):
    remote_id = listing.remote_id
    response = adapter.request("/v3/product/info/list", {"product_id": [int(remote_id)]})
    rows = ozon_rows(response.get("items") if isinstance(response, dict) else None, "id", allowed={remote_id})
    if remote_id not in rows:
        raise ValueError("Ozon 未返回该商品的状态，已保留原记录")
    info = rows[remote_id]
    status = "archived" if info.get("is_archived") else str(info.get("statuses", {}).get("status") or "")
    return OnlineStatus(remote_id=remote_id, raw_status=status, sale_state="unknown")


def mercado_status(adapter, listing):
    remote_id = listing.remote_id
    parent = adapter.get("/marketplace/items/" + quote(remote_id, safe=""))
    if not isinstance(parent, dict) or parent.get("id") != remote_id or str(parent.get("seller_id")) != adapter.account_id or parent.get("site_id") != "CBT":
        raise ValueError("Mercado 商品身份与当前全局账号不一致")
    refs = parent.get("marketplace_items") or []
    up_id = canonicalize_mercadolibre_siteless_user_product_id(parent.get("user_product_id"))
    if up_id:
        try:
            mapping = validate_user_product_mapping(adapter.get(f"/marketplace/user-products/{quote(up_id, safe='')}/mapping"),
                {"siteless_user_product_id": up_id, "account_user_id": adapter.account_id})
        except RuntimeError as exc:
            raise ValueError(f"Mercado 商品映射校验失败：{exc}") from None
        if mapping["parent_item_id"] != remote_id:
            raise ValueError("Mercado mapping 父商品身份不一致")
        refs = mapping["site_items"]
    if not isinstance(refs, list):
        raise ValueError("Mercado 市场映射不是数组")
    indexed = {}
    for ref in refs:
        item_id = str(ref.get("item_id") or "") if isinstance(ref, dict) else ""
        if not item_id or item_id in indexed or ref.get("parent_id", remote_id) != remote_id or str(ref.get("parent_user_id", adapter.account_id)) != adapter.account_id:
            raise ValueError("Mercado 市场映射身份无效")
        indexed[item_id] = ref
    if set(indexed) != {market.id for market in listing.markets}:
        raise ValueError("商品关联站点已变化，请同步店铺商品以更新完整信息")
    markets = []
    for item_id, ref in indexed.items():
        child = adapter.get("/marketplace/items/" + quote(item_id, safe=""))
        if not isinstance(child, dict) or child.get("id") != item_id or child.get("site_id") != ref.get("site_id"):
            raise ValueError("Mercado 市场商品身份不一致")
        if ref.get("user_id") is not None and str(child.get("seller_id")) != str(ref["user_id"]):
            raise ValueError("Mercado 市场卖家身份不一致")
        if child.get("cbt_item_id") and child["cbt_item_id"] != remote_id:
            raise ValueError("Mercado 市场详情父商品身份不一致")
        markets.append(MarketStatus(id=item_id, raw_status=str(child.get("status") or ""), raw_sub_status=child.get("sub_status") or []))
    status = str(parent.get("status") or "")
    return OnlineStatus(remote_id=remote_id, raw_status=status, sale_state=status,
        raw_sub_status=parent.get("sub_status") or [], markets=markets)
