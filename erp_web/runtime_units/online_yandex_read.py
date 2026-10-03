"""Yandex 只读同步：目录分页先行，详情按 100 个 SKU 分批，最多 3 个接口并发。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from threading import Event

from erp_web.marketplaces.online_sync import raise_if_access_blocked
from typing import Any, Iterator

from erp_web.runtime_units.online_yandex_snapshot import build_listing, build_stocks, catalog_listing
from erp_web.schemas.online_products import OnlineSyncBatch

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


def catalog_pages(adapter, ids: list[str] | None = None) -> Iterator[list[dict[str, Any]]]:
    path = f"/v2/businesses/{adapter.business}/offer-mappings"
    seen = set()
    bodies = [{"offerIds": ids[i:i + BATCH_SIZE]} for i in range(0, len(ids), BATCH_SIZE)] if ids is not None else [{"archived": False}, {"archived": True}]
    for body in bodies:
        for rows in pages(adapter, path, "offerMappings", body, query=None if ids is not None else {"limit": BATCH_SIZE}):
            indexed = index_rows(rows, allowed=set(ids) if ids is not None else None, nested=True)
            fresh = [row for key, row in indexed.items() if key not in seen]
            seen.update(indexed)
            yield fresh


def hidden_ids(adapter, remote_id: str = "") -> set[str]:
    result = set()
    query = {"offer_id": remote_id} if remote_id else {"limit": 500}
    for rows in pages(adapter, f"/v2/campaigns/{adapter.campaign}/hidden-offers", "hiddenOffers", query=query, method="GET"):
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
    return result


def read_batch(adapter, mappings, hidden: set[str]) -> OnlineSyncBatch:
    """同一批接口并发，批与批顺序执行；失败不退化成逐 SKU 请求风暴。"""
    ids = [row["offer"]["offerId"] for row in mappings]
    allowed = set(ids)

    def rows(path, key, body):
        return index_rows([row for page in pages(adapter, path, key, body) for row in page], allowed=allowed)

    requests = {
        "campaign": lambda: rows(f"/v2/campaigns/{adapter.campaign}/offers", "offers", {"offerIds": ids}),
        "cards": lambda: rows(f"/v2/businesses/{adapter.business}/offer-cards", "offerCards", {"offerIds": ids}),
        "prices": lambda: rows(f"/v2/businesses/{adapter.business}/offer-prices", "offers", {"offerIds": ids}),
    }
    if adapter.mode == "business":
        for warehouse in adapter.warehouses:
            wid = str(warehouse["id"])
            requests["stock:" + wid] = lambda wid=wid: stock_result(adapter,
                f"/v3/businesses/{adapter.business}/offers/stocks", {"partnerWarehouseId": int(wid), "offerIds": ids})
    elif adapter.mode == "campaign_warehouses":
        requests["stock:campaign"] = lambda: stock_result(adapter,
            f"/v2/campaigns/{adapter.campaign}/offers/stocks", {"offerIds": ids})
    responses, errors = {}, []
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
                label = {"campaign": "店铺状态", "cards": "商品卡片", "prices": "价格"}.get(name, "库存")
                errors.append(f"{label}：{exc}")
    result = OnlineSyncBatch("details")
    if errors:
        result.errors = {remote_id: "；".join(errors) for remote_id in ids}
        return result
    stocks = {name.removeprefix("stock:"): value for name, value in responses.items() if name.startswith("stock:")}
    for mapping in mappings:
        remote_id = mapping["offer"]["offerId"]
        try:
            if remote_id not in responses["cards"]:
                raise ValueError("平台未返回该商品的卡片信息，已保留原记录")
            card = responses["cards"][remote_id]
            names, names_error = {}, ""
            if card.get("parameterValues"):
                category_id = str(mapping["offer"].get("marketCategoryId") or
                                  card.get("mapping", {}).get("marketCategoryId") or
                                  mapping.get("mapping", {}).get("marketCategoryId") or "")
                if category_id:
                    names, names_error = adapter.attribute_names(category_id)
                else:
                    names_error = "平台未返回类目编号，暂时无法关联属性名称"
            result.listings.append(build_listing(mapping, responses["campaign"].get(remote_id, {}), remote_id in hidden,
                card, responses["prices"].get(remote_id, {}).get("price", {}),
                build_stocks(adapter.mode, adapter.warehouses, stocks, remote_id),
                account_id=adapter.account_id, campaign_id=adapter.campaign, settings=adapter.settings,
                attribute_names=names, attribute_names_error=names_error))
        except Exception as exc:
            result.errors[remote_id] = str(exc)
    return result


def sync_yandex(adapter, ids=None) -> Iterator[OnlineSyncBatch]:
    mappings = []
    for page in catalog_pages(adapter, ids):
        mappings.extend(page)
        yield OnlineSyncBatch("catalog", [catalog_listing(row, account_id=adapter.account_id,
            campaign_id=adapter.campaign, settings=adapter.settings) for row in page])
    # 阶段标记使空目录也能正确完成，并在首批详情网络请求之前持久化进度。
    yield OnlineSyncBatch("details", discovery_complete=True)
    if ids is not None:
        missing = set(ids) - {row["offer"]["offerId"] for row in mappings}
        if missing:
            yield OnlineSyncBatch("details", errors={sku: "Yandex 未返回该 SKU 的目录记录" for sku in sorted(missing)})
    if not mappings:
        return
    try:
        hidden = hidden_ids(adapter)
    except Exception as exc:
        raise_if_access_blocked(exc)
        yield OnlineSyncBatch("details", errors={row["offer"]["offerId"]: str(exc) for row in mappings})
        return
    for offset in range(0, len(mappings), BATCH_SIZE):
        yield read_batch(adapter, mappings[offset:offset + BATCH_SIZE], hidden)
