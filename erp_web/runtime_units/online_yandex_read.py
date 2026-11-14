"""Yandex 只读同步：目录分页先行，详情按 100 个 SKU 分批，最多 3 个接口并发。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from threading import Event

from erp_web.marketplaces.online_sync import raise_if_access_blocked
from typing import Any, Iterator, Literal

from erp_web.runtime_units.online_yandex_snapshot import build_listing, build_stocks, catalog_listing, with_warehouses
from erp_web.schemas.online_products import Capability, OnlineSyncBatch

BATCH_SIZE = 100
MAX_REQUESTS = 3


def pages(adapter, path, key, body=None, *, query=None, method="POST"):
    """完整消费分页；身份校验由调用方负责，缺失数组或循环游标不能算同步完成。"""
    query = dict(query or {})
    tokens = set()
    while True:
        response = adapter.request(path, body, query=query or None, method=method)
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get(key), list):
            raise ValueError(f"Yandex 响应缺少 {key}，未完成读取")
        yield result[key]
        token = str(result.get("paging", {}).get("nextPageToken") or "")
        if not token:
            return
        if token in tokens:
            raise ValueError("Yandex 分页游标重复，未完成读取")
        tokens.add(token)
        query["pageToken"] = token


def index_rows(rows, *, allowed=None, nested=False):
    indexed = {}
    for row in rows:
        item = row.get("offer", {}) if nested and isinstance(row, dict) else row
        remote_id = item.get("offerId") if isinstance(item, dict) else None
        if not isinstance(remote_id, str) or not remote_id or remote_id in indexed or allowed is not None and remote_id not in allowed:
            raise ValueError("Yandex 响应商品身份缺失、重复或超出请求范围")
        indexed[remote_id] = row
    return indexed


def catalog_pages(adapter, ids: list[str] | None = None, *, catalog: str | None = None) -> Iterator[list[dict[str, Any]]]:
    path = f"/v2/businesses/{adapter.business}/offer-mappings"
    seen = set()
    bodies = [{"offerIds": ids[i:i + BATCH_SIZE]} for i in range(0, len(ids), BATCH_SIZE)] if ids is not None else [{"archived": catalog == "archived"}]
    for body in bodies:
        for rows in pages(adapter, path, "offerMappings", body, query=None if ids is not None else {"limit": BATCH_SIZE}):
            indexed = index_rows(rows, allowed=set(ids) if ids is not None else None, nested=True)
            # 指定 SKU 时官方禁止同时传 archived；仅在本地核对原同步目录，再读取详情。
            fresh = [row for key, row in indexed.items() if key not in seen
                     and (catalog is None or (row["offer"].get("archived") is True) == (catalog == "archived"))]
            seen.update(indexed)
            yield fresh


def hidden_ids(adapter, remote_id: str = "", *, campaign_id: str = "") -> set[str]:
    result = set()
    query = {"offer_id": remote_id} if remote_id else {"limit": 500}
    for rows in pages(adapter, f"/v2/campaigns/{campaign_id or adapter.campaign}/hidden-offers", "hiddenOffers", query=query, method="GET"):
        result.update(index_rows(rows, allowed={remote_id} if remote_id else None))
    return result


def stock_result(adapter, path, body):
    """指定 SKU 的库存应完整返回；缺页、仓库或商品身份漂移均不能覆盖快照。"""
    result = adapter.request(path, body).get("result", {})
    if not isinstance(result, dict) or result.get("paging", {}).get("nextPageToken"):
        raise ValueError("Yandex 指定 SKU 的库存返回不完整")
    if "partnerWarehouseId" in body:
        if str(result.get("partnerWarehouseId")) != str(body["partnerWarehouseId"]):
            raise ValueError("Yandex 库存仓库身份不一致")
        warehouses = [result]
    else:
        warehouses = result.get("warehouses")
    if not isinstance(warehouses, list):
        raise ValueError("Yandex 库存响应缺少仓库列表")
    for warehouse in warehouses:
        if not isinstance(warehouse, dict) or not isinstance(warehouse.get("offers"), list):
            raise ValueError("Yandex 库存响应缺少商品列表")
        index_rows(warehouse["offers"], allowed=set(body["offerIds"]))
    if "partnerWarehouseId" not in body:
        ids = [warehouse.get("warehouseId") for warehouse in warehouses]
        if any(not wid for wid in ids) or len(ids) != len(set(ids)):
            raise ValueError("Yandex 库存仓库身份缺失或重复")
    return result


def account_prices(adapter, ids):
    path = f"/v2/businesses/{adapter.business}/offer-prices"
    return index_rows([row for page in pages(adapter, path, "offers", {"offerIds": ids}) for row in page], allowed=set(ids))


def stock_requests(adapter, ids):
    requests = {}
    if adapter.mode == "business":
        for warehouse in adapter.warehouses:
            wid = str(warehouse["id"])
            requests["stock:" + wid] = lambda wid=wid: stock_result(adapter,
                f"/v3/businesses/{adapter.business}/offers/stocks", {"partnerWarehouseId": int(wid), "offerIds": ids})
    elif adapter.mode in ("campaign_warehouses", "platform_warehouses"):
        requests["stock:campaign"] = lambda: stock_result(adapter,
            f"/v2/campaigns/{adapter.campaign}/offers/stocks", {"offerIds": ids})
    return requests


def request_results(requests):
    """每个已声明接口只执行一次；复用有限并发及访问受阻时的取消边界。"""
    responses, errors = {}, {}
    stopped = Event()
    def run(request):
        if stopped.is_set():
            raise RuntimeError("本批次已因访问受阻而停止")
        try:
            return request()
        except Exception as exc:
            try:
                raise_if_access_blocked(exc)
            except Exception:
                stopped.set()
                raise
            raise

    with ThreadPoolExecutor(max_workers=MAX_REQUESTS, thread_name_prefix="yandex-sync-read") as pool:
        futures = {pool.submit(copy_context().run, run, request): name for name, request in requests.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                responses[name] = future.result()
            except Exception as exc:
                if stopped.is_set():
                    for pending in futures:
                        pending.cancel()
                raise_if_access_blocked(exc)
                label = {"campaign": "店铺状态", "hidden": "店铺隐藏状态", "cards": "商品卡片", "prices": "价格"}.get(name.split(':', 1)[0], "库存")
                errors[name] = f"{label}：{exc}"
    return responses, errors


def list_prices(adapter, mappings, campaign_ids, responses):
    """复用同批目录与店铺响应，只为缺少账号基础价的 SKU 发起补查。"""
    prices, missing = {}, []
    for mapping in mappings:
        sku = mapping["offer"]["offerId"]
        offers = [mapping["offer"]]
        offers.extend(responses["campaign:" + cid].get(sku, {}) for cid in sorted(campaign_ids))
        price = next((offer["basicPrice"] for offer in offers
                      if (offer.get("basicPrice") or {}).get("value") is not None), None)
        if price is None:
            missing.append(sku)
        else:
            prices[sku] = price
    if not missing:
        return prices, {}
    try:
        for sku, row in account_prices(adapter, missing).items():
            prices[sku] = row.get("price") or {}
    except Exception as exc:
        raise_if_access_blocked(exc)
        return prices, {"prices": f"价格：{exc}"}
    return prices, {}


def read_batch(adapter, mappings, hidden, *, scope: Literal["details", "list", "sales", "status"] = "details") -> OnlineSyncBatch:
    """同批复用店铺商品响应；页面销售刷新跳过目录、卡片、隐藏清单和属性。"""
    full = scope == "details"
    ids = [row["offer"]["offerId"] for row in mappings]

    def rows(path, key):
        return index_rows([row for page in pages(adapter, path, key, {"offerIds": ids}) for row in page], allowed=set(ids))

    warehouses = adapter.warehouses if adapter.mode == 'business' else []
    campaign_ids = {str(model['campaignId']) for warehouse in warehouses for model in warehouse.get('models', [])
                    if model.get('placementType') == 'FBS' and model.get('campaignId')} | {adapter.campaign}
    hidden = hidden if isinstance(hidden, dict) else {adapter.campaign: hidden}
    requests = {}
    if scope != "sales":
        requests["cards"] = lambda: rows(f"/v2/businesses/{adapter.business}/offer-cards", "offerCards")
    for campaign_id in sorted(campaign_ids):
        requests['campaign:' + campaign_id] = lambda cid=campaign_id: rows(f'/v2/campaigns/{cid}/offers', 'offers')
        if scope != "sales" and campaign_id not in hidden:
            requests['hidden:' + campaign_id] = lambda cid=campaign_id: hidden_ids(adapter, ids[0] if len(ids) == 1 else '', campaign_id=cid)
    if scope != "status":
        requests.update(stock_requests(adapter, ids))
    if full:
        requests["prices"] = lambda: account_prices(adapter, ids)
    responses, errors = request_results(requests)
    result = OnlineSyncBatch("details" if full else "basic")
    blocking = [error for key, error in errors.items() if full or not key.startswith("stock:")]
    if blocking:
        result.errors = {remote_id: "；".join(blocking) for remote_id in ids}
        return result
    prices = {sku: row.get("price") or {} for sku, row in responses.get("prices", {}).items()}
    if scope in ("list", "sales"):
        prices, price_errors = list_prices(adapter, mappings, campaign_ids, responses)
        errors.update(price_errors)
    stocks = {name.removeprefix("stock:"): value for name, value in responses.items() if name.startswith("stock:")}
    hidden = {**hidden, **{name.removeprefix('hidden:'): value for name, value in responses.items() if name.startswith('hidden:')}}
    # 列表只展示平台返回的币种与金额，只有管理详情需要账号级修改设置。
    settings = adapter.settings if full else {}
    for mapping in mappings:
        remote_id = mapping["offer"]["offerId"]
        try:
            campaigns = {cid: responses['campaign:' + cid].get(remote_id, {}) for cid in campaign_ids}
            if scope == "sales":
                card = {}
            elif remote_id in responses["cards"]:
                card = responses["cards"][remote_id]
            else:
                raise ValueError("平台未返回该商品的卡片信息，已保留原记录")
            names, names_error = {}, ""
            if full and card.get("parameterValues"):
                category_id = str(mapping["offer"].get("marketCategoryId") or
                                  card.get("mapping", {}).get("marketCategoryId") or
                                  mapping.get("mapping", {}).get("marketCategoryId") or "")
                if category_id:
                    names, names_error = adapter.attribute_names(category_id)
                else:
                    names_error = "平台未返回类目编号，暂时无法关联属性名称"
            listing = build_listing(mapping, campaigns[adapter.campaign], remote_id in hidden.get(adapter.campaign, set()),
                card, prices.get(remote_id, {}),
                build_stocks(adapter.mode, warehouses, stocks, remote_id) if scope != "status" else [],
                account_id=adapter.account_id, campaign_id=adapter.campaign, settings=settings,
                attribute_names=names, attribute_names_error=names_error)
            with_warehouses(listing, warehouses, campaigns, hidden, settings=settings)
            if not full:
                listing.details_state = "unloaded"
                for operation in ("price", "stock", "content"):
                    listing.capabilities[operation] = Capability(reason="打开商品详情后核验当前资料")
            if scope != "status":
                for price in listing.prices:
                    if price.id == "business" and remote_id not in prices:
                        price.read_error = errors.get("prices", "平台未返回该 SKU 的账号基础价")
                    elif price.id != "business":
                        cid = adapter.campaign if price.id == "campaign" else price.id.removeprefix("campaign:")
                        if not campaigns.get(cid):
                            price.read_error = "平台未返回该 SKU 的店铺商品记录"
                for stock in listing.stocks:
                    if stock.quantity is None:
                        stock.read_error = errors.get("stock:" + stock.id, "平台未返回可确认的可售库存")
            result.listings.append(listing)
        except Exception as exc:
            result.errors[remote_id] = str(exc)
    return result


def sync_yandex(adapter, ids=None, *, catalog="current", full=False) -> Iterator[OnlineSyncBatch]:
    if catalog not in ("current", "archived") and not (catalog is None and ids is not None):
        raise ValueError("同步目录只能为 current 或 archived")
    discovered, hidden = set(), None
    phase = "details" if full else "basic"
    for page in catalog_pages(adapter, ids, catalog=catalog):
        discovered.update(row["offer"]["offerId"] for row in page)
        yield OnlineSyncBatch("catalog", [catalog_listing(row, account_id=adapter.account_id,
            campaign_id=adapter.campaign) for row in page])
        if page:
            if hidden is None:
                campaigns = {str(model['campaignId']) for warehouse in (adapter.warehouses if adapter.mode == 'business' else [])
                             for model in warehouse.get('models', []) if model.get('placementType') == 'FBS' and model.get('campaignId')} | {adapter.campaign}
                hidden = {cid: hidden_ids(adapter, campaign_id=cid) for cid in sorted(campaigns)}
            yield read_batch(adapter, page, hidden, scope="details" if full else "list")
    yield OnlineSyncBatch(phase, discovery_complete=True)
    if ids is not None:
        missing = set(ids) - discovered
        if missing:
            yield OnlineSyncBatch(phase, errors={sku: "Yandex 未返回该 SKU 的所选目录记录，商品可能已移出该目录" for sku in sorted(missing)})
