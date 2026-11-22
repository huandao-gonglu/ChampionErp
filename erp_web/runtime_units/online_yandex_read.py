"""Yandex 只读同步：目录按 100 分页，详情按 200 合批，仓库库存跨页按 100 合批，单桶最多 4 并发。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from threading import Event

from erp_web.marketplaces.online_sync import raise_if_access_blocked
from typing import Any, Iterator, Literal

from erp_web.runtime_units.online_yandex_snapshot import available_stock, build_listing, build_stocks, catalog_listing, with_warehouses
from erp_web.schemas.online_products import Capability, OnlineListing, OnlineSyncBatch

CATALOG_PAGE_SIZE = 100
DETAIL_BATCH_SIZE = 200
STOCK_BATCH_SIZE = 100
BATCH_SIZE = CATALOG_PAGE_SIZE
MAX_REQUESTS = 4
HOT_MAX_CAMPAIGN_WORKERS = 8
HOT_WINDOW_CHUNKS = 3
MAX_PENDING_STOCK_LISTINGS = 500


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
    bodies = [{"offerIds": ids[i:i + CATALOG_PAGE_SIZE]} for i in range(0, len(ids), CATALOG_PAGE_SIZE)] if ids is not None else [{"archived": catalog == "archived"}]
    for body in bodies:
        for rows in pages(adapter, path, "offerMappings", body, query=None if ids is not None else {"limit": CATALOG_PAGE_SIZE}):
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


def _chunked_stock_result(adapter, path: str, base_body: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """库存接口单页上限为 100 个 SKU；超限时按 100 分片查询并合并同一仓库范围。"""
    if len(ids) <= STOCK_BATCH_SIZE:
        return stock_result(adapter, path, {**base_body, "offerIds": ids})
    if "partnerWarehouseId" in base_body:
        merged_offers: list[dict[str, Any]] = []
        for offset in range(0, len(ids), STOCK_BATCH_SIZE):
            part = stock_result(adapter, path, {**base_body, "offerIds": ids[offset:offset + STOCK_BATCH_SIZE]})
            merged_offers.extend(part["offers"])
        return {"partnerWarehouseId": base_body["partnerWarehouseId"], "offers": merged_offers}
    by_warehouse: dict[Any, dict[str, Any]] = {}
    for offset in range(0, len(ids), STOCK_BATCH_SIZE):
        part = stock_result(adapter, path, {**base_body, "offerIds": ids[offset:offset + STOCK_BATCH_SIZE]})
        for warehouse in part["warehouses"]:
            wid = warehouse["warehouseId"]
            if wid not in by_warehouse:
                by_warehouse[wid] = {**warehouse, "offers": list(warehouse["offers"])}
            else:
                by_warehouse[wid]["offers"].extend(warehouse["offers"])
    return {"warehouses": list(by_warehouse.values())}


def account_prices(adapter, ids):
    path = f"/v2/businesses/{adapter.business}/offer-prices"
    rows = []
    for offset in range(0, len(ids), DETAIL_BATCH_SIZE):
        chunk = ids[offset:offset + DETAIL_BATCH_SIZE]
        rows.extend(row for page in pages(adapter, path, "offers", {"offerIds": chunk}) for row in page)
    return index_rows(rows, allowed=set(ids))


def stock_requests(adapter, ids):
    requests = {}
    if adapter.mode == "business":
        for warehouse in adapter.warehouses:
            wid = str(warehouse["id"])
            requests["stock:" + wid] = lambda wid=wid: _chunked_stock_result(
                adapter, f"/v3/businesses/{adapter.business}/offers/stocks",
                {"partnerWarehouseId": int(wid)}, ids,
            )
    elif adapter.mode in ("campaign_warehouses", "platform_warehouses"):
        requests["stock:campaign"] = lambda: _chunked_stock_result(
            adapter, f"/v2/campaigns/{adapter.campaign}/offers/stocks", {}, ids,
        )
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


def _all_hidden_ids(adapter, campaign_ids: set[str]) -> dict[str, set[str]]:
    """多仓店铺并发读取隐藏清单，沿用有限并发与访问受阻停止边界。"""
    ordered = sorted(campaign_ids)
    if len(ordered) <= 1:
        return {cid: hidden_ids(adapter, campaign_id=cid) for cid in ordered}
    responses, errors = request_results({
        "hidden:" + cid: (lambda cid=cid: hidden_ids(adapter, campaign_id=cid))
        for cid in ordered
    })
    if errors:
        raise RuntimeError("；".join(errors.values()))
    return {cid: responses["hidden:" + cid] for cid in ordered}


def _needed_warehouse_skus(warehouses, ids, responses, *, skip_no_stocks: bool) -> dict[str, list[str]]:
    """按仓库筛出实际需要查询库存的 SKU；跳过已确认 NO_STOCKS 或未加入该店铺的 SKU。"""
    needed: dict[str, list[str]] = {}
    for warehouse in warehouses:
        wid = str(warehouse["id"])
        target_ids = list(ids)
        if skip_no_stocks:
            cids = {str(m["campaignId"]) for m in warehouse.get("models", [])
                    if m.get("placementType") == "FBS" and m.get("campaignId")}
            if len(cids) == 1:
                cid = next(iter(cids))
                if ("campaign:" + cid) in responses:
                    camp_rows = responses["campaign:" + cid]
                    target_ids = [
                        sku for sku in ids
                        if sku in camp_rows and camp_rows[sku].get("status") != "NO_STOCKS"
                    ]
        if target_ids:
            needed[wid] = target_ids
    return needed


def _warehouse_stock_requests(adapter, warehouses, ids, responses, *, skip_no_stocks: bool):
    needed = _needed_warehouse_skus(warehouses, ids, responses, skip_no_stocks=skip_no_stocks)
    return {
        "stock:" + wid: (lambda wid=wid, target_ids=target_ids: _chunked_stock_result(
            adapter, f"/v3/businesses/{adapter.business}/offers/stocks",
            {"partnerWarehouseId": int(wid)}, target_ids,
        ))
        for wid, target_ids in needed.items()
    }


class _BusinessStockAccumulator:
    """跨目录页按仓库攒批读取库存（每仓满 100 即发，尾批随同步结束刷新），控制稀疏仓库请求数并保持内存有界。"""

    def __init__(self, adapter, warehouses):
        self.adapter = adapter
        self.warehouse_ids = [str(w["id"]) for w in warehouses]
        self.queues: dict[str, list[str]] = {wid: [] for wid in self.warehouse_ids}
        self.pending_listings: dict[str, OnlineListing] = {}
        self.pending_warehouses: dict[str, set[str]] = {}

    def add(self, listings: list[OnlineListing], needed_by_wh: dict[str, list[str]]) -> list[OnlineListing]:
        built_ids = {listing.remote_id for listing in listings}
        for listing in listings:
            self.pending_listings[listing.remote_id] = listing
            self.pending_warehouses[listing.remote_id] = set()
        for wid in self.warehouse_ids:
            skus = [sku for sku in needed_by_wh.get(wid, ()) if sku in built_ids]
            if skus:
                self.queues[wid].extend(skus)
                for sku in skus:
                    self.pending_warehouses[sku].add(wid)
        return self.flush(flush_all=False)

    def _run_chunks(self, chunks: list[tuple[str, list[str]]]) -> None:
        if not chunks:
            return
        calls = {}
        chunk_meta: dict[str, tuple[str, list[str]]] = {}
        for idx, (wid, sku_chunk) in enumerate(chunks):
            key = f"stock:{wid}:{idx}"
            chunk_meta[key] = (wid, sku_chunk)
            calls[key] = lambda wid=wid, sku_chunk=sku_chunk: stock_result(
                self.adapter, f"/v3/businesses/{self.adapter.business}/offers/stocks",
                {"partnerWarehouseId": int(wid), "offerIds": sku_chunk},
            )
        responses, errors = request_results(calls)
        for key, (wid, sku_chunk) in chunk_meta.items():
            if key in errors:
                err_msg = errors[key]
                for sku in sku_chunk:
                    listing = self.pending_listings.get(sku)
                    if listing is not None:
                        stock = next((s for s in listing.stocks if s.id == wid), None)
                        if stock is not None:
                            stock.read_error = err_msg
                    self.pending_warehouses.get(sku, set()).discard(wid)
                continue
            offers_by_sku = {row["offerId"]: row for row in responses[key]["offers"]}
            for sku in sku_chunk:
                listing = self.pending_listings.get(sku)
                if listing is not None:
                    stock = next((s for s in listing.stocks if s.id == wid), None)
                    if stock is not None:
                        stock.quantity = available_stock(offers_by_sku.get(sku, {}).get("stocks", []))
                        if stock.quantity is None and stock.raw_status == "PUBLISHED":
                            stock.read_error = "平台未返回可确认的可售库存"
                self.pending_warehouses.get(sku, set()).discard(wid)

    def _pop_ready(self) -> list[OnlineListing]:
        ready_ids = [sku for sku, waiting in self.pending_warehouses.items() if not waiting]
        ready = []
        for sku in ready_ids:
            self.pending_warehouses.pop(sku, None)
            listing = self.pending_listings.pop(sku, None)
            if listing is not None:
                ready.append(listing)
        return ready

    def flush(self, *, flush_all: bool) -> list[OnlineListing]:
        chunks: list[tuple[str, list[str]]] = []
        for wid in self.warehouse_ids:
            queue = self.queues[wid]
            while len(queue) >= STOCK_BATCH_SIZE:
                chunks.append((wid, queue[:STOCK_BATCH_SIZE]))
                del queue[:STOCK_BATCH_SIZE]
            if flush_all and queue:
                chunks.append((wid, list(queue)))
                queue.clear()
        self._run_chunks(chunks)
        ready = self._pop_ready()
        # 多仓场景下的内存保护：待回填刊登超过上限时提前刷新当前队列最长的仓库。
        while not flush_all and len(self.pending_listings) > MAX_PENDING_STOCK_LISTINGS:
            wid = max(self.warehouse_ids, key=lambda key: len(self.queues[key]), default="")
            if not wid or not self.queues[wid]:
                break
            batch = self.queues[wid][:STOCK_BATCH_SIZE]
            del self.queues[wid][:STOCK_BATCH_SIZE]
            self._run_chunks([(wid, batch)])
            ready.extend(self._pop_ready())
        return ready


def list_prices(adapter, mappings, campaign_ids, responses):
    """复用同批目录与店铺响应，只为缺少账号基础价的 SKU 发起补查。"""
    prices, missing = {}, []
    for mapping in mappings:
        sku = mapping["offer"]["offerId"]
        offers = [mapping["offer"]]
        offers.extend(responses.get("campaign:" + cid, {}).get(sku, {}) for cid in sorted(campaign_ids))
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


def read_batch(
    adapter,
    mappings,
    hidden,
    *,
    scope: Literal["details", "list", "sales", "status"] = "details",
    stock_accumulator: _BusinessStockAccumulator | None = None,
) -> OnlineSyncBatch:
    """同批复用店铺商品响应；页面销售刷新跳过目录、卡片、隐藏清单和属性。"""
    full = scope == "details"
    ids = [row["offer"]["offerId"] for row in mappings]

    def rows(path, key):
        collected = []
        for offset in range(0, len(ids), DETAIL_BATCH_SIZE):
            chunk = ids[offset:offset + DETAIL_BATCH_SIZE]
            collected.extend(row for page in pages(adapter, path, key, {"offerIds": chunk}) for row in page)
        return index_rows(collected, allowed=set(ids))

    warehouses = adapter.warehouses if adapter.mode == 'business' else []
    campaign_ids = {str(model['campaignId']) for warehouse in warehouses for model in warehouse.get('models', [])
                    if model.get('placementType') == 'FBS' and model.get('campaignId')} | {adapter.campaign}
    hidden = hidden if isinstance(hidden, dict) else {adapter.campaign: hidden}
    defer_business_stocks = scope in ("list", "sales") and adapter.mode == "business" and bool(warehouses)
    requests = {}
    if scope != "sales":
        requests["cards"] = lambda: rows(f"/v2/businesses/{adapter.business}/offer-cards", "offerCards")
    for campaign_id in sorted(campaign_ids):
        requests['campaign:' + campaign_id] = lambda cid=campaign_id: rows(f'/v2/campaigns/{cid}/offers', 'offers')
        if scope != "sales" and campaign_id not in hidden:
            requests['hidden:' + campaign_id] = lambda cid=campaign_id: hidden_ids(adapter, ids[0] if len(ids) == 1 else '', campaign_id=cid)
    if scope != "status" and not defer_business_stocks:
        requests.update(stock_requests(adapter, ids))
    if full:
        requests["prices"] = lambda: account_prices(adapter, ids)
    responses, errors = request_results(requests)
    result = OnlineSyncBatch("details" if full else "basic")
    failed_campaigns = {key.removeprefix("campaign:") for key in errors if key.startswith("campaign:")}
    all_campaigns_failed = bool(campaign_ids) and len(failed_campaigns) >= len(campaign_ids)
    if full or scope == "status":
        blocking = list(errors.values())
    else:
        blocking = [
            error for key, error in errors.items()
            if not key.startswith("stock:") and (not key.startswith("campaign:") or all_campaigns_failed)
        ]
    if blocking:
        result.errors = {remote_id: "；".join(blocking) for remote_id in ids}
        return result
    needed_stocks: dict[str, list[str]] = {}
    if defer_business_stocks:
        if stock_accumulator is not None:
            needed_stocks = _needed_warehouse_skus(warehouses, ids, responses, skip_no_stocks=True)
        else:
            # 按店铺状态过滤确定 NO_STOCKS 或未上架该店的 SKU，避免对未铺货仓库发无效库存查询。
            stock_calls = _warehouse_stock_requests(adapter, warehouses, ids, responses, skip_no_stocks=True)
            if stock_calls:
                stock_res, stock_err = request_results(stock_calls)
                responses.update(stock_res)
                errors.update(stock_err)
    prices = {sku: row.get("price") or {} for sku, row in responses.get("prices", {}).items()}
    if scope in ("list", "sales"):
        prices, price_errors = list_prices(adapter, mappings, campaign_ids, responses)
        errors.update(price_errors)
    stocks = {name.removeprefix("stock:"): value for name, value in responses.items() if name.startswith("stock:")}
    hidden = {**hidden, **{name.removeprefix('hidden:'): value for name, value in responses.items() if name.startswith('hidden:')}}
    # 列表只展示平台返回的币种与金额，只有管理详情需要账号级修改设置。
    settings = adapter.settings if full else {}
    deferred_stock_fill = defer_business_stocks and stock_accumulator is not None
    built_listings: list[OnlineListing] = []
    for mapping in mappings:
        remote_id = mapping["offer"]["offerId"]
        try:
            campaigns = {cid: responses.get('campaign:' + cid, {}).get(remote_id, {}) for cid in campaign_ids}
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
                        if ("campaign:" + cid) in errors:
                            price.read_error = errors["campaign:" + cid]
                        elif not campaigns.get(cid) and price.writable:
                            price.read_error = "平台未返回该 SKU 的店铺商品记录"
                if not deferred_stock_fill:
                    for stock in listing.stocks:
                        if stock.quantity is None:
                            if ("stock:" + stock.id) in errors:
                                stock.read_error = errors["stock:" + stock.id]
                            else:
                                # Yandex 对 NO_STOCKS / 未铺货或非在售仓正常不返回可售数，不判为同步失败；仅在售仓缺失可售数时报错。
                                stock_status = stock.raw_status or (listing.raw_status if stock.id == "campaign" else "")
                                if stock_status == "PUBLISHED":
                                    stock.read_error = "平台未返回可确认的可售库存"
            built_listings.append(listing)
        except Exception as exc:
            result.errors[remote_id] = str(exc)
    if deferred_stock_fill:
        result.listings = stock_accumulator.add(built_listings, needed_stocks)
    else:
        result.listings = built_listings
    return result


def sync_yandex(adapter, ids=None, *, catalog="current", full=False) -> Iterator[OnlineSyncBatch]:
    if catalog not in ("current", "archived") and not (catalog is None and ids is not None):
        raise ValueError("同步目录只能为 current 或 archived")
    discovered, hidden = set(), None
    phase = "details" if full else "basic"
    scope: Literal["details", "list"] = "details" if full else "list"
    stock_acc: _BusinessStockAccumulator | None = None
    buffered: list[dict[str, Any]] = []
    page_iter = iter(catalog_pages(adapter, ids, catalog=catalog))

    def ensure_hidden():
        nonlocal hidden, stock_acc
        if hidden is not None:
            return
        warehouses = adapter.warehouses if adapter.mode == "business" else []
        campaigns = {str(model["campaignId"]) for warehouse in warehouses
                     for model in warehouse.get("models", []) if model.get("placementType") == "FBS" and model.get("campaignId")} | {adapter.campaign}
        hidden = _all_hidden_ids(adapter, campaigns)
        if scope == "list" and adapter.mode == "business" and warehouses:
            stock_acc = _BusinessStockAccumulator(adapter, warehouses)

    def flush_pending() -> Iterator[OnlineSyncBatch]:
        nonlocal buffered
        if buffered:
            ensure_hidden()
        while buffered:
            chunk, buffered = buffered[:DETAIL_BATCH_SIZE], buffered[DETAIL_BATCH_SIZE:]
            batch = read_batch(adapter, chunk, hidden, scope=scope, stock_accumulator=stock_acc)
            if batch.listings or batch.errors:
                yield batch
        if stock_acc is not None:
            remaining = stock_acc.flush(flush_all=True)
            if remaining:
                yield OnlineSyncBatch(phase, listings=remaining)

    page = next(page_iter, None)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="yandex-sync-catalog") as prefetch_pool:
        while page is not None:
            discovered.update(row["offer"]["offerId"] for row in page)
            yield OnlineSyncBatch("catalog", [catalog_listing(row, account_id=adapter.account_id,
                campaign_id=adapter.campaign) for row in page])
            if page:
                buffered.extend(page)
            if len(buffered) >= DETAIL_BATCH_SIZE:
                ensure_hidden()
                next_future = None
                try:
                    while len(buffered) >= DETAIL_BATCH_SIZE:
                        chunk, buffered = buffered[:DETAIL_BATCH_SIZE], buffered[DETAIL_BATCH_SIZE:]
                        # 最后一片详情处理时并行预取下一页目录，压缩长耗时翻页等待。
                        if next_future is None and len(buffered) < DETAIL_BATCH_SIZE:
                            next_future = prefetch_pool.submit(copy_context().run, next, page_iter, None)
                        batch = read_batch(adapter, chunk, hidden, scope=scope, stock_accumulator=stock_acc)
                        if batch.listings or batch.errors:
                            yield batch
                except BaseException:
                    if next_future is not None:
                        next_future.cancel()
                    raise
                try:
                    page = next_future.result() if next_future is not None else next(page_iter, None)
                except Exception as exc:
                    raise_if_access_blocked(exc)
                    yield from flush_pending()
                    raise
            elif buffered:
                # 仍在攒满 200 SKU 时先预取下一页目录，并顺带并发初始化隐藏清单。
                next_future = prefetch_pool.submit(copy_context().run, next, page_iter, None)
                try:
                    ensure_hidden()
                except BaseException:
                    next_future.cancel()
                    raise
                try:
                    page = next_future.result()
                except Exception as exc:
                    raise_if_access_blocked(exc)
                    yield from flush_pending()
                    raise
            else:
                try:
                    page = next(page_iter, None)
                except Exception as exc:
                    raise_if_access_blocked(exc)
                    yield from flush_pending()
                    raise
    yield from flush_pending()
    yield OnlineSyncBatch(phase, discovery_complete=True)
    if ids is not None:
        missing = set(ids) - discovered
        if missing:
            yield OnlineSyncBatch(phase, errors={sku: "Yandex 未返回该 SKU 的所选目录记录，商品可能已移出该目录" for sku in sorted(missing)})


def _fetch_campaign_offers(adapter, campaign_id: str, chunk: list[str]) -> dict[str, dict[str, Any]]:
    collected = [
        row
        for page in pages(adapter, f"/v2/campaigns/{campaign_id}/offers", "offers", {"offerIds": chunk})
        for row in page
    ]
    return index_rows(collected, allowed=set(chunk))


def sync_yandex_hot(adapter, remote_ids: list[str]) -> Iterator[OnlineSyncBatch]:
    """热数据快刷：扁平并发刷新多仓店铺与隐藏清单，并在首个分片返回后流水线并发拉取仓库库存与合并价格。"""
    targets = list(dict.fromkeys(sku for sku in remote_ids if sku))
    if not targets:
        return
    warehouses = adapter.warehouses if adapter.mode == "business" else []
    campaigns = sorted({
        str(model["campaignId"])
        for warehouse in warehouses
        for model in warehouse.get("models", [])
        if model.get("placementType") == "FBS" and model.get("campaignId")
    } | {adapter.campaign})
    is_business = adapter.mode == "business" and bool(warehouses)
    chunks = [targets[i:i + DETAIL_BATCH_SIZE] for i in range(0, len(targets), DETAIL_BATCH_SIZE)]
    hidden: dict[str, set[str]] = {}
    single_target = targets[0] if len(targets) == 1 else ""

    for win_start in range(0, len(chunks), HOT_WINDOW_CHUNKS):
        window_chunks = chunks[win_start:win_start + HOT_WINDOW_CHUNKS]
        need_hidden = not hidden
        stopped = Event()
        campaign_workers = min(HOT_MAX_CAMPAIGN_WORKERS, max(MAX_REQUESTS, len(campaigns) * 2))

        def run_guarded(fn):
            if stopped.is_set():
                raise RuntimeError("本批次已因访问受阻而停止")
            try:
                return fn()
            except Exception as exc:
                try:
                    raise_if_access_blocked(exc)
                except Exception:
                    stopped.set()
                    raise
                raise

        chunk_campaigns: dict[int, dict[str, dict[str, Any]]] = {idx: {} for idx in range(len(window_chunks))}
        chunk_campaign_errors: dict[int, dict[str, str]] = {idx: {} for idx in range(len(window_chunks))}
        chunk_remaining: dict[int, int] = {idx: len(campaigns) for idx in range(len(window_chunks))}
        hidden_errors: dict[str, str] = {}
        stock_queues: dict[str, list[str]] = {str(w["id"]): [] for w in warehouses}
        stock_offers: dict[str, dict[str, dict[str, Any]]] = {str(w["id"]): {} for w in warehouses}
        stock_errors: dict[str, dict[str, str]] = {str(w["id"]): {} for w in warehouses}
        non_business_by_wh: dict[Any, dict[str, Any]] = {}
        non_business_stock_error = ""
        prices: dict[str, dict[str, Any]] = {}
        prices_error = ""

        with (
            ThreadPoolExecutor(max_workers=campaign_workers, thread_name_prefix="yandex-hot-camp") as camp_pool,
            ThreadPoolExecutor(max_workers=MAX_REQUESTS, thread_name_prefix="yandex-hot-biz") as biz_pool,
        ):
            camp_futures = {}
            biz_futures = {}

            def cancel_all():
                stopped.set()
                for fut in (*camp_futures, *biz_futures):
                    fut.cancel()

            def submit_business_stock(wid: str, sku_chunk: list[str]) -> None:
                fut = biz_pool.submit(
                    copy_context().run, run_guarded,
                    lambda wid=wid, sku_chunk=sku_chunk: stock_result(
                        adapter, f"/v3/businesses/{adapter.business}/offers/stocks",
                        {"partnerWarehouseId": int(wid), "offerIds": sku_chunk},
                    ),
                )
                biz_futures[fut] = ("stock", wid, sku_chunk)

            # 按分片优先提交店铺查询，并同池并发隐藏清单（避免 4 仓撞 3 并发二波及串行批次屏障）。
            for idx, chunk in enumerate(window_chunks):
                for cid in campaigns:
                    fut = camp_pool.submit(
                        copy_context().run, run_guarded,
                        lambda cid=cid, chunk=chunk: _fetch_campaign_offers(adapter, cid, chunk),
                    )
                    camp_futures[fut] = ("campaign", idx, cid)
                if need_hidden and idx == 0:
                    for cid in campaigns:
                        fut = camp_pool.submit(
                            copy_context().run, run_guarded,
                            lambda cid=cid: hidden_ids(adapter, single_target, campaign_id=cid),
                        )
                        camp_futures[fut] = ("hidden", cid)

            if not is_business and adapter.mode in ("campaign_warehouses", "platform_warehouses"):
                window_ids = [sku for chunk in window_chunks for sku in chunk]
                for offset in range(0, len(window_ids), STOCK_BATCH_SIZE):
                    sku_chunk = window_ids[offset:offset + STOCK_BATCH_SIZE]
                    fut = biz_pool.submit(
                        copy_context().run, run_guarded,
                        lambda sku_chunk=sku_chunk: stock_result(
                            adapter, f"/v2/campaigns/{adapter.campaign}/offers/stocks", {"offerIds": sku_chunk},
                        ),
                    )
                    biz_futures[fut] = ("non_business_stock", sku_chunk)

            try:
                for fut in as_completed(camp_futures):
                    meta = camp_futures[fut]
                    try:
                        value = fut.result()
                    except Exception as exc:
                        if stopped.is_set():
                            cancel_all()
                        raise_if_access_blocked(exc)
                        if meta[0] == "hidden":
                            hidden_errors[meta[1]] = f"店铺隐藏状态：{exc}"
                        else:
                            _, idx, cid = meta
                            chunk_campaign_errors[idx][cid] = f"店铺状态：{exc}"
                            chunk_remaining[idx] -= 1
                        continue
                    if meta[0] == "hidden":
                        hidden[meta[1]] = value
                        continue
                    _, idx, cid = meta
                    chunk_campaigns[idx][cid] = value
                    chunk_remaining[idx] -= 1
                    if chunk_remaining[idx] == 0 and is_business and len(chunk_campaign_errors[idx]) < len(campaigns):
                        chunk_responses = {"campaign:" + k: v for k, v in chunk_campaigns[idx].items()}
                        needed = _needed_warehouse_skus(warehouses, window_chunks[idx], chunk_responses, skip_no_stocks=True)
                        for wid, skus in needed.items():
                            queue = stock_queues[wid]
                            queue.extend(skus)
                            while len(queue) >= STOCK_BATCH_SIZE:
                                batch_skus = queue[:STOCK_BATCH_SIZE]
                                del queue[:STOCK_BATCH_SIZE]
                                submit_business_stock(wid, batch_skus)

                if hidden_errors:
                    cancel_all()
                    raise RuntimeError("；".join(hidden_errors.values()))

                # 尾批库存与合并后的缺失基础价查询同时投递到 business 池，单波收尾。
                if is_business:
                    for wid, queue in stock_queues.items():
                        while queue:
                            batch_skus = queue[:STOCK_BATCH_SIZE]
                            del queue[:STOCK_BATCH_SIZE]
                            submit_business_stock(wid, batch_skus)

                missing_prices: list[str] = []
                for idx, chunk in enumerate(window_chunks):
                    if len(chunk_campaign_errors[idx]) >= len(campaigns):
                        continue
                    for sku in chunk:
                        price = next(
                            (
                                offer["basicPrice"]
                                for cid in campaigns
                                if ((offer := chunk_campaigns[idx].get(cid, {}).get(sku, {})).get("basicPrice") or {}).get("value") is not None
                            ),
                            None,
                        )
                        if price is not None:
                            prices[sku] = price
                        else:
                            missing_prices.append(sku)

                if missing_prices:
                    fut = biz_pool.submit(
                        copy_context().run, run_guarded,
                        lambda missing_prices=missing_prices: account_prices(adapter, missing_prices),
                    )
                    biz_futures[fut] = ("prices", missing_prices)

                for fut in as_completed(biz_futures):
                    meta = biz_futures[fut]
                    try:
                        value = fut.result()
                    except Exception as exc:
                        if stopped.is_set():
                            cancel_all()
                        raise_if_access_blocked(exc)
                        if meta[0] == "stock":
                            _, wid, sku_chunk = meta
                            err_msg = f"库存：{exc}"
                            for sku in sku_chunk:
                                stock_errors[wid][sku] = err_msg
                        elif meta[0] == "non_business_stock":
                            non_business_stock_error = f"库存：{exc}"
                        else:
                            prices_error = f"价格：{exc}"
                        continue
                    if meta[0] == "stock":
                        _, wid, _ = meta
                        for row in value["offers"]:
                            stock_offers[wid][row["offerId"]] = row
                    elif meta[0] == "non_business_stock":
                        for wh in value["warehouses"]:
                            wid = wh["warehouseId"]
                            if wid not in non_business_by_wh:
                                non_business_by_wh[wid] = {**wh, "offers": list(wh["offers"])}
                            else:
                                non_business_by_wh[wid]["offers"].extend(wh["offers"])
                    else:
                        for sku, row in value.items():
                            prices[sku] = row.get("price") or {}
            except BaseException:
                cancel_all()
                raise

        non_business_stocks = {"campaign": {"warehouses": list(non_business_by_wh.values())}} if non_business_by_wh else {}
        built_listings: list[OnlineListing] = []
        batch_errors: dict[str, str] = {}
        missing_candidates: list[str] = []

        for idx, chunk in enumerate(window_chunks):
            camp_errs = chunk_campaign_errors[idx]
            if len(camp_errs) >= len(campaigns):
                blocking = "；".join(camp_errs.values())
                for sku in chunk:
                    batch_errors[sku] = blocking
                continue
            for remote_id in chunk:
                try:
                    sku_campaigns = {cid: chunk_campaigns[idx].get(cid, {}).get(remote_id, {}) for cid in campaigns}
                    if is_business:
                        stocks_by_wh = {
                            wid: {
                                "partnerWarehouseId": wid,
                                "offers": [stock_offers[wid][remote_id]] if remote_id in stock_offers[wid] else [],
                            }
                            for wid in stock_offers
                        }
                    else:
                        stocks_by_wh = non_business_stocks
                    listing = build_listing(
                        {"offer": {"offerId": remote_id}},
                        sku_campaigns[adapter.campaign],
                        remote_id in hidden.get(adapter.campaign, set()),
                        {},
                        prices.get(remote_id, {}),
                        build_stocks(adapter.mode, warehouses, stocks_by_wh, remote_id),
                        account_id=adapter.account_id,
                        campaign_id=adapter.campaign,
                        settings={},
                    )
                    with_warehouses(listing, warehouses, sku_campaigns, hidden, settings={})
                    listing.details_state = "unloaded"
                    for operation in ("price", "stock", "content"):
                        listing.capabilities[operation] = Capability(reason="打开商品详情后核验当前资料")
                    for price in listing.prices:
                        if price.id == "business" and remote_id not in prices:
                            price.read_error = prices_error or "平台未返回该 SKU 的账号基础价"
                        elif price.id != "business":
                            cid = adapter.campaign if price.id == "campaign" else price.id.removeprefix("campaign:")
                            if cid in camp_errs:
                                price.read_error = camp_errs[cid]
                            elif not sku_campaigns.get(cid) and price.writable:
                                price.read_error = "平台未返回该 SKU 的店铺商品记录"
                    for stock in listing.stocks:
                        if stock.quantity is None:
                            wh_err = stock_errors.get(stock.id, {}).get(remote_id) or (non_business_stock_error if stock.id == "campaign" else "")
                            if wh_err:
                                stock.read_error = wh_err
                            else:
                                stock_status = stock.raw_status or (listing.raw_status if stock.id == "campaign" else "")
                                if stock_status == "PUBLISHED":
                                    stock.read_error = "平台未返回可确认的可售库存"
                    if not camp_errs and not prices_error and not any(sku_campaigns.values()) and remote_id not in prices:
                        missing_candidates.append(remote_id)
                    built_listings.append(listing)
                except Exception as exc:
                    batch_errors[remote_id] = str(exc)

        if built_listings or batch_errors or missing_candidates:
            yield OnlineSyncBatch("hot", listings=built_listings, errors=batch_errors, missing_candidates=missing_candidates)
